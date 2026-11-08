"""F12c: a Fleet Doctor consult with a real draft and a real check (PR 7).

DOCTOR_DRAFT=coder and DOCTOR_CHECK=sandbox. The coder is a scripted stand-in that returns a
fixed patch (no LLM), and the sandbox is the real `Sandbox` with `FakeDocker` as its runner:
no container starts, but each "container" runs the device tests on the host against the toy
device (`tests/fixtures/toy_device`, the planted D1 fault), so the repro test really fails on
the base and really passes with the fix. Patches are built with `code_lockin.package_diff`,
so they are what `git diff` would write.

Proves: repro fails then passes → offered with DOCTOR_OFFER_STUB_CHECKED off; repro passes
on the base, the fix fails, or no repro test → `failed_checks`; the coder budget (shared
with the batch's `coder.started`) makes the consult wait, not fail; a closed consult leaves
a `doctor_consult` recording that a `traces_jsonl` export carries; `verify/` never reaches
the coder or the sandbox.
"""

import io
import json
import sys
import tarfile
import uuid
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path

import pytest
from sqlalchemy import func, select

from romulus_cloud import events
from romulus_cloud.adapters.blobstore import build_blobstore
from romulus_cloud.adapters.coder import CodeDraft, Sandbox
from romulus_cloud.adapters.llm import FakeLlm
from romulus_cloud.models import Blob, DoctorConsult, DoctorProposal, Event, Recording
from romulus_cloud.services import code_lockin, datasets, doctor, registry
from romulus_cloud.services.doctor_jobs import InlineJobs
from romulus_cloud.worker import Step, run_tick

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "fixtures"))
import coder_fakes as fakes  # noqa: E402

T0 = datetime(2026, 11, 7, 22, 0, tzinfo=UTC)
CODE_REV = "toyd1"
SECRET = "llm-SECRET-key-0123456789abcdef"
ON = {
    "doctor_mode": "fake",
    "doctor_draft": "coder",
    "doctor_check": "sandbox",
    "safety_paths_confirmed": True,
    "doctor_offer_stub_checked": False,  # a real check needs no stub opt-in
    "llm_api_key": SECRET,
}
TICKS_TO_OFFER = 6  # open + admit, gather, diagnose, draft, check, offer

GRIPPER = (fakes.TOY / "control" / "gripper.py").read_text()
RULE = (
    "        if dist_mm is not None and dist_mm < PROXIMITY_MM:\n"
    '            return "RELEASE", "open"\n'
)
FIXED = GRIPPER.replace(RULE, "")
NOT_A_FIX = GRIPPER.replace('count as "something is close"', "count as near")
REPRO = (
    "from control.gripper import step\n\n\n"
    "def test_a_hand_near_while_holding_keeps_the_gripper_closed():\n"
    '    assert step("HOLDING", 10.0) == ("HOLDING", "close")\n'
)
PASSES_ON_BASE = (
    "from control.gripper import step\n\n\n"
    "def test_idle_stays_open():\n"
    '    assert step("IDLE", 300) == ("IDLE", "open")\n'
)
REPRO_PATH = "tests/test_doctor_repro.py"


def _patch(**changes: str) -> str:
    """A code_patch against the toy device: `control__gripper_py=...` → control/gripper.py."""
    base = fakes.toy_files()
    new = dict(base)
    for key, text in changes.items():
        new[f"device/{key.replace('__', '/').replace('_py', '.py')}"] = text.encode()
    return code_lockin.package_diff(base, new)


GOOD = _patch(control__gripper_py=FIXED, tests__test_doctor_repro_py=REPRO)


class ScriptedCoder:
    """Returns a fixed patch, like a coder would; records what it was given."""

    def __init__(self, diff: str, *, passed: bool = True, notes: str = "") -> None:
        self.diff, self.passed = diff, passed
        self.notes = notes or "scripted coder: 7 steps, tokens 1200 in / 300 out"
        self.calls: list[dict] = []

    def draft(self, patch, incident, code_rev, files=None, task=None) -> CodeDraft:
        self.calls.append(
            {
                "patch": patch,
                "incident": incident,
                "code_rev": code_rev,
                "files": files,
                "task": task,
            }
        )
        return CodeDraft(self.diff, "5 passed in 0.10s", self.passed, self.notes)


class WatchingDocker(fakes.FakeDocker):
    """FakeDocker that remembers whether any run's work dir ever held `verify/`."""

    def __init__(self) -> None:
        super().__init__()
        self.ever_saw_verify = False

    def __call__(self, argv, **kw):
        result = super().__call__(argv, **kw)
        self.ever_saw_verify |= self.saw_verify_in_work
        return result


class Doctor:
    def __init__(self, sessions, clock, settings, coder) -> None:
        self.sessions, self.clock, self.settings, self.coder = sessions, clock, settings, coder
        self.jobs, self.llm, self.docker = InlineJobs(), FakeLlm(), WatchingDocker()
        self.sandbox = Sandbox(image="romulus-coder:test", runner=self.docker)

    def tick(self, t: datetime) -> datetime:
        self.clock.at(t)
        fn = partial(
            doctor.tick,
            settings=self.settings,
            jobs=self.jobs,
            llm=self.llm,
            coder=self.coder,
            sandbox=self.sandbox,
        )
        assert run_tick([Step("doctor.tick", fn)], self.sessions, t, {}) == {"doctor.tick": "ok"}
        return t + timedelta(seconds=5)

    def run(self, t: datetime, ticks: int) -> datetime:
        for _ in range(ticks):
            t = self.tick(t)
        return t


@pytest.fixture
def settings(make_settings):
    """The API's settings, like the worker's (the pointer and pull re-check them)."""
    return make_settings(**ON)


@pytest.fixture
def toy(seeded, settings):
    """The toy device, verify scripts included, registered as code `toyd1`."""
    store = build_blobstore(settings.blob_dir)
    kw = {"actor": "human:test", "actor_class": "human"}
    registry.create_code(
        seeded, store, registry.build_tarball(fakes.toy_files()), rev=CODE_REV, **kw
    )
    seeded.commit()
    return seeded


@pytest.fixture
def make_doctor(sessions, clock, make_settings, toy):
    def make(coder, **overrides) -> Doctor:
        return Doctor(sessions, clock, make_settings(**(ON | overrides)), coder)

    return make


def _report(client, example) -> str:
    body = example("incident_consult_wanted") | {
        "incident_id": str(uuid.uuid4()),
        "occurred_at": (T0 - timedelta(minutes=1)).isoformat(),
        "running": {"firmware": 1, "harness": 1, "code_rev": CODE_REV},
    }
    body["log_tail"] = [*body["log_tail"], "Traceback: control/gripper.py line 27 in step"]
    resp = client.post("/v1/devices/arm-1/incidents", json=body)
    assert resp.status_code == 201, resp.text
    return body["incident_id"]


def _consults(db_session, incident_id) -> list[DoctorConsult]:
    db_session.expire_all()
    stmt = select(DoctorConsult).where(DoctorConsult.incident_id == incident_id)
    return list(db_session.scalars(stmt.order_by(DoctorConsult.attempt)))


def _events(db_session, incident_id, kind=None) -> list[Event]:
    db_session.expire_all()
    stmt = select(Event).where(Event.refs.contains({"incident": incident_id}))
    if kind:
        stmt = stmt.where(Event.kind == kind)
    return list(db_session.scalars(stmt.order_by(Event.ts, Event.recorded_at)))


def _first_closed(doc, db_session, incident_id, t, max_ticks=8) -> DoctorConsult:
    for _ in range(max_ticks):
        t = doc.tick(t)
        first = _consults(db_session, incident_id)[0]
        if first.status == "closed":
            return first
    raise AssertionError(f"consult still {first.stage}/{first.status}")


def _test_cmds(docker) -> list[str]:
    return [c["env"].get("CODER_TEST_CMD", "") for c in docker.calls if c["argv"][-1] == "test"]


# --- repro fails, then passes → offered ------------------------------------------------------


def test_a_real_draft_and_check_offer_the_fix(client, example, make_doctor, db_session, clock):
    coder = ScriptedCoder(GOOD)
    doc = make_doctor(coder)
    clock.at(T0)
    incident_id = _report(client, example)
    doc.run(T0 + timedelta(seconds=2), TICKS_TO_OFFER)

    (consult,) = _consults(db_session, incident_id)
    assert (consult.stage, consult.status, consult.outcome) == ("applied", "waiting", None)
    # The draft: the coder's patch, its repro test found inside it, a real (non-stub) draft.
    assert consult.draft["stub"] is False and consult.draft["code_patch"] == GOOD
    assert consult.draft["repro_test"] == REPRO_PATH
    # The check: really failed before and passed after, in the sandbox.
    checks = consult.checks
    assert checks["stub"] is False and checks["repro_test"] == REPRO_PATH
    assert (checks["fails_before"], checks["passes_after"], checks["device_tests"]) == (
        True,
        True,
        True,
    )
    (checked,) = _events(db_session, incident_id, "doctor.checked")
    assert checked.data["passed"] is True and checked.data["stub"] is False
    assert "1 failed" in checked.data["before"]["tests_summary"]
    assert "passed" in checked.data["after"]["tests_summary"]

    # The coder got the base package without verify/, and a task that asks for a repro first.
    (call,) = coder.calls
    assert call["patch"] == "" and call["code_rev"] == CODE_REV
    assert "device/control/gripper.py" in call["files"]
    assert not [n for n in call["files"] if "verify" in n.lower()]
    assert "reproduction test" in call["task"] and '<evidence untrusted="true">' in call["task"]
    assert "Traceback: control/gripper.py" in call["task"].split("<evidence", 1)[1]
    # The case file excerpted the file the log names; still no verify.
    assert [f["path"] for f in consult.case_file["code"]["files"]] == ["control/gripper.py"]
    assert "verify" not in json.dumps(consult.case_file["code"]).lower()

    # The sandbox: no network, never saw verify/, the repro alone first (narrowed command).
    assert doc.docker.calls and not doc.docker.ever_saw_verify
    assert all("--network" in c["argv"] and "none" in c["argv"] for c in doc.docker.calls)
    cmds = _test_cmds(doc.docker)
    assert cmds[:2] == [f"python -m pytest -q {REPRO_PATH}"] * 2  # base, then repro only
    assert cmds[2:] == ["python -m pytest -q"] * 2  # base, then the full patch

    # Spend: the coder's own token counts; the draft counted against the shared budget.
    (drafted,) = _events(db_session, incident_id, "doctor.drafted")
    assert drafted.data["usage_estimated"] is False
    assert (drafted.data["prompt_tokens"], drafted.data["completion_tokens"]) == (1200, 300)
    assert drafted.data["cost_usd"] == pytest.approx(1.5 * doc.settings.doctor_usd_per_1k_tokens)
    (started,) = _events(db_session, incident_id, "coder.started")
    assert started.data["source"] == "doctor" and started.data["base_code_rev"] == CODE_REV
    assert started.refs["consult"] == consult.id

    # Offered: the check-in points at it and the device can pull it, stub opt-in off.
    resp = client.post("/v1/devices/arm-1/checkin", json=example("checkin"))
    pointer = resp.json()["proposal"]
    assert pointer is not None and pointer["proposal_id"] == consult.raw["proposal_id"]
    pulled = client.get(f"/v1/devices/arm-1/proposals/{pointer['proposal_id']}")
    assert pulled.status_code == 200, pulled.text
    body = pulled.json()
    assert body["checks"]["stub"] is False and body["checks"]["fails_before"] is True
    assert body["repro_test"] == REPRO_PATH and body["code_patch"] == GOOD


# --- failed checks ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("diff", "reason"),
    [
        (
            _patch(control__gripper_py=FIXED, tests__test_doctor_repro_py=PASSES_ON_BASE),
            "doesn't fail on the base code (exit 0",
        ),
        (
            _patch(control__gripper_py=NOT_A_FIX, tests__test_doctor_repro_py=REPRO),
            "the device tests don't pass with the fix (exit 1",
        ),
    ],
    ids=["repro-passes-on-base", "fix-fails"],
)
def test_a_check_that_doesnt_hold_closes_failed_checks(
    diff, reason, client, example, make_doctor, db_session, clock
):
    doc = make_doctor(ScriptedCoder(diff))
    clock.at(T0)
    incident_id = _report(client, example)
    first = _first_closed(doc, db_session, incident_id, T0 + timedelta(seconds=2))
    assert first.outcome == "failed_checks"
    (checked,) = [e for e in _events(db_session, incident_id, "doctor.checked")]
    assert checked.data["passed"] is False and reason in checked.data["reason"]
    (closed,) = [
        e
        for e in _events(db_session, incident_id, "doctor.consult_closed")
        if e.refs["consult"] == first.id
    ]
    assert reason in closed.data["why"]
    assert db_session.scalar(select(func.count()).select_from(DoctorProposal)) == 0
    # failed_checks retries within the consult cap (attempt 2 opens).
    assert [c.attempt for c in _consults(db_session, incident_id)] == [1, 2]


def test_a_repro_that_passes_on_the_base_never_runs_the_full_patch(
    client, example, make_doctor, db_session, clock
):
    diff = _patch(control__gripper_py=FIXED, tests__test_doctor_repro_py=PASSES_ON_BASE)
    doc = make_doctor(ScriptedCoder(diff))
    clock.at(T0)
    incident_id = _report(client, example)
    _first_closed(doc, db_session, incident_id, T0 + timedelta(seconds=2))
    assert _test_cmds(doc.docker) == [f"python -m pytest -q {REPRO_PATH}"] * 2


@pytest.mark.parametrize(
    ("coder", "reason"),
    [
        (ScriptedCoder(_patch(control__gripper_py=FIXED)), "no repro test"),
        (ScriptedCoder(""), "the coding agent changed nothing"),
        (ScriptedCoder(GOOD, passed=False), "the coding agent's own test run did not pass"),
        (
            ScriptedCoder(GOOD + _patch(verify__test_verify_d1_py="def test_x(): pass\n")),
            "held-out verify/",
        ),
    ],
    ids=["no-repro-test", "empty", "own-tests-failed", "touches-verify"],
)
def test_a_draft_that_cant_be_checked_closes_failed_checks_before_the_sandbox(
    coder, reason, client, example, make_doctor, db_session, clock
):
    doc = make_doctor(coder)
    clock.at(T0)
    incident_id = _report(client, example)
    first = _first_closed(doc, db_session, incident_id, T0 + timedelta(seconds=2))
    assert first.outcome == "failed_checks" and first.stage == "closed"
    (closed,) = [
        e
        for e in _events(db_session, incident_id, "doctor.consult_closed")
        if e.refs["consult"] == first.id
    ]
    assert reason in closed.data["why"]
    (drafted,) = [
        e
        for e in _events(db_session, incident_id, "doctor.drafted")
        if e.refs["consult"] == first.id
    ]
    assert drafted.data["cost_usd"] > 0  # the coder ran, so it is paid for either way
    assert _events(db_session, incident_id, "doctor.checked") == []
    assert doc.docker.calls == []  # the sandbox never ran


def test_a_draft_without_token_counts_is_charged_the_estimate(
    client, example, make_doctor, db_session, clock
):
    doc = make_doctor(ScriptedCoder(GOOD, notes="no usage here"), doctor_draft_estimate_usd=0.25)
    clock.at(T0)
    incident_id = _report(client, example)
    doc.run(T0 + timedelta(seconds=2), 4)
    (drafted,) = _events(db_session, incident_id, "doctor.drafted")
    assert drafted.data["usage_estimated"] is True and drafted.data["cost_usd"] == 0.25
    assert drafted.data["prompt_tokens"] is None


# --- the coder budget ------------------------------------------------------------------------


def test_an_exhausted_coder_budget_makes_the_consult_wait_not_fail(
    client, example, make_doctor, db_session, clock
):
    """Six batch drafts started 50 minutes ago use the hour's budget: the consult waits in
    `draft` until the oldest leaves the window (T0 + 10 min), then drafts."""
    for i in range(6):
        events.record(
            db_session,
            "agent:batch",
            "agent",
            "coder.started",
            {"device": "sim-1", "overlay": f"ov-{i}"},
            "batch draft",
            data={"job": f"coder.draft:{i}"},
            ts=T0 - timedelta(minutes=50),
        )
    db_session.commit()
    coder = ScriptedCoder(GOOD)
    doc = make_doctor(coder)  # CODER_MAX_DRAFTS_PER_HOUR = 6, the default
    clock.at(T0)
    incident_id = _report(client, example)
    t = doc.run(T0 + timedelta(seconds=2), 4)  # admit, gather, diagnose, draft (held)

    (consult,) = _consults(db_session, incident_id)
    frees_at = T0 + timedelta(minutes=10)
    assert (consult.stage, consult.status, consult.outcome) == ("draft", "waiting", None)
    assert consult.raw["retry_at"] == frees_at.isoformat()
    assert "job_tries" not in consult.raw or "draft:" + consult.id not in consult.raw["job_tries"]
    assert coder.calls == [] and _events(db_session, incident_id, "coder.started") == []
    (waiting,) = _events(db_session, incident_id, "doctor.declined")
    assert waiting.data["which"] == "coder_budget" and waiting.data["stage"] == "draft"
    assert (waiting.data["drafts_started"], waiting.data["budget"]) == (6, 6)
    assert waiting.data["cost_usd"] == 0.0

    # Before retry_at: nothing happens, nothing is written.
    before = db_session.scalar(select(func.count()).select_from(Event))
    doc.tick(T0 + timedelta(minutes=5))
    assert db_session.scalar(select(func.count()).select_from(Event)) == before
    assert _consults(db_session, incident_id)[0].stage == "draft"

    # After it: the batch drafts left the window, so the Doctor drafts (and counts).
    doc.tick(frees_at + timedelta(seconds=1))
    consult = _consults(db_session, incident_id)[0]
    assert consult.stage == "check" and "retry_at" not in consult.raw
    assert len(coder.calls) == 1
    (started,) = _events(db_session, incident_id, "coder.started")
    assert started.data["source"] == "doctor"
    assert t < frees_at


def test_doctor_drafts_use_up_the_budget_the_batch_reads(
    client, example, make_doctor, db_session, clock
):
    """With a budget of 1, the Doctor's own draft fills it: the other incident's consult
    waits, and the batch's count (`coder.started` in the last hour) sees the Doctor's. (Both
    consults open in one tick, so which one drafts first follows their ids.)"""
    coder = ScriptedCoder(GOOD)
    doc = make_doctor(coder, coder_max_drafts_per_hour=1)
    clock.at(T0)
    incidents = [_report(client, example), _report(client, example)]
    doc.run(T0 + timedelta(seconds=2), 4)
    consults = sorted((_consults(db_session, i)[0] for i in incidents), key=lambda c: c.stage)
    drafted, waiting = consults  # "check" sorts before "draft"
    assert (drafted.stage, waiting.stage, waiting.status) == ("check", "draft", "waiting")
    assert "retry_at" not in drafted.raw and waiting.raw["retry_at"]
    assert len(coder.calls) == 1
    count = select(func.count()).select_from(Event).where(Event.kind == "coder.started")
    assert db_session.scalar(count) == 1  # what batch._drafts_this_hour counts


# --- the trace -------------------------------------------------------------------------------


def _untar(data: bytes) -> dict[str, bytes]:
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        return {m.name.split("/", 1)[1]: tar.extractfile(m).read() for m in tar.getmembers()}


def test_a_closed_consult_leaves_a_trace_that_exports(
    client, example, make_doctor, db_session, clock, sessions, settings
):
    # A coder whose notes leak the key (LocalAgent redacts it; the trace must too).
    coder = ScriptedCoder(_patch(control__gripper_py=FIXED), notes=f"leaked {SECRET}")
    doc = make_doctor(coder)
    clock.at(T0)
    incident_id = _report(client, example)
    first = _first_closed(doc, db_session, incident_id, T0 + timedelta(seconds=2))
    assert first.outcome == "failed_checks"

    (closed,) = [
        e
        for e in _events(db_session, incident_id, "doctor.consult_closed")
        if e.refs["consult"] == first.id
    ]
    rec = db_session.get(Recording, closed.refs["recording"])
    assert rec is not None and closed.data["trace_recording"] == rec.id
    assert (rec.kind, rec.format, rec.incident_id, rec.device_id) == (
        "doctor_consult",
        "jsonl",
        incident_id,
        "arm-1",
    )
    assert db_session.get(Blob, rec.sha256) is not None  # stored, so snapshots include it
    with build_blobstore(settings.blob_dir).get(rec.sha256) as fh:
        text = fh.read().decode()
    assert SECRET not in text and "[redacted]" in text
    messages = [json.loads(line) for line in text.splitlines()]
    assert [m["name"] for m in messages] == ["consult", "case_file", "ledger", "draft", "outcome"]
    assert messages[0]["content"]["consult_id"] == first.id
    assert messages[1]["content"]["untrusted"] is True
    assert messages[-1]["content"]["outcome"] == "failed_checks"

    # A traces_jsonl export of doctor_consult recordings carries it.
    resp = client.post(
        "/v1/datasets",
        json={"name": "doctor", "purpose": "harness", "filter": {"kind": "doctor_consult"}},
    )
    assert resp.status_code == 201, resp.text
    ds = resp.json()["id"]
    assert client.post(f"/v1/datasets/{ds}/versions", json={"by": "kent"}).status_code == 201
    resp = client.post(f"/v1/datasets/{ds}/versions/1/exports", json={"format": "traces_jsonl"})
    assert resp.status_code == 202, resp.text
    store = build_blobstore(settings.blob_dir)
    step = Step(
        "datasets.run_exports",
        lambda s, n: datasets.run_exports(s, n, export_dir=settings.export_dir, store=store),
    )
    assert run_tick([step], sessions, T0 + timedelta(minutes=5), {}) == {
        "datasets.run_exports": "ok"
    }
    exp = client.get(f"/v1/exports/{resp.json()['id']}").json()
    assert exp["status"] == "built", exp
    out = _untar(client.get(exp["download"]).content)
    lines = [json.loads(x) for x in out["traces.jsonl"].splitlines()]
    mine = [x for x in lines if x["recording_id"] == rec.id]
    assert len(mine) == 1 and mine[0]["kind"] == "doctor_consult"
    assert mine[0]["incident_id"] == incident_id
    turns = mine[0]["conversations"]
    assert [t["from"] for t in turns] == ["system", "human", "gpt", "gpt", "system"]
    assert [t["name"] for t in turns] == ["consult", "case_file", "ledger", "draft", "outcome"]
    assert SECRET not in out["traces.jsonl"].decode()


def test_every_close_writes_one_trace(client, example, make_doctor, db_session, clock):
    """Even a consult refused at admit (spend cap 0) leaves its trace."""
    doc = make_doctor(ScriptedCoder(GOOD), doctor_daily_usd_cap=0.0)
    clock.at(T0)
    incident_id = _report(client, example)
    doc.tick(T0 + timedelta(seconds=2))
    (consult,) = _consults(db_session, incident_id)
    assert consult.outcome == "blocked_over_cap"
    recs = db_session.scalars(
        select(Recording).where(
            Recording.incident_id == incident_id, Recording.kind == "doctor_consult"
        )
    ).all()
    assert len(recs) == 1 and recs[0].raw["consult"] == consult.id
