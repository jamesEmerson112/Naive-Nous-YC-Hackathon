"""F12 interop: the real cloud's Fleet Doctor and the unmodified fake device, end to end.

The fake fleet (`arms`, tools/fake_device) talks to the real app through `device_http`, so
its DeviceClient, visor, outbox, `TriggerHook` and `ProposalApplier` run exactly as they
would against a live cloud. The Doctor runs as the worker would: one `doctor.tick` per
call through `run_tick`, with `InlineJobs` and `FakeLlm`. The API and the Doctor share the
opt-in settings (`ON`); nothing calls a model.

The flow: F1 installs → `SCENARIOS["consult"]` on sim-1 (an L1 ask to the real
`/doctor/ask`, then an unresolved incident asking for a consult, the advice in its
reasoning) → Doctor ticks until the fix is offered → the arm's next check-in carries the
pointer, the applier pulls it (sha256 checked against `X-Sha256` and the pointer), checks
the base, applies, reports `applied` → healthy samples across the 60 s mark-good window on
the arm's clock → `held` after the hold delay → the Doctor closes the consult `held`.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from functools import partial

import pytest
from sqlalchemy import select

from fake_device.scenarios import SCENARIOS
from fake_device.scenarios import Step as ScenarioStep
from fake_device.visor import TickResult
from romulus_cloud.adapters.llm import FakeLlm
from romulus_cloud.models import DoctorConsult, DoctorProposal, Overlay
from romulus_cloud.services import doctor
from romulus_cloud.services.doctor_jobs import InlineJobs
from romulus_cloud.worker import Step, run_tick
from romulus_contracts import SHA256_HEADER

T0 = datetime(2026, 11, 7, 22, 0, tzinfo=UTC)
ON = {"doctor_mode": "fake", "doctor_offer_stub_checked": True, "safety_paths_confirmed": True}
TICKS_TO_OFFER = 6  # open + admit, gather, diagnose, draft, check, offer
HOLD_AFTER_S = 600.0  # the scenario's wait, and the proposal overlay's `held` delay
FAULT_AT = T0 + timedelta(seconds=20)  # after F1
STORY = [
    "doctor.asked",
    "doctor.advised",
    "doctor.consult_opened",
    "doctor.diagnosed",
    "doctor.drafted",
    "doctor.checked",
    "doctor.proposal_offered",
    "doctor.proposal_result",
    "overlay.applied",
    "overlay.held",
    "doctor.consult_closed",
]


@pytest.fixture
def settings(make_settings):
    """The API's settings (the `app` fixture's), with the Doctor on as in the worker. The
    check-in pointer and the pull re-check them on every request."""
    return make_settings(**ON)


@pytest.fixture
def doctor_tick(sessions, clock, settings) -> Callable[[datetime], None]:
    """`doctor_tick(t)`: the worker's `doctor.tick` at `t`, committed by `run_tick`."""
    fn = partial(doctor.tick, settings=settings, jobs=InlineJobs(), llm=FakeLlm())

    def tick(t: datetime) -> None:
        clock.at(t)
        assert run_tick([Step("doctor.tick", fn)], sessions, t, {}) == {"doctor.tick": "ok"}

    return tick


@pytest.fixture
def tick(arms, clock) -> Callable[[datetime], dict[str, TickResult]]:
    """`tick(t)`: every fake arm runs one visor cycle (and its Doctor tick) at `t`."""

    def run(t: datetime) -> dict[str, TickResult]:
        clock.at(t)
        return {name: arm.tick(t) for name, arm in arms.items()}

    return run


def _consult(db_session, incident_id: str) -> DoctorConsult:
    db_session.expire_all()
    stmt = select(DoctorConsult).where(DoctorConsult.incident_id == incident_id)
    (consult,) = db_session.scalars(stmt)
    return consult


def _offered(
    arms, tick, doctor_tick, clock, client, db_session
) -> tuple[str, str, datetime, ScenarioStep]:
    """F1, then the consult scenario's fault on sim-1 (at FAULT_AT) and Doctor ticks until
    its fix is offered → (incident_id, proposal_id, the time for the next tick, the
    scenario's remaining "consult wait over" step)."""
    for t in (T0, T0 + timedelta(seconds=10)):  # F1: pull, install, report; last_good
        tick(t)
    sim1 = client.get("/v1/devices/sim-1").json()
    assert sim1["running"]["code_rev"] == "c0de001" and sim1["last_good"]["firmware"] == 1

    arm = arms["sim-1"]
    fault, wait_over = SCENARIOS["consult"](arm, HOLD_AFTER_S)
    clock.at(FAULT_AT)
    fault.run(FAULT_AT)

    # L1: the ask reached the real `/doctor/ask`, and its advice is in the incident.
    (asked,) = arm.doctor.history
    assert asked["trigger"] == "T2" and asked["advice"] is not None
    assert not arm.doctor.breaker.tripped
    assert asked["advice"]["ask_id"].startswith("ask_")
    assert len(arm.outbox) == 0  # incident and recordings delivered
    (incident,) = [i for i in client.get("/v1/incidents").json() if i["device_id"] == "sim-1"]
    incident_id = incident["incident_id"]
    assert asked["ask"]["incident_id"] == incident_id
    assert arm.device.consult_parked and arm.device.health() == "degraded"
    story = client.get(f"/v1/incidents/{incident_id}/story").json()
    (agent,) = [s for s in story["steps"] if s["kind"] == "incident.agent_acted"]
    first_claim = asked["advice"]["hypotheses"][0]["claim"]
    assert "Fleet Doctor (T2)" in agent["why"] and first_claim in agent["why"]

    t = FAULT_AT + timedelta(seconds=2)
    for _ in range(TICKS_TO_OFFER):
        doctor_tick(t)
        t += timedelta(seconds=5)
    consult = _consult(db_session, incident_id)
    assert (consult.origin, consult.trigger) == ("device", "T2")
    assert (consult.stage, consult.status) == ("applied", "waiting"), consult.outcome
    return incident_id, consult.raw["proposal_id"], t, wait_over


def test_the_fake_device_takes_a_doctor_fix_end_to_end(
    arms, tick, doctor_tick, clock, client, db_session, monkeypatch
):
    incident_id, pid, t, wait_over = _offered(arms, tick, doctor_tick, clock, client, db_session)
    arm = arms["sim-1"]

    # What the device will pull: the header hashes the exact body bytes served.
    served = client.get(f"/v1/devices/sim-1/proposals/{pid}")
    assert served.status_code == 200, served.text
    sha = hashlib.sha256(served.content).hexdigest()
    assert served.headers[SHA256_HEADER] == sha

    pulls: list[tuple[str, str | None, str]] = []
    real_get = arm.client.get_proposal

    def spy(proposal_id, *, expected_sha256=None):
        data, got = real_get(proposal_id, expected_sha256=expected_sha256)
        pulls.append((proposal_id, expected_sha256, got))
        return data, got

    monkeypatch.setattr(arm.client, "get_proposal", spy)

    # The next check-in points at the fix; the applier pulls, checks, applies, reports.
    applied_at = t + timedelta(seconds=5)
    pointer = tick(applied_at)["sim-1"].response.proposal
    assert pointer is not None and pointer.proposal_id == pid
    assert str(pointer.incident_id) == incident_id and pointer.sha256 == sha
    assert pulls == [(pid, sha, sha)]  # checked against the pointer and the header
    overlay = arm.device.overlay
    assert overlay is not None and overlay.proposal_id == pid
    overlay_id = overlay.overlay_id
    assert len(arm.outbox) == 0  # `applied` delivered in the same tick
    db_session.expire_all()
    proposal = db_session.get(DoctorProposal, pid)
    assert (proposal.status, proposal.overlay_id) == ("applied", overlay_id)
    assert overlay.code_patch == proposal.code_patch
    row = db_session.get(Overlay, ("sim-1", overlay_id))
    assert row is not None and row.status == "active"
    assert row.incident_id == incident_id and row.raw["proposal_id"] == pid
    assert client.get(f"/v1/devices/sim-1/proposals/{pid}").status_code == 410

    # Mark-good on the arm's monotonic clock (the tick's `now`): a healthy sample in each
    # third of the 60 s window. The arm stays parked until then.
    timer = arm.applier.timer
    assert timer is not None and timer.started == applied_at.timestamp()
    for s in (20, 40):
        tick(applied_at + timedelta(seconds=s))
        assert arm.applier.timer is timer and arm.device.consult_parked
    good_at = applied_at + timedelta(seconds=60)
    tick(good_at)
    assert arm.applier.timer is None and arm.applier.inflight is None
    assert not arm.device.consult_parked and arm.device.active_overlay_id() == overlay_id
    assert client.get("/v1/devices/sim-1").json()["overlay_id"] == overlay_id

    doctor_tick(good_at + timedelta(seconds=1))  # marked good is not held: keep waiting
    assert _consult(db_session, incident_id).status == "waiting"
    wait_over.run(FAULT_AT + timedelta(seconds=wait_over.at_s))  # the scenario's last step:
    assert arm.device.active_overlay_id() == overlay_id  # a no-op once the fix is good

    # `held` after the hold delay, through the outbox; then the Doctor closes the consult.
    held_at = good_at + timedelta(seconds=HOLD_AFTER_S)
    tick(held_at)
    assert arm.device.overlay_held and len(arm.outbox) == 0
    db_session.expire_all()
    assert db_session.get(Overlay, ("sim-1", overlay_id)).status == "held"
    doctor_tick(held_at + timedelta(seconds=1))
    consult = _consult(db_session, incident_id)
    assert (consult.stage, consult.status, consult.outcome) == ("closed", "closed", "held")
    db_session.expire_all()
    assert db_session.get(DoctorProposal, pid).status == "held"

    story = client.get(f"/v1/incidents/{incident_id}/story").json()
    kinds = [s["kind"] for s in story["steps"]]
    assert [k for k in kinds if k in STORY] == STORY
    assert story["ledger"]
    (closed,) = [s for s in story["steps"] if s["kind"] == "doctor.consult_closed"]
    assert closed["refs"]["proposal"] == pid and "held" in closed["line"]


def test_a_frozen_fleet_hides_the_fix_and_the_arm_takes_nothing_until_unfrozen(
    arms, tick, doctor_tick, clock, client, db_session
):
    incident_id, pid, t, _ = _offered(arms, tick, doctor_tick, clock, client, db_session)
    arm = arms["sim-1"]

    clock.at(t)
    assert client.post("/v1/fleet/freeze", json={"by": "kent", "why": "test"}).status_code == 200
    resp = tick(t + timedelta(seconds=5))["sim-1"].response
    assert resp.frozen is True and resp.proposal is None
    assert arm.device.overlay is None and arm.applier.timer is None
    assert pid not in arm.applier.handled and len(arm.outbox) == 0
    db_session.expire_all()
    assert db_session.get(DoctorProposal, pid).status == "offered"
    doctor_tick(t + timedelta(seconds=6))  # frozen: the consult holds in place
    assert _consult(db_session, incident_id).status == "waiting"

    clock.at(t + timedelta(seconds=10))
    assert client.post("/v1/fleet/unfreeze", json={"by": "kent"}).status_code == 200
    resp = tick(t + timedelta(seconds=15))["sim-1"].response
    assert resp.frozen is False and resp.proposal.proposal_id == pid
    assert arm.device.overlay is not None and arm.device.overlay.proposal_id == pid
    db_session.expire_all()
    proposal = db_session.get(DoctorProposal, pid)
    assert (proposal.status, proposal.overlay_id) == ("applied", arm.device.overlay.overlay_id)
