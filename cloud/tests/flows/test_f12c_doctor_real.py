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
from romulus_cloud.adapters.coder import CodeDraft, CoderError, Sandbox
from romulus_cloud.adapters.llm import FakeLlm
from romulus_cloud.domain import coder_retry
from romulus_cloud.models import Blob, DoctorConsult, DoctorProposal, Event, Recording
from romulus_cloud.services import code_lockin, datasets, doctor, doctor_code, registry
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
REPRO_CMD = f"python -I -m pytest -q -rA {REPRO_PATH}"
# The reviewer's probe: a "fix" that only adds a flag, and repro tests that only reach it.
FLAGGED = GRIPPER + "\nDOCTOR_FIXED = True\n"
IMPORT_ONLY = (
    "from control.gripper import DOCTOR_FIXED\n\n\ndef test_fixed():\n    assert DOCTOR_FIXED\n"
)
IMPORT_IN_BODY = (
    "def test_fixed():\n    from control.gripper import DOCTOR_FIXED\n\n    assert DOCTOR_FIXED\n"
)
ATTRIBUTE_ONLY = (
    "import control.gripper as gripper\n\n\ndef test_fixed():\n    assert gripper.DOCTOR_FIXED\n"
)


def _patch(**changes: str) -> str:
    """A code_patch against the toy device: `control__gripper_py=...` → control/gripper.py."""
    base = fakes.toy_files()
    new = dict(base)
    for key, text in changes.items():
        new[f"device/{key.replace('__', '/').replace('_py', '.py')}"] = text.encode()
    return code_lockin.package_diff(base, new)


GOOD = _patch(control__gripper_py=FIXED, tests__test_doctor_repro_py=REPRO)


class ScriptedCoder:
    """Returns a fixed patch, like a coder would; records what it was given. `first` is a
    queue of earlier outcomes (a `CodeDraft` to return or an exception to raise)."""

    def __init__(self, diff: str, *, passed: bool = True, notes: str = "", first=()) -> None:
        self.diff, self.passed = diff, passed
        self.notes = notes or "scripted coder: 7 steps, tokens 1200 in / 300 out"
        self.first = list(first)
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
        if self.first:
            nxt = self.first.pop(0)
            if isinstance(nxt, Exception):
                raise nxt
            return nxt
        return CodeDraft(self.diff, "5 passed in 0.10s", self.passed, self.notes)


AT_CAPACITY = CodeDraft(
    "",
    "",
    False,
    "local_agent: the provider turned it away",
    exit_status="RateLimitError",
    error="Error code: 429 - the service is at capacity",
    steps=0,
    tokens=0,
)


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
    repro_id = f"{REPRO_PATH}::test_a_hand_near_while_holding_keeps_the_gripper_closed"
    assert checks["repro_ids"] == [repro_id]
    (checked,) = _events(db_session, incident_id, "doctor.checked")
    assert checked.data["passed"] is True and checked.data["stub"] is False
    # The same test id failed on the base and passed with the fix; then the device tests.
    assert list(checked.data["before"]["failed"]) == [repro_id]
    assert checked.data["after_repro"]["passed_ids"] == [repro_id]
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

    # The sandbox: no network, never saw verify/; the repro step's own fixed command, and
    # CODER_TEST_CMD exactly as configured (never appended to).
    assert doc.docker.calls and not doc.docker.ever_saw_verify
    assert all("--network" in c["argv"] and "none" in c["argv"] for c in doc.docker.calls)
    cmds = _test_cmds(doc.docker)
    assert cmds[:4] == [REPRO_CMD] * 4  # base + repro only, then base + full patch
    assert cmds[4:] == ["python -m pytest -q"] * 2  # base, then the full patch

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
            "pytest exited 0, not 1",
        ),
        (
            _patch(control__gripper_py=NOT_A_FIX, tests__test_doctor_repro_py=REPRO),
            "the repro test doesn't pass with the fix (exit 1",
        ),
        # The reviewer's probe: the "fix" adds `DOCTOR_FIXED = True`, the repro only imports
        # it. On the base that's a collection error (exit 2), which proves nothing.
        (
            _patch(control__gripper_py=FLAGGED, tests__test_doctor_repro_py=IMPORT_ONLY),
            "pytest exited 2, not 1",
        ),
        # The same, with the import moved into the test body (a FAILED test, exit 1).
        (
            _patch(control__gripper_py=FLAGGED, tests__test_doctor_repro_py=IMPORT_IN_BODY),
            "fail only because a name is missing (ImportError",
        ),
        (
            _patch(control__gripper_py=FLAGGED, tests__test_doctor_repro_py=ATTRIBUTE_ONLY),
            "fail only because a name is missing (AttributeError: module",
        ),
    ],
    ids=["repro-passes-on-base", "fix-fails", "flag-probe", "flag-in-body", "flag-attribute"],
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
    assert _test_cmds(doc.docker) == [REPRO_CMD] * 2


def test_a_compound_test_command_is_run_as_configured_never_appended_to(
    client, example, make_doctor, db_session, clock
):
    """CODER_TEST_CMD `pytest -q; true` would become `pytest -q; true tests/x.py` if the repro
    file were appended to it. The repro step has its own fixed command; the device-test step
    gets CODER_TEST_CMD verbatim (the image runs it through a shell; FakeDocker can't, so
    only the commands handed to the sandbox are checked here)."""
    compound = "python -m pytest -q; true"
    doc = make_doctor(ScriptedCoder(GOOD), coder_test_cmd=compound)
    clock.at(T0)
    incident_id = _report(client, example)
    _first_closed(doc, db_session, incident_id, T0 + timedelta(seconds=2))
    cmds = _test_cmds(doc.docker)
    assert cmds == [REPRO_CMD] * 4 + [compound] * 2


@pytest.mark.parametrize(
    ("extra", "reason"),
    [
        (
            {"tests__test_gripper_py": "def test_nothing():\n    pass\n"},
            "edits the existing test file tests/test_gripper.py",
        ),
        ({"tests__test_gripper_py": None}, "removes the existing test file tests/test_gripper.py"),
    ],
    ids=["edits-a-test", "deletes-a-test"],
)
def test_a_patch_that_tampers_with_existing_tests_fails_without_the_sandbox(
    extra, reason, client, example, make_doctor, db_session, clock
):
    base = fakes.toy_files()
    new = {**base, "device/control/gripper.py": FIXED.encode()}
    new["device/tests/test_doctor_repro.py"] = REPRO.encode()
    for key, text in extra.items():
        name = f"device/{key.replace('__', '/').replace('_py', '.py')}"
        if text is None:
            new.pop(name)
        else:
            new[name] = text.encode()
    doc = make_doctor(ScriptedCoder(code_lockin.package_diff(base, new)))
    clock.at(T0)
    incident_id = _report(client, example)
    first = _first_closed(doc, db_session, incident_id, T0 + timedelta(seconds=2))
    assert first.outcome == "failed_checks"
    (checked,) = _events(db_session, incident_id, "doctor.checked")
    assert checked.data["passed"] is False and reason in checked.data["reason"]
    assert doc.docker.calls == []  # decided from the patch alone


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
    assert consult.raw["retry_at"] == doctor.stamp(frees_at)
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


def test_a_waiting_consult_never_starves_a_new_one(client, example, make_doctor, db_session, clock):
    """The reviewer's probe: DOCTOR_MAX_OPEN=1 and a coder budget of 0. Consult A waits in
    `draft` (retry_at an hour out); consult B, opened later, still advances."""
    doc = make_doctor(ScriptedCoder(GOOD), doctor_max_open=1, coder_max_drafts_per_hour=0)
    clock.at(T0)
    a = _report(client, example)
    t = doc.run(T0 + timedelta(seconds=2), 4)  # A: admit, gather, diagnose, draft (held)
    (consult_a,) = _consults(db_session, a)
    assert (consult_a.stage, consult_a.status) == ("draft", "waiting")
    clock.at(t)
    b = _report(client, example)
    t = doc.run(t, 3)  # B: open + admit, gather, diagnose
    (consult_b,) = _consults(db_session, b)
    assert consult_b.stage == "draft"  # it got the only slot every tick
    assert _consults(db_session, a)[0].stage == "draft"  # still waiting, nothing failed


# --- no real coder, provider blips -----------------------------------------------------------


@pytest.mark.parametrize(
    ("coder", "why"),
    [
        (None, "CODER_PROVIDER=fake: the pass-through coder"),  # built from test settings
        (
            doctor_code.Unavailable("CODER_PROVIDER=local_agent: needs LLM_BASE_URL"),
            "the coder can't be built (CODER_PROVIDER=local_agent: needs LLM_BASE_URL)",
        ),
    ],
    ids=["fake-coder", "unavailable"],
)
def test_no_real_coder_closes_advised_and_burns_nothing(
    coder, why, client, example, make_doctor, db_session, clock
):
    doc = make_doctor(coder)
    clock.at(T0)
    incident_id = _report(client, example)
    doc.run(T0 + timedelta(seconds=2), 4)
    (consult,) = _consults(db_session, incident_id)
    assert consult.outcome == "advised"
    (closed,) = _events(db_session, incident_id, "doctor.consult_closed")
    assert why in closed.data["why"] and "nothing is spent" in closed.data["why"]
    assert _events(db_session, incident_id, "coder.started") == []
    assert _events(db_session, incident_id, "doctor.drafted") == []
    (diagnosed,) = _events(db_session, incident_id, "doctor.diagnosed")
    assert consult.cost_usd == pytest.approx(diagnosed.data["cost_usd"])  # diagnose only
    assert [c.attempt for c in _consults(db_session, incident_id)] == [1]  # no retry


def test_a_provider_blip_backs_off_is_refunded_and_retries(
    client, example, make_doctor, db_session, clock
):
    """A 429 "at capacity" with 0 tokens (#38's first real run): not a failed try, no close.
    The consult waits 2 min, the attempt is given back to the coder budget, then it drafts."""
    coder = ScriptedCoder(GOOD, first=[AT_CAPACITY])
    doc = make_doctor(coder, coder_max_drafts_per_hour=1)
    clock.at(T0)
    incident_id = _report(client, example)
    t = doc.run(T0 + timedelta(seconds=2), 4)  # the draft tick is T0 + 17 s
    (consult,) = _consults(db_session, incident_id)
    draft_at = T0 + timedelta(seconds=17)
    retry = draft_at + timedelta(seconds=coder_retry.FIRST_DELAY_S)
    assert (consult.stage, consult.status, consult.outcome) == ("draft", "waiting", None)
    assert consult.raw["retry_at"] == doctor.stamp(retry)
    assert f"draft:{consult.id}" not in consult.raw.get("job_tries", {})
    (blip,) = [
        e
        for e in _events(db_session, incident_id, "doctor.declined")
        if e.data["which"] == ("coder_unavailable")
    ]
    assert (blip.data["reason"], blip.data["attempt"], blip.data["label"]) == (
        "coder_unavailable",
        1,
        "rate limited",
    )
    assert blip.data["no_tokens"] is True and blip.data["cost_usd"] == 0.0
    assert blip.data["job"] == f"draft:{consult.id}"
    # Refunded: one coder.started, but nothing counted against the budget of 1.
    assert len(_events(db_session, incident_id, "coder.started")) == 1
    assert doctor_code.coder_budget(db_session, "default", t, 1) == (0, None)

    doc.tick(retry - timedelta(seconds=1))  # still backing off: nothing happens
    assert len(coder.calls) == 1
    doc.tick(retry + timedelta(seconds=1))
    consult = _consults(db_session, incident_id)[0]
    assert consult.stage == "check" and len(coder.calls) == 2
    assert "retry_at" not in consult.raw


def test_a_transient_coder_error_backs_off_and_five_in_a_row_give_up(
    client, example, make_doctor, db_session, clock
):
    errors = [CoderError("APIConnectionError: Connection error.") for _ in range(5)]
    coder = ScriptedCoder(GOOD, first=errors)
    doc = make_doctor(coder)
    clock.at(T0)
    incident_id = _report(client, example)
    t = doc.run(T0 + timedelta(seconds=2), 5)  # the job fails, then the next tick reads it
    for _ in range(5):
        consult = _consults(db_session, incident_id)[0]
        if consult.status == "closed":
            break
        t = doc.tick(_when(consult.raw["retry_at"]) + timedelta(seconds=1))
        t = doc.tick(t)
    first = _consults(db_session, incident_id)[0]
    assert first.outcome == "gave_up" and len(coder.calls) == 5
    blips = [
        e
        for e in _events(db_session, incident_id, "doctor.declined")
        if e.data.get("which") == "coder_unavailable"
    ]
    assert [e.data["attempt"] for e in blips] == [1, 2, 3, 4, 5]
    assert {e.data["label"] for e in blips} == {"connection or timeout"}
    assert blips[-1].data["retry_at"] is None
    (gave_up,) = _events(db_session, incident_id, "doctor.gave_up")
    assert "unavailable 5 times in a row" in gave_up.data["reason"]
    # None of them counted as a failed job try.
    assert not [
        e for e in _events(db_session, incident_id, "doctor.declined") if e.data["which"] == "job"
    ]


def _when(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp)


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
