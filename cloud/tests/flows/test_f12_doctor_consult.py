"""F12: an unresolved incident becomes a Fleet Doctor consult, an offered fix, an overlay.

The device side is driven through the real routes with `client` (incident, check-in,
proposal pull, result, overlay status); the Doctor runs as the worker would, one
`doctor.tick` per call through `run_tick` (each tick commits), with `InlineJobs` and
`FakeLlm` injected. Nothing calls a model. The happy path opts in with
`doctor_mode="fake"`, `doctor_offer_stub_checked=True` and `safety_paths_confirmed=True`;
the defaults do nothing (mode off) or never offer (stub check, unconfirmed safety paths).
"""

import hashlib
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Any

import pytest
from sqlalchemy import func, select

from romulus_cloud.adapters.llm import FakeLlm, LlmError
from romulus_cloud.domain import doctor as rules
from romulus_cloud.models import DoctorConsult, DoctorProposal, Event, Overlay
from romulus_cloud.services import doctor
from romulus_cloud.services.doctor_jobs import Done, InlineJobs, Missing, Pending
from romulus_cloud.worker import Step, run_tick
from romulus_contracts import SHA256_HEADER, Proposal

T0 = datetime(2026, 11, 7, 22, 0, tzinfo=UTC)
ON = {"doctor_mode": "fake", "doctor_offer_stub_checked": True, "safety_paths_confirmed": True}
TICKS_TO_OFFER = 6  # open + admit, gather, diagnose, draft, check, offer
SAFETY_PATCH = "\n".join(
    [
        "diff --git a/safety/limits.py b/safety/limits.py",
        "--- a/safety/limits.py",
        "+++ b/safety/limits.py",
        "@@ -1,1 +1,1 @@",
        "-MAX_TORQUE = 0.5",
        "+MAX_TORQUE = 0.9",
        "",
    ]
)
ESCAPING_PATCH = SAFETY_PATCH.replace("safety/limits.py", "../outside.py")


class ManualJobs:
    """Jobs that stay pending until `release()`; `lose()` forgets everything, as a worker
    restart does (every key then polls `Missing`)."""

    def __init__(self) -> None:
        self.pending: dict[str, Callable[[], Any]] = {}
        self.done: dict[str, Any] = {}
        self.calls: list[str] = []

    def submit(self, key, fn):
        if key in self.pending or key in self.done:
            return
        self.calls.append(key)
        self.pending[key] = fn

    def poll(self, key):
        if key in self.done:
            return self.done[key]
        return Pending() if key in self.pending else Missing()

    def forget(self, key):
        self.pending.pop(key, None)
        self.done.pop(key, None)

    def release(self):
        for key, fn in list(self.pending.items()):
            self.done[key] = Done(fn())
            del self.pending[key]

    def lose(self):
        self.pending.clear()
        self.done.clear()


class Doctor:
    """The worker's `doctor.tick`, with test settings, jobs and LLM injected."""

    def __init__(self, sessions, clock, settings, jobs=None, llm=None) -> None:
        self.sessions, self.clock, self.settings = sessions, clock, settings
        self.jobs = jobs if jobs is not None else InlineJobs()
        self.llm = llm if llm is not None else FakeLlm()

    def tick(self, t: datetime) -> datetime:
        self.clock.at(t)
        fn = partial(doctor.tick, settings=self.settings, jobs=self.jobs, llm=self.llm)
        results = run_tick([Step("doctor.tick", fn)], self.sessions, t, {})
        assert results == {"doctor.tick": "ok"}, results
        return t + timedelta(seconds=5)

    def run(self, t: datetime, ticks: int) -> datetime:
        for _ in range(ticks):
            t = self.tick(t)
        return t


@pytest.fixture
def make_doctor(sessions, clock, make_settings, seeded) -> Callable[..., Doctor]:
    def make(*, jobs=None, llm=None, **overrides) -> Doctor:
        return Doctor(sessions, clock, make_settings(**(ON | overrides)), jobs=jobs, llm=llm)

    return make


def _report(client, example, *, name="incident_consult_wanted", device="arm-1", **changes):
    body = example(name) | {
        "incident_id": str(uuid.uuid4()),
        "occurred_at": (T0 - timedelta(minutes=1)).isoformat(),
        **changes,
    }
    resp = client.post(f"/v1/devices/{device}/incidents", json=body)
    assert resp.status_code == 201, resp.text
    return body["incident_id"]


def _checkin(client, example, device="arm-1") -> dict:
    resp = client.post(f"/v1/devices/{device}/checkin", json=example("checkin"))
    assert resp.status_code == 200, resp.text
    return resp.json()


def _consults(db_session, incident_id) -> list[DoctorConsult]:
    db_session.expire_all()
    stmt = select(DoctorConsult).where(DoctorConsult.incident_id == incident_id)
    return list(db_session.scalars(stmt.order_by(DoctorConsult.attempt)))


def _proposal(db_session, proposal_id) -> DoctorProposal:
    db_session.expire_all()
    p = db_session.get(DoctorProposal, proposal_id)
    assert p is not None
    return p


def _overlay(db_session, overlay_id, device="arm-1") -> Overlay | None:
    db_session.expire_all()
    return db_session.get(Overlay, (device, overlay_id))


def _doctor_events(db_session, incident_id) -> list[Event]:
    db_session.expire_all()
    stmt = select(Event).where(Event.refs.contains({"incident": incident_id}))
    stmt = stmt.where(Event.kind.like("doctor.%"))
    return list(db_session.scalars(stmt.order_by(Event.ts, Event.recorded_at)))


def _event_count(db_session) -> int:
    db_session.expire_all()
    return db_session.scalar(select(func.count()).select_from(Event)) or 0


def _result(client, proposal_id, result, *, overlay_id=None, at=T0, device="arm-1", **extra):
    body = {"result": result, "reason": f"test: {result}", "occurred_at": at.isoformat()}
    if overlay_id:
        body["overlay_id"] = overlay_id
    return client.post(f"/v1/devices/{device}/proposals/{proposal_id}/result", json=body | extra)


def _overlay_status(client, overlay_id, status, at, device="arm-1"):
    body = {"status": status, "reason": f"test: {status}", "occurred_at": at.isoformat()}
    resp = client.post(f"/v1/devices/{device}/overlays/{overlay_id}", json=body)
    assert resp.status_code == 204, resp.text


def _offered(doc, client, example, clock, db_session, **report) -> tuple[str, str, datetime]:
    """Report a consult incident and tick until its fix is offered → (incident, proposal, t)."""
    clock.at(T0)
    incident_id = _report(client, example, **report)
    t = doc.run(T0 + timedelta(seconds=2), TICKS_TO_OFFER)
    consult = _consults(db_session, incident_id)[-1]
    assert (consult.stage, consult.status) == ("applied", "waiting"), consult.outcome
    return incident_id, consult.raw["proposal_id"], t


# --- the happy path -------------------------------------------------------------------------


def test_a_consult_offers_a_fix_the_device_pulls_applies_and_holds(
    client, example, make_doctor, db_session, clock, count_events
):
    doc = make_doctor()
    clock.at(T0)
    assert _checkin(client, example)["proposal"] is None
    incident_id = _report(client, example)

    t = doc.tick(T0 + timedelta(seconds=2))  # open + admit
    (consult,) = _consults(db_session, incident_id)
    assert (consult.origin, consult.trigger, consult.attempt) == ("device", "T2", 1)
    assert consult.stage == "gather"
    t = doc.run(t, TICKS_TO_OFFER - 1)
    (consult,) = _consults(db_session, incident_id)
    assert (consult.stage, consult.status, consult.outcome) == ("applied", "waiting", None)
    case = consult.case_file
    assert case["untrusted"] is True and case["notes"] == ["trace, code, mcap: not wired"]
    assert case["log_tail"] == example("incident_consult_wanted")["log_tail"]
    assert case["code"]["rev"] == "c0de001"
    assert consult.ledger and consult.cost_usd > 0
    # One LLM call, off the tick, and its result is dropped once applied and committed.
    assert doc.jobs.calls == [f"diagnose:{consult.id}"]
    assert isinstance(doc.jobs.poll(f"diagnose:{consult.id}"), Missing)

    # The check-in points at the fix and still writes no event (routine).
    before = _event_count(db_session)
    pointer = _checkin(client, example)["proposal"]
    assert _event_count(db_session) == before
    assert pointer["incident_id"] == incident_id
    assert pointer["proposal_id"] == consult.raw["proposal_id"]

    # The pull: the header and the pointer both hash the exact bytes served.
    resp = client.get(f"/v1/devices/arm-1/proposals/{pointer['proposal_id']}")
    assert resp.status_code == 200, resp.text
    sha = hashlib.sha256(resp.content).hexdigest()
    assert resp.headers[SHA256_HEADER] == sha == pointer["sha256"]
    assert client.get(f"/v1/devices/arm-1/proposals/{pointer['proposal_id']}").content == (
        resp.content
    )
    proposal = Proposal.model_validate_json(resp.content)
    assert str(proposal.incident_id) == incident_id
    assert (proposal.base.firmware, proposal.base.harness) == (1, 1)
    assert (proposal.base.code_rev, proposal.base.overlay_id) == ("c0de001", None)
    assert proposal.checks == {"stub": True}
    assert rules.FAKE_FIX_PATH in proposal.code_patch

    # Applied → the cloud creates the overlay row; a resend is a no-op, a different result 409.
    applied_at = t + timedelta(seconds=1)
    pid = pointer["proposal_id"]
    assert _result(client, pid, "applied", overlay_id="ov-doc-1", at=applied_at).status_code == 204
    resend = _result(client, pid, "applied", overlay_id="ov-doc-1", at=applied_at)
    assert resend.status_code == 200 and resend.json() == {"status": "already_received"}
    conflict = _result(client, pid, "rejected", at=applied_at)
    assert conflict.status_code == 409 and conflict.json()["error"] == "proposal_result_conflict"
    assert count_events("doctor.proposal_result", {"proposal": pid}) == 1
    ov = _overlay(db_session, "ov-doc-1")
    assert ov is not None and ov.incident_id == incident_id and ov.status == "active"
    assert ov.raw["proposal_id"] == pid and ov.raw["source"] == "doctor"
    assert ov.code_patch == proposal.code_patch and ov.touches_safety is False
    assert (ov.base_firmware, ov.base_harness, ov.base_code_rev) == (1, 1, "c0de001")
    assert count_events("overlay.applied", {"device": "arm-1", "overlay": "ov-doc-1"}) == 1
    assert _checkin(client, example)["proposal"] is None  # no longer offered

    t = doc.tick(t)  # the overlay is still active: keep waiting
    assert _consults(db_session, incident_id)[0].status == "waiting"
    _overlay_status(client, "ov-doc-1", "held", t + timedelta(seconds=1))
    doc.tick(t + timedelta(seconds=5))
    (consult,) = _consults(db_session, incident_id)
    assert (consult.stage, consult.status, consult.outcome) == ("closed", "closed", "held")
    assert _proposal(db_session, pid).status == "held"
    (closed,) = [e for e in _doctor_events(db_session, incident_id) if e.kind.endswith("closed")]
    assert closed.data["outcome"] == "held" and closed.refs["proposal"] == pid

    story = client.get(f"/v1/incidents/{incident_id}/story").json()
    kinds = [s["kind"] for s in story["steps"]]
    want = [
        "doctor.consult_opened",
        "doctor.diagnosed",
        "doctor.drafted",
        "doctor.checked",
        "doctor.proposal_offered",
        "doctor.proposal_result",
        "overlay.held",
        "doctor.consult_closed",
    ]
    assert [k for k in kinds if k in want] == want
    # Same `ts` as the result; which reads first is `story.STAGE`'s call (PR 6).
    assert kinds.count("overlay.applied") == 1
    steps = {s["kind"]: s for s in story["steps"]}
    assert steps["doctor.checked"]["why"].startswith("check: stub, passed by default")
    assert "stub" in steps["doctor.diagnosed"]["why"]


def test_a_held_status_that_arrives_before_applied_fills_its_placeholder(
    client, example, make_doctor, db_session, clock
):
    doc = make_doctor()
    incident_id, pid, t = _offered(doc, client, example, clock, db_session)
    _overlay_status(client, "ov-doc-1", "held", t)
    placeholder = _overlay(db_session, "ov-doc-1")
    assert placeholder.incident_id is None and placeholder.status == "held"

    assert _result(client, pid, "applied", overlay_id="ov-doc-1", at=t).status_code == 204
    ov = _overlay(db_session, "ov-doc-1")
    assert ov.incident_id == incident_id and ov.status == "held"  # its status is kept
    assert ov.raw["proposal_id"] == pid and ov.raw["last_status"]["status"] == "held"
    assert ov.code_patch and ov.base_code_rev == "c0de001"

    doc.tick(t + timedelta(seconds=5))
    assert _consults(db_session, incident_id)[0].outcome == "held"


# --- the device says no ---------------------------------------------------------------------


@pytest.mark.parametrize("result", ["stale", "rejected"])
def test_stale_or_rejected_closes_the_consult_without_a_retry(
    result, client, example, make_doctor, db_session, clock, count_events
):
    doc = make_doctor()
    incident_id, pid, t = _offered(doc, client, example, clock, db_session)
    assert _result(client, pid, result, at=t).status_code == 204
    assert _overlay(db_session, "ov-doc-1") is None
    doc.run(t, 3)
    (consult,) = _consults(db_session, incident_id)
    assert consult.outcome == result
    assert count_events("doctor.gave_up", {"incident": incident_id}) == 0


def test_reverted_retries_once_then_gives_up(
    client, example, make_doctor, db_session, clock, count_events
):
    doc = make_doctor()
    incident_id, pid1, t = _offered(doc, client, example, clock, db_session)
    assert _result(client, pid1, "applied", overlay_id="ov-doc-1", at=t).status_code == 204
    _overlay_status(client, "ov-doc-1", "reverted", t + timedelta(seconds=1))
    t = doc.tick(t + timedelta(seconds=5))  # closes attempt 1 and opens attempt 2
    first, second = _consults(db_session, incident_id)
    assert first.outcome == "reverted" and _proposal(db_session, pid1).status == "reverted"
    assert (second.attempt, second.origin, second.raw["retry_of"]) == (2, "device", first.id)

    t = doc.run(t, TICKS_TO_OFFER)
    second = _consults(db_session, incident_id)[1]
    assert second.stage == "applied"
    tried = second.case_file["tried"]
    assert {"by": "fleet-doctor", "attempt": 1, "outcome": "reverted", "proposal_id": pid1} in (
        tried
    )
    pid2 = _checkin(client, example)["proposal"]["proposal_id"]
    assert pid2 == second.raw["proposal_id"] != pid1
    assert _result(client, pid2, "failed_verify", at=t).status_code == 204
    t = doc.run(t, 4)
    assert [c.outcome for c in _consults(db_session, incident_id)] == ["reverted", "failed_verify"]
    assert count_events("doctor.gave_up", {"incident": incident_id}) == 1
    (gave_up,) = [e for e in _doctor_events(db_session, incident_id) if e.kind == "doctor.gave_up"]
    assert gave_up.data["consults_used"] == 2
    assert count_events("doctor.consult_opened", {"incident": incident_id}) == 2


# --- safety ---------------------------------------------------------------------------------


def test_unconfirmed_safety_paths_discard_every_fix(
    client, example, make_doctor, db_session, clock
):
    doc = make_doctor(safety_paths_confirmed=False)
    clock.at(T0)
    incident_id = _report(client, example)
    doc.run(T0 + timedelta(seconds=2), TICKS_TO_OFFER + 2)
    (consult,) = _consults(db_session, incident_id)
    assert consult.outcome == "discarded"
    (discarded,) = [
        e for e in _doctor_events(db_session, incident_id) if e.kind == "doctor.proposal_discarded"
    ]
    assert discarded.data["reason"] == "safety_paths_unknown"
    assert db_session.scalar(select(func.count()).select_from(DoctorProposal)) == 0
    assert _checkin(client, example)["proposal"] is None


@pytest.mark.parametrize("patch", [SAFETY_PATCH, ESCAPING_PATCH], ids=["safety", "escaping"])
def test_a_draft_touching_safety_is_discarded(
    patch, monkeypatch, client, example, make_doctor, db_session, clock
):
    """A patch naming a safety path, or one whose file list can't be trusted (fail closed),
    is discarded even with every opt-in on."""
    monkeypatch.setattr(
        rules, "fake_draft", lambda case: {"code_patch": patch, "repro_test": None, "why": "x"}
    )
    doc = make_doctor()
    clock.at(T0)
    incident_id = _report(client, example)
    doc.run(T0 + timedelta(seconds=2), TICKS_TO_OFFER + 2)
    (consult,) = _consults(db_session, incident_id)
    assert consult.outcome == "discarded"
    reasons = [
        e.data["reason"]
        for e in _doctor_events(db_session, incident_id)
        if e.kind == "doctor.proposal_discarded"
    ]
    assert reasons == ["touches_safety"]
    assert db_session.scalar(select(func.count()).select_from(DoctorProposal)) == 0


# --- caps, freeze, expiry -------------------------------------------------------------------


def test_the_daily_spend_cap_blocks_at_admit(client, example, make_doctor, db_session, clock):
    # A $0 cap: blocked before any money is spent.
    doc = make_doctor(doctor_daily_usd_cap=0.0)
    clock.at(T0)
    incident_id = _report(client, example)
    doc.tick(T0 + timedelta(seconds=2))
    (consult,) = _consults(db_session, incident_id)
    assert consult.outcome == "blocked_over_cap" and doc.jobs.calls == []
    (blocked,) = [
        e for e in _doctor_events(db_session, incident_id) if e.kind == "doctor.blocked_over_cap"
    ]
    assert blocked.data["which"] == "spend" and blocked.data["cap_usd"] == 0.0


def test_spend_adds_up_and_a_tiny_cap_blocks_the_next_consult(
    client, example, make_doctor, db_session, clock
):
    doc = make_doctor(doctor_daily_usd_cap=1e-6)
    clock.at(T0)
    first = _report(client, example)
    t = doc.run(T0 + timedelta(seconds=2), 3)  # admitted at $0, then diagnosed: spends
    assert _consults(db_session, first)[0].cost_usd > 1e-6
    clock.at(t)
    second = _report(client, example)
    doc.tick(t)
    (consult,) = _consults(db_session, second)
    assert consult.outcome == "blocked_over_cap"
    assert len(doc.jobs.calls) == 1


def test_a_frozen_fleet_holds_consults_hides_the_pointer_and_refuses_the_pull(
    client, example, make_doctor, db_session, clock
):
    doc = make_doctor()
    incident_id, pid, t = _offered(doc, client, example, clock, db_session)
    other = _report(client, example)
    assert client.post("/v1/fleet/freeze", json={"by": "kent", "why": "test"}).status_code == 200

    before = _event_count(db_session)
    t = doc.run(t, 3)
    assert _event_count(db_session) == before  # no event per tick while frozen
    assert _consults(db_session, other) == []  # nothing opens
    assert _consults(db_session, incident_id)[0].status == "waiting"
    checkin = _checkin(client, example)
    assert checkin["frozen"] is True and checkin["proposal"] is None
    resp = client.get(f"/v1/devices/arm-1/proposals/{pid}")
    assert resp.status_code == 409 and resp.json()["error"] == "fleet_frozen"

    assert client.post("/v1/fleet/unfreeze", json={"by": "kent"}).status_code == 200
    assert _checkin(client, example)["proposal"]["proposal_id"] == pid
    doc.tick(t)
    assert len(_consults(db_session, other)) == 1


def test_an_unpulled_fix_expires_and_a_late_result_is_still_recorded(
    client, example, make_doctor, db_session, clock, count_events
):
    doc = make_doctor(doctor_proposal_ttl_s=60)
    incident_id, pid, t = _offered(doc, client, example, clock, db_session)
    late = t + timedelta(seconds=61)
    doc.tick(late)
    assert _proposal(db_session, pid).status == "expired"
    assert count_events("doctor.proposal_expired", {"proposal": pid}) == 1
    doc.tick(late + timedelta(seconds=5))
    (consult,) = _consults(db_session, incident_id)
    assert consult.outcome == "expired"
    clock.at(late + timedelta(seconds=6))
    assert _checkin(client, example)["proposal"] is None
    resp = client.get(f"/v1/devices/arm-1/proposals/{pid}")
    assert resp.status_code == 410 and resp.json()["error"] == "proposal_gone"

    # The device pulled and applied it in time; only its report was late (the outbox).
    assert _result(client, pid, "applied", overlay_id="ov-doc-1", at=t).status_code == 204
    ov = _overlay(db_session, "ov-doc-1")
    assert ov.incident_id == incident_id and ov.raw["proposal_id"] == pid
    (result,) = [
        e for e in _doctor_events(db_session, incident_id) if e.kind == "doctor.proposal_result"
    ]
    assert result.data["late"] is True
    doc.run(late + timedelta(seconds=10), 2)
    assert len(_consults(db_session, incident_id)) == 1  # expired isn't retried


# --- what never gets a consult --------------------------------------------------------------


def test_an_incident_without_code_rev_is_declined_not_rejected(
    client, example, make_doctor, db_session, clock
):
    doc = make_doctor()
    clock.at(T0)
    incident_id = _report(client, example, running={"firmware": 1, "harness": 1})
    doc.tick(T0 + timedelta(seconds=2))
    (consult,) = _consults(db_session, incident_id)
    assert consult.outcome == "declined"
    (declined,) = [
        e for e in _doctor_events(db_session, incident_id) if e.kind == "doctor.declined"
    ]
    assert declined.data["stage"] == "admit" and "code_rev" in declined.why


def test_an_incident_older_than_the_lookback_is_never_consulted(
    client, example, make_doctor, db_session, clock
):
    doc = make_doctor()
    clock.at(T0 - timedelta(hours=7))
    old = _report(client, example)
    clock.at(T0 - timedelta(hours=5))
    recent = _report(client, example)
    doc.run(T0, 2)
    assert _consults(db_session, old) == []
    assert len(_consults(db_session, recent)) == 1


def test_auto_consult_is_off_by_default_and_never_opens_on_hardware(
    client, example, make_doctor, db_session, clock
):
    clock.at(T0)
    software = _report(client, example, consult=None)
    hardware = _report(client, example, name="incident_park_hardware")
    resolved = _report(
        client,
        example,
        consult=None,
        outcome={"resolved": True, "resolving_action": "patch", "suspected_cause": "software"},
    )
    make_doctor().run(T0 + timedelta(seconds=2), 2)
    assert _consults(db_session, software) == []

    make_doctor(doctor_auto_consult=True).run(T0 + timedelta(seconds=20), 2)
    (consult,) = _consults(db_session, software)
    assert (consult.origin, consult.trigger) == ("cloud", None)
    assert _consults(db_session, hardware) == [] and _consults(db_session, resolved) == []


def test_consult_true_is_shorthand_for_wanted(client, example, make_doctor, db_session, clock):
    clock.at(T0)
    incident_id = _report(client, example, consult=True)
    make_doctor().tick(T0 + timedelta(seconds=2))
    (consult,) = _consults(db_session, incident_id)
    assert (consult.origin, consult.trigger) == ("device", None)


def test_with_default_settings_the_doctor_does_nothing(
    client, example, db_session, clock, settings, seeded
):
    clock.at(T0)
    incident_id = _report(client, example)
    assert doctor.tick(db_session, T0, settings=settings, jobs=InlineJobs(), llm=FakeLlm()) == {}
    assert _consults(db_session, incident_id) == []
    assert _doctor_events(db_session, incident_id) == []


@pytest.mark.parametrize(
    ("overrides", "outcome"),
    [
        ({}, "discarded"),  # safety paths unconfirmed
        ({"safety_paths_confirmed": True}, "advised"),  # a stub check isn't offered
        (
            {
                "safety_paths_confirmed": True,
                "doctor_offer_stub_checked": True,
                "doctor_draft": "none",
            },
            "advised",
        ),
    ],
    ids=["fake-defaults", "stub-not-offered", "draft-none"],
)
def test_fake_mode_without_the_opt_ins_never_offers(
    overrides, outcome, client, example, make_doctor, db_session, clock
):
    defaults = {"doctor_offer_stub_checked": False, "safety_paths_confirmed": False}
    doc = make_doctor(**(defaults | overrides))
    clock.at(T0)
    incident_id = _report(client, example)
    doc.run(T0 + timedelta(seconds=2), TICKS_TO_OFFER + 2)
    assert _consults(db_session, incident_id)[0].outcome == outcome
    assert db_session.scalar(select(func.count()).select_from(DoctorProposal)) == 0


# --- jobs -----------------------------------------------------------------------------------


def test_a_pending_job_waits_a_lost_one_is_resubmitted(
    client, example, make_doctor, db_session, clock
):
    jobs = ManualJobs()
    doc = make_doctor(jobs=jobs)
    clock.at(T0)
    incident_id = _report(client, example)
    t = doc.run(T0 + timedelta(seconds=2), 3)  # admit, gather, diagnose submitted
    (consult,) = _consults(db_session, incident_id)
    key = f"diagnose:{consult.id}"
    assert (consult.stage, consult.status, consult.job_key) == ("diagnose", "waiting", key)
    t = doc.tick(t)  # still pending: nothing moves
    assert _consults(db_session, incident_id)[0].stage == "diagnose"

    jobs.lose()  # the worker restarted
    t = doc.tick(t)
    assert jobs.calls == [key, key]
    assert _consults(db_session, incident_id)[0].raw["job_tries"] == {key: 2}
    jobs.release()
    doc.tick(t)
    consult = _consults(db_session, incident_id)[0]
    assert consult.stage == "draft" and consult.ledger
    assert isinstance(jobs.poll(key), Missing)  # forgotten after the commit


class BrokenLlm(FakeLlm):
    def complete(self, system, user, *, max_tokens=None, timeout_s=None):
        raise LlmError("provider down")


def test_a_job_that_fails_three_times_gives_up(client, example, make_doctor, db_session, clock):
    doc = make_doctor(llm=BrokenLlm())
    clock.at(T0)
    incident_id = _report(client, example)
    doc.run(T0 + timedelta(seconds=2), 8)
    (consult,) = _consults(db_session, incident_id)
    assert consult.outcome == "gave_up"
    assert doc.jobs.calls == [f"diagnose:{consult.id}"] * 3
    (gave_up,) = [e for e in _doctor_events(db_session, incident_id) if e.kind == "doctor.gave_up"]
    assert gave_up.data["reason"].startswith("job failed") and "provider down" in gave_up.why


# --- route errors ---------------------------------------------------------------------------


def test_proposal_route_errors(client, example, make_doctor, db_session, clock):
    doc = make_doctor()
    _, pid, t = _offered(doc, client, example, clock, db_session)
    assert client.get("/v1/devices/arm-1/proposals/prop_nope").status_code == 404
    other = client.get(f"/v1/devices/sim-1/proposals/{pid}")
    assert other.status_code == 404 and other.json()["error"] == "unknown_proposal"
    assert _result(client, pid, "stale", device="sim-1").status_code == 404
    no_overlay = _result(client, pid, "applied")
    assert no_overlay.status_code == 422
    bad_version = _result(client, pid, "stale", contract_version="9.9")
    assert bad_version.status_code == 400
    assert _proposal(db_session, pid).status == "offered"  # nothing was recorded
