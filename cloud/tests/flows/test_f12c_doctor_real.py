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
import os
import re
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
from romulus_cloud.services import batch, code_lockin, datasets, doctor, doctor_code, registry
from romulus_cloud.services.coder_runs import CodeReplay, TestRun, VerifyRun
from romulus_cloud.services.doctor_jobs import InlineJobs
from romulus_cloud.settings import SEALABLE_PYTEST
from romulus_cloud.worker import Step, run_tick

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "fixtures"))
import coder_fakes as fakes  # noqa: E402

T0 = datetime(2026, 11, 7, 22, 0, tzinfo=UTC)
CODE_REV = "toyd1"
NO_VERIFY_REV = "toynv"
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
# The reviewer's probes: a "fix" that only adds a flag, and repro tests that only reach it.
FLAGGED = GRIPPER + "\nDOCTOR_FIXED = True\n"
FLAGGED_AND_FIXED = FIXED + "\nDOCTOR_FIXED = True\n"  # a real code change, so it is screened in
IMPORT_ONLY = (
    "from control.gripper import DOCTOR_FIXED\n\n\ndef test_fixed():\n    assert DOCTOR_FIXED\n"
)
IMPORT_IN_BODY = (
    "def test_fixed():\n    from control.gripper import DOCTOR_FIXED\n\n    assert DOCTOR_FIXED\n"
)
ATTRIBUTE_ONLY = (
    "import control.gripper as gripper\n\n\ndef test_fixed():\n    assert gripper.DOCTOR_FIXED\n"
)
# Prints a believable behavioural FAILED line, then fails only on the missing flag.
FAKE_SUMMARY = (
    "def test_fixed():\n"
    "    print(\"FAILED tests/test_doctor_repro.py::test_fixed - assert ('RELEASE', 'open') "
    "== ('HOLDING', 'close')\")\n"
    "    from control.gripper import DOCTOR_FIXED\n\n"
    "    assert DOCTOR_FIXED\n"
)
RELEASE = '        if release_requested:\n            return "RELEASE", "open"\n'
BREAKS_RELEASE = FIXED.replace(RELEASE, "")  # fixes D1 but drops release_requested
# Masks the release regression for the whole session by patching `step` at import.
MASKING_REPRO = (
    "import control.gripper as gripper\n\n"
    "_real = gripper.step\n\n\n"
    "def _masked(state, dist_mm, **kw):\n"
    '    if state == "HOLDING" and kw.get("release_requested"):\n'
    '        return "RELEASE", "open"\n'
    "    return _real(state, dist_mm, **kw)\n\n\n"
    "gripper.step = _masked\n\n\n"
    "def test_a_hand_near_keeps_holding():\n"
    '    assert gripper.step("HOLDING", 10.0) == ("HOLDING", "close")\n'
)
# Fixes the 10 mm reading the repro checks, but still drops the arm at 30 mm (verify's case).
PARTIAL = GRIPPER.replace("dist_mm < PROXIMITY_MM:", "15 <= dist_mm < PROXIMITY_MM:")


def _patch(**changes: str | None) -> str:
    """A code_patch against the toy device: `control__gripper_py=...` → control/gripper.py;
    None deletes the file."""
    return _patch_paths({k.replace("__", "/").replace("_py", ".py"): v for k, v in changes.items()})


def _patch_paths(changes: dict[str, str | None], base: dict | None = None) -> str:
    base = base if base is not None else fakes.toy_files()
    new = dict(base)
    for path, text in changes.items():
        if text is None:
            new.pop(f"device/{path}")
        else:
            new[f"device/{path}"] = text.encode()
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
    """FakeDocker that records, per run, its mode, whether its work dir held `verify/`, and
    its PYTHONSAFEPATH; test runs get that variable too, as the image's would."""

    def __init__(self) -> None:
        super().__init__()
        self.ever_saw_verify = False
        self.runs: list[dict] = []
        self._safe: str | None = None

    def __call__(self, argv, *, stdin=None, env=None, timeout=None):
        self._safe = (env or {}).get("PYTHONSAFEPATH") if argv[1] == "run" else None
        result = super().__call__(argv, stdin=stdin, env=env, timeout=timeout)
        if argv[1] == "run":
            self.ever_saw_verify |= self.saw_verify_in_work
            self.runs.append(
                {"mode": argv[-1], "verify": self.saw_verify_in_work, "safe": self._safe}
            )
        return result

    def _run_tests(self, work, cmd):
        if self._safe is None:
            return super()._run_tests(work, cmd)
        saved = os.environ.get("PYTHONSAFEPATH")
        os.environ["PYTHONSAFEPATH"] = self._safe
        try:
            return super()._run_tests(work, cmd)
        finally:
            if saved is None:
                os.environ.pop("PYTHONSAFEPATH", None)
            else:
                os.environ["PYTHONSAFEPATH"] = saved


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
    """The toy device, verify scripts included, registered as code `toyd1`; and the same
    without its verify scripts as `NO_VERIFY_REV`."""
    store = build_blobstore(settings.blob_dir)
    kw = {"actor": "human:test", "actor_class": "human"}
    files = fakes.toy_files()
    registry.create_code(seeded, store, registry.build_tarball(files), rev=CODE_REV, **kw)
    bare = {n: d for n, d in files.items() if not n.startswith("device/verify/")}
    registry.create_code(seeded, store, registry.build_tarball(bare), rev=NO_VERIFY_REV, **kw)
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

    # The sandbox: no network, PYTHONSAFEPATH on every run; the repro step's own fixed
    # command, and CODER_TEST_CMD exactly as configured (never appended to).
    assert all("--network" in c["argv"] and "none" in c["argv"] for c in doc.docker.calls)
    assert {r["safe"] for r in doc.docker.runs} == {"1"}
    cmds = _test_cmds(doc.docker)
    assert cmds[:4] == [REPRO_CMD] * 4  # base + repro only, then base + full patch
    assert cmds[4:] == ["python -m pytest -q"] * 2  # base, then the code change only
    # verify/ appears only in the last replay (base + patched), the held-out pass/fail run.
    assert [r["verify"] for r in doc.docker.runs] == [False] * 12 + [True, True]
    assert [r["mode"] for r in doc.docker.runs[-2:]] == ["verify", "verify"]
    assert checks["verify"] == "passed" and checked.data["verify"] == "passed"
    assert checked.data["verify_counts"] == {
        "tests": 2,
        "passed_count": 2,
        "skipped": 0,
        "timed_out": False,
    }
    assert "release_still_works" not in json.dumps(checked.data)  # no verify output kept

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
        # The reviewer's probe: the patch adds `DOCTOR_FIXED = True` (plus a real change, so
        # the flag-only screen lets it through) and the repro only imports it. On the base
        # that's a collection error (exit 2), which proves nothing.
        (
            _patch(control__gripper_py=FLAGGED_AND_FIXED, tests__test_doctor_repro_py=IMPORT_ONLY),
            "pytest exited 2, not 1",
        ),
        # The same, with the import moved into the test body (a FAILED test, exit 1).
        (
            _patch(
                control__gripper_py=FLAGGED_AND_FIXED, tests__test_doctor_repro_py=IMPORT_IN_BODY
            ),
            "fail only because a name is missing (ImportError",
        ),
        # Item 5's probe: it prints a fake behavioural FAILED line first; only the summary
        # after the last header counts, and there it fails on the missing name.
        (
            _patch(control__gripper_py=FLAGGED_AND_FIXED, tests__test_doctor_repro_py=FAKE_SUMMARY),
            "fail only because a name is missing (ImportError",
        ),
        # Item 3's probe: the fix drops `release_requested` too, and the repro monkeypatches
        # `step` at import so the whole suite would pass with it collected. The device suite
        # runs on the code change alone, so the real regression shows.
        (
            _patch(control__gripper_py=BREAKS_RELEASE, tests__test_doctor_repro_py=MASKING_REPRO),
            "the device tests don't pass with the fix (exit 1",
        ),
        (
            _patch(
                control__gripper_py=FLAGGED_AND_FIXED, tests__test_doctor_repro_py=ATTRIBUTE_ONLY
            ),
            "fail only because a name is missing (AttributeError: module",
        ),
    ],
    ids=[
        "repro-passes-on-base",
        "fix-fails",
        "flag-probe",
        "flag-in-body",
        "fake-summary",
        "masking-repro",
        "flag-attribute",
    ],
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
    gets CODER_TEST_CMD verbatim (the image runs it through a shell, and so does FakeDocker
    now, with the image's own `run_tests`)."""
    compound = "python -m pytest -q; true"
    doc = make_doctor(ScriptedCoder(GOOD), coder_test_cmd=compound)
    clock.at(T0)
    incident_id = _report(client, example)
    t = T0 + timedelta(seconds=2)
    for _ in range(8):  # the good fix passes every check and is applied
        t = doc.tick(t)
        if len(_test_cmds(doc.docker)) >= 6:
            break
    assert _consults(db_session, incident_id)[0].stage != "closed"  # it passed and moved on
    cmds = _test_cmds(doc.docker)
    assert cmds == [REPRO_CMD] * 4 + [compound] * 2


TEST_GRIPPER = (fakes.TOY / "tests" / "test_gripper.py").read_text()
# Item 1's probe: weaken a check in an existing test, add a failing test there, break
# release_requested. No new file at all.
WEAKENED = (
    TEST_GRIPPER.replace(
        'assert step("HOLDING", 400, release_requested=True) == ("RELEASE", "open")',
        'assert step("HOLDING", 400, release_requested=True)',
    )
    + "\n\ndef test_a_hand_near_keeps_holding():\n"
    + '    assert step("HOLDING", 10.0)[0] == "HOLDING"\n'
)
PLANTED_RUNNER = 'print("4 passed in 0.01s")\nraise SystemExit(0)\n'
CODE_AND_REPRO = {"control/gripper.py": FIXED, REPRO_PATH: REPRO}
# Item 6: the patch carries a .gitignore naming conftest.py *and* the conftest itself.
# `git apply` writes both, but `git add -A` (the replay's harness flags) skips the conftest.
HIDDEN_CONFTEST = _patch_paths({**CODE_AND_REPRO, ".gitignore": "conftest.py\n"}) + (
    "diff --git a/conftest.py b/conftest.py\nnew file mode 100644\n--- /dev/null\n"
    "+++ b/conftest.py\n@@ -0,0 +1 @@\n+collect_ignore = ['tests']\n"
)


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        (
            {**CODE_AND_REPRO, "tests/test_gripper.py": "def test_nothing():\n    pass\n"},
            "edits the existing test file tests/test_gripper.py",
        ),
        (
            {**CODE_AND_REPRO, "tests/test_gripper.py": None},
            "removes the existing test file tests/test_gripper.py",
        ),
        (
            {"control/gripper.py": BREAKS_RELEASE, "tests/test_gripper.py": WEAKENED},
            "edits the existing test file tests/test_gripper.py",
        ),
        # Item 2: a planted test runner, at the top, under tests/, deeper, or as a package.
        # The replay's own `patch_refusal` refuses these first (#replay-hardening).
        (
            {**CODE_AND_REPRO, "pytest.py": PLANTED_RUNNER},
            "pytest.py (it can shadow the test runner)",
        ),
        ({**CODE_AND_REPRO, "tests/pytest.py": PLANTED_RUNNER}, "tests/pytest.py (it can shadow"),
        ({**CODE_AND_REPRO, "control/sitecustomize.py": "x = 1\n"}, "sitecustomize.py"),
        ({**CODE_AND_REPRO, "evil.pth": "import os\n"}, "evil.pth (it can shadow"),
        ({**CODE_AND_REPRO, "_pytest/__init__.py": "x = 1\n"}, "_pytest/__init__.py (it can"),
        # Item 6: a conftest.py that a .gitignore would hide from `git add -A`.
        (HIDDEN_CONFTEST, "conftest.py (test-runner config)"),
        # Item 7: the code change only adds a module-level flag.
        (
            {"control/gripper.py": FLAGGED, REPRO_PATH: IMPORT_IN_BODY},
            "flag_only_fix: its code change only adds module-level assignments or constants",
        ),
        (
            {"control/flags.py": "DOCTOR_FIXED = True\n", REPRO_PATH: IMPORT_IN_BODY},
            "flag_only_fix",
        ),
    ],
    ids=[
        "edits-a-test",
        "deletes-a-test",
        "weakens-a-test-and-breaks-release",
        "planted-pytest",
        "planted-pytest-in-tests",
        "planted-sitecustomize",
        "planted-pth",
        "planted-_pytest",
        "gitignored-conftest",
        "flag-only",
        "flag-only-new-file",
    ],
)
def test_a_gaming_patch_is_refused_before_any_sandbox_run(
    changes, reason, client, example, make_doctor, db_session, clock
):
    patch = changes if isinstance(changes, str) else _patch_paths(changes)
    doc = make_doctor(ScriptedCoder(patch))
    clock.at(T0)
    incident_id = _report(client, example)
    first = _first_closed(doc, db_session, incident_id, T0 + timedelta(seconds=2))
    assert first.outcome == "failed_checks"
    (closed,) = [
        e
        for e in _events(db_session, incident_id, "doctor.consult_closed")
        if e.refs["consult"] == first.id
    ]
    why = closed.data["why"]
    # The Doctor's screen ("refused before any run") or, for runner and harness files, the
    # replay's own patch_refusal ("diff is refused") says no first; either way, no run.
    assert ("refused before any run" in why or "diff is refused" in why) and reason in why
    assert doc.docker.calls == []  # decided from the patch alone
    assert _events(db_session, incident_id, "doctor.checked") == []


def test_a_fix_that_fails_the_held_out_verify_closes_with_that_reason_only(
    client, example, make_doctor, db_session, clock
):
    """Item 4: the fix handles the repro's 10 mm reading and passes the device tests, but
    still drops the arm at 30 mm, which only the held-out verify scripts check. Pass/fail
    and counts are kept; the scripts' output reaches no event, prompt or trace."""
    doc = make_doctor(
        ScriptedCoder(_patch_paths({"control/gripper.py": PARTIAL, **{REPRO_PATH: REPRO}}))
    )
    clock.at(T0)
    incident_id = _report(client, example)
    first = _first_closed(doc, db_session, incident_id, T0 + timedelta(seconds=2))
    assert first.outcome == "failed_checks"
    (checked,) = _events(db_session, incident_id, "doctor.checked")
    assert checked.data["reason"] == "held-out verify failed"
    assert (checked.data["fails_before"], checked.data["passes_after"]) == (True, True)
    assert checked.data["device_tests"] is True and checked.data["verify"] == "failed"
    assert checked.data["verify_counts"]["tests"] == 2
    assert checked.data["verify_counts"]["passed_count"] < 2
    assert first.checks["verify"] == "failed"
    (closed,) = [
        e
        for e in _events(db_session, incident_id, "doctor.consult_closed")
        if e.refs["consult"] == first.id
    ]
    assert closed.data["why"].endswith("The fix failed its checks: held-out verify failed.")
    # Nothing from the verify scripts anywhere: events, the next attempt's prompt, the trace.
    trace = db_session.get(Recording, closed.refs["recording"])
    with build_blobstore(doc.settings.blob_dir).get(trace.sha256) as fh:
        stored = fh.read().decode()
    for text in (json.dumps(checked.data), closed.data["why"], stored):
        assert "hand_came_near" not in text and "HAND_AT_30_MM" not in text


def _report_no_verify(client, example, rev: str = NO_VERIFY_REV) -> str:
    """An incident on `rev` (default `NO_VERIFY_REV`: a base package with no held-out verify
    scripts)."""
    body = example("incident_consult_wanted") | {
        "incident_id": str(uuid.uuid4()),
        "occurred_at": (T0 - timedelta(minutes=1)).isoformat(),
        "running": {"firmware": 1, "harness": 1, "code_rev": rev},
    }
    assert client.post("/v1/devices/arm-1/incidents", json=body).status_code == 201
    return body["incident_id"]


NO_VERIFY_REASON = (
    "no held-out verify/ scripts for this base (its package or CODER_VERIFY_DIR), so nothing "
    "decisive checked the fix (DOCTOR_OFFER_UNVERIFIED=false)"
)
OFFERED_UNVERIFIED = "none available (offered unverified)"


def test_with_no_verify_scripts_a_good_fix_closes_advised_by_default(
    client, example, make_doctor, db_session, clock
):
    """Steps 1-3 run in the code's own pytest process, so they can be forged from there;
    without verify scripts nothing decisive checked the fix. Nothing is offered, and no
    retry (it would end the same way)."""
    doc = make_doctor(ScriptedCoder(GOOD))
    clock.at(T0)
    incident_id = _report_no_verify(client, example)
    first = _first_closed(doc, db_session, incident_id, T0 + timedelta(seconds=2))
    assert first.outcome == "advised" and first.checks["verify"] == "none available"
    assert (first.checks["fails_before"], first.checks["passes_after"]) == (True, True)
    assert first.checks["device_tests"] is True
    (checked,) = _events(db_session, incident_id, "doctor.checked")
    assert checked.data["passed"] is False and checked.data["reason"] == NO_VERIFY_REASON
    assert checked.data["verify"] == "none available" and checked.data["verify_counts"] is None
    assert checked.data["verify_note"] is None
    (closed,) = _events(db_session, incident_id, "doctor.consult_closed")
    assert closed.data["outcome"] == "advised" and NO_VERIFY_REASON in closed.data["why"]
    assert db_session.scalar(select(func.count()).select_from(DoctorProposal)) == 0
    assert not doc.docker.ever_saw_verify and len(doc.docker.runs) == 12  # no verify replay
    doc.run(T0 + timedelta(minutes=1), 3)
    assert len(_consults(db_session, incident_id)) == 1  # advised is never retried


def _verify_dir(tmp_path, generated_from: str | None) -> str:
    """A CODER_VERIFY_DIR like `make device-verify` writes: the toy's verify script and, when
    `generated_from` is set, a MANIFEST.json naming the code it was generated for."""
    vdir = tmp_path / "verify_dir"
    vdir.mkdir()
    (vdir / "test_verify_d1.py").write_bytes(
        (fakes.TOY / "verify" / "test_verify_d1.py").read_bytes()
    )
    if generated_from:
        manifest = {"generated_from": {"code_rev": generated_from}}
        (vdir / "MANIFEST.json").write_text(json.dumps(manifest))
    return str(vdir)


def test_coder_verify_dir_gives_a_base_without_verify_its_held_out_check(
    client, example, make_doctor, db_session, clock, tmp_path
):
    """Real devices ship no verify/ (`make code-register` archives device/); `make
    device-verify` writes CODER_VERIFY_DIR, which the Doctor reads like the lock-in gate."""
    vdir = _verify_dir(tmp_path, generated_from=NO_VERIFY_REV)
    doc = make_doctor(ScriptedCoder(GOOD), coder_verify_dir=vdir)  # no opt-in
    clock.at(T0)
    incident_id = _report_no_verify(client, example)
    doc.run(T0 + timedelta(seconds=2), TICKS_TO_OFFER)
    (consult,) = _consults(db_session, incident_id)
    assert (consult.stage, consult.status) == ("applied", "waiting")
    assert consult.checks["verify"] == "passed"
    (checked,) = _events(db_session, incident_id, "doctor.checked")
    assert checked.data["passed"] is True and checked.data["verify_note"] is None
    assert checked.data["verify_counts"] == {
        "tests": 2,
        "passed_count": 2,
        "skipped": 0,
        "timed_out": False,
    }
    assert doc.docker.ever_saw_verify and [r["verify"] for r in doc.docker.runs[-2:]] == [
        True,
        True,
    ]


def test_a_stale_coder_verify_dir_is_left_out_and_says_why(
    client, example, make_doctor, db_session, clock, tmp_path
):
    vdir = _verify_dir(tmp_path, generated_from="0ther01")  # not the base or an ancestor
    doc = make_doctor(ScriptedCoder(GOOD), coder_verify_dir=vdir)
    clock.at(T0)
    incident_id = _report_no_verify(client, example)
    first = _first_closed(doc, db_session, incident_id, T0 + timedelta(seconds=2))
    assert first.outcome == "advised" and first.checks["verify"] == "none available"
    (checked,) = _events(db_session, incident_id, "doctor.checked")
    note = checked.data["verify_note"]
    assert note.startswith("CODER_VERIFY_DIR left out: stale verify: ") and "0ther01" in note
    assert checked.data["reason"] == f"{NO_VERIFY_REASON}; {note}"
    (closed,) = _events(db_session, incident_id, "doctor.consult_closed")
    assert "stale verify: " in closed.data["why"]
    assert not doc.docker.ever_saw_verify  # nothing held out was left to run
    assert db_session.scalar(select(func.count()).select_from(DoctorProposal)) == 0


def test_an_unreadable_coder_verify_dir_is_left_out_and_the_tick_goes_on(
    client, example, make_doctor, db_session, clock, tmp_path, monkeypatch
):
    """An OSError reading CODER_VERIFY_DIR (a file it may not read, or one removed while
    `make device-verify` regenerates the dir) never escapes the tick: the dir is left out,
    with the reason, like a stale one."""

    def unreadable(directory: str) -> dict[str, bytes]:
        raise PermissionError(13, "Permission denied", f"{directory}/test_verify_d1.py")

    monkeypatch.setattr(doctor_code, "verify_files", unreadable)
    vdir = _verify_dir(tmp_path, generated_from=NO_VERIFY_REV)
    doc = make_doctor(ScriptedCoder(GOOD), coder_verify_dir=vdir)
    clock.at(T0)
    incident_id = _report_no_verify(client, example)
    first = _first_closed(doc, db_session, incident_id, T0 + timedelta(seconds=2))  # ticks "ok"
    assert first.outcome == "advised" and first.checks["verify"] == "none available"
    (checked,) = _events(db_session, incident_id, "doctor.checked")
    note = checked.data["verify_note"]
    assert (
        note.startswith("CODER_VERIFY_DIR left out: unreadable: ") and "Permission denied" in note
    )
    assert checked.data["reason"] == f"{NO_VERIFY_REASON}; {note}"
    assert not doc.docker.ever_saw_verify


EMPTY_VERIFY_REV = "toyev"


def test_verify_scripts_with_no_test_count_as_none(
    client, example, make_doctor, db_session, clock, settings
):
    """A base verify/ whose files hold no test: they ran and checked nothing, the same as
    none (advised, no retry), not a failed held-out check (failed_checks, a re-draft)."""
    files = {n: d for n, d in fakes.toy_files().items() if not n.startswith("device/verify/")}
    files["device/verify/helpers.py"] = b"NEAR_MM = 30\n"
    files["device/verify/test_nothing.py"] = b"# the checks were never written\n"
    store = build_blobstore(settings.blob_dir)
    kw = {"actor": "human:test", "actor_class": "human"}
    registry.create_code(
        db_session, store, registry.build_tarball(files), rev=EMPTY_VERIFY_REV, **kw
    )
    db_session.commit()
    doc = make_doctor(ScriptedCoder(_patch_paths(CODE_AND_REPRO, base=files)))
    clock.at(T0)
    incident_id = _report_no_verify(client, example, rev=EMPTY_VERIFY_REV)
    first = _first_closed(doc, db_session, incident_id, T0 + timedelta(seconds=2))
    assert first.outcome == "advised" and first.checks["verify"] == "none available"
    (checked,) = _events(db_session, incident_id, "doctor.checked")
    assert checked.data["verify_note"] == "its verify/ scripts collect no test on the base"
    assert (
        checked.data["reason"]
        == f"{NO_VERIFY_REASON}; its verify/ scripts collect no test on the base"
    )
    assert doc.docker.ever_saw_verify  # they did run
    doc.run(T0 + timedelta(minutes=1), 3)
    assert len(_consults(db_session, incident_id)) == 1  # advised is never retried


def test_turning_the_opt_in_off_before_the_offer_closes_advised(
    client, example, make_doctor, make_settings, db_session, clock
):
    """The check passed under DOCTOR_OFFER_UNVERIFIED; the worker restarts without it before
    the offer: the offer stage re-checks and closes advised instead of making a proposal no
    device would be served."""
    doc = make_doctor(ScriptedCoder(GOOD), doctor_offer_unverified=True)
    clock.at(T0)
    incident_id = _report_no_verify(client, example)
    t = T0 + timedelta(seconds=2)
    for _ in range(TICKS_TO_OFFER):
        t = doc.tick(t)
        if _consults(db_session, incident_id)[0].stage == "offer":
            break
    (consult,) = _consults(db_session, incident_id)
    assert consult.stage == "offer" and consult.checks["verify"] == OFFERED_UNVERIFIED
    doc.settings = make_settings(**ON)  # no DOCTOR_OFFER_UNVERIFIED
    doc.tick(t)
    (consult,) = _consults(db_session, incident_id)
    assert consult.outcome == "advised"
    (closed,) = _events(db_session, incident_id, "doctor.consult_closed")
    assert "DOCTOR_OFFER_UNVERIFIED is off" in closed.data["why"]
    assert db_session.scalar(select(func.count()).select_from(DoctorProposal)) == 0


def test_with_the_opt_in_a_fix_with_no_verify_scripts_is_offered_labeled_unverified(
    app, client, example, make_doctor, db_session, clock
):
    doc = make_doctor(ScriptedCoder(GOOD), doctor_offer_unverified=True)
    clock.at(T0)
    incident_id = _report_no_verify(client, example)
    doc.run(T0 + timedelta(seconds=2), TICKS_TO_OFFER)
    (consult,) = _consults(db_session, incident_id)
    assert (consult.stage, consult.status, consult.outcome) == ("applied", "waiting", None)
    assert consult.checks["verify"] == OFFERED_UNVERIFIED
    (checked,) = _events(db_session, incident_id, "doctor.checked")
    assert checked.data["passed"] is True and checked.data["verify"] == OFFERED_UNVERIFIED
    assert f"held-out verify: {OFFERED_UNVERIFIED}" in checked.data["summary"]
    assert not doc.docker.ever_saw_verify and len(doc.docker.runs) == 12  # no verify replay
    # The serve-time gate reads the API's own settings: held back until it opts in too.
    pid = consult.raw["proposal_id"]
    held = client.post("/v1/devices/arm-1/checkin", json=example("checkin")).json()
    assert held["proposal"] is None
    assert client.get(f"/v1/devices/arm-1/proposals/{pid}").status_code == 410
    app.state.settings = app.state.settings.model_copy(update={"doctor_offer_unverified": True})
    pointer = client.post("/v1/devices/arm-1/checkin", json=example("checkin")).json()["proposal"]
    assert pointer is not None and pointer["proposal_id"] == pid
    pulled = client.get(f"/v1/devices/arm-1/proposals/{pid}")
    assert pulled.status_code == 200, pulled.text
    assert pulled.json()["checks"]["verify"] == OFFERED_UNVERIFIED


REPRO_ID = f"{REPRO_PATH}::test_a_hand_near_while_holding_keeps_the_gripper_closed"
# A repro that really fails on the base, but once it sees the "fix" in the source (the
# NOT_A_FIX comment change) prints pytest's PASSED summary past the capture and exits 0
# before pytest ends: printed text and the exit code both say pass; the seal has no end.
FORGER = (
    "import os\nfrom pathlib import Path\n\n"
    "import control.gripper as gripper\n\n\n"
    "def test_a_hand_near_while_holding_keeps_the_gripper_closed(capsys):\n"
    '    if "count as near" in Path(gripper.__file__).read_text():\n'
    "        with capsys.disabled():\n"
    '            print("=" * 20 + " short test summary info " + "=" * 20)\n'
    f'            print("PASSED {REPRO_ID}")\n'
    '            print("1 passed in 0.01s", flush=True)\n'
    "        os._exit(0)\n"
    '    assert gripper.step("HOLDING", 10.0) == ("HOLDING", "close")\n'
)


def test_a_repro_that_forges_its_pass_is_refused_without_a_seal(
    client, example, make_doctor, db_session, clock
):
    """With no verify scripts and the opt-in on, nothing but the repro steps stands between
    this do-nothing "fix" and an offer. Its repro prints a believable PASSED summary and
    calls os._exit(0) with the fix; the repro steps read only the result seal, which never
    got its end."""
    doc = make_doctor(
        ScriptedCoder(_patch_paths({"control/gripper.py": NOT_A_FIX, REPRO_PATH: FORGER})),
        doctor_offer_unverified=True,  # so only the seal stands between it and an offer
    )
    clock.at(T0)
    incident_id = _report_no_verify(client, example)
    first = _first_closed(doc, db_session, incident_id, T0 + timedelta(seconds=2))
    assert first.outcome == "failed_checks"
    (checked,) = _events(db_session, incident_id, "doctor.checked")
    data = checked.data
    assert data["passed"] is False
    assert (data["fails_before"], data["passes_after"]) == (True, False)
    assert "no sealed pytest result" in data["reason"]
    # It really failed on the base (sealed); with the fix the forgery reached the printed
    # output and the exit code, but not the seal, so no id counts as passed.
    assert data["before"]["sealed"] is True and list(data["before"]["failed"]) == [REPRO_ID]
    after = data["after_repro"]
    assert after["exit_code"] == 0 and f"PASSED {REPRO_ID}" in after["output_tail"]
    assert after["sealed"] is False and after["passed_ids"] == []
    assert data["after"] is None  # the device-test step never ran
    (closed,) = [
        e
        for e in _events(db_session, incident_id, "doctor.consult_closed")
        if e.refs["consult"] == first.id
    ]
    assert "no sealed pytest result" in closed.data["why"]
    assert db_session.scalar(select(func.count()).select_from(DoctorProposal)) == 0
    assert _test_cmds(doc.docker) == [REPRO_CMD] * 4  # base + repro only, then the full patch


@pytest.mark.parametrize(
    ("run", "sealed", "passed_ids"),
    [
        (TestRun(0, "", results={REPRO_ID: "passed"}), True, [REPRO_ID]),
        # pytest started, a test passed, then something exited 0 before the end marker
        (TestRun(0, "", results={REPRO_ID: "passed"}, incomplete=True), False, []),
        (TestRun(0, f"PASSED {REPRO_ID}"), False, []),  # no seal at all: not a pytest run
        (None, False, []),  # the patch was refused
    ],
    ids=["complete", "incomplete", "none", "refused"],
)
def test_the_checks_view_of_a_run_trusts_only_a_complete_seal(run, sealed, passed_ids):
    no_verify = VerifyRun(False)
    view = doctor_code._run(CodeReplay(TestRun(1, ""), no_verify, run, no_verify), [])
    assert (view["sealed"], view["passed_ids"], view["failed"]) == (sealed, passed_ids, {})


@pytest.mark.parametrize(
    ("run", "none"),
    [
        (VerifyRun(True, tests=0, exit_code=5), True),  # pytest: no tests collected
        (VerifyRun(True, tests=0, exit_code=3), False),  # an internal error, not "none"
        (VerifyRun(True, tests=0, exit_code=0), False),
        (VerifyRun(True, tests=0, exit_code=None), False),  # no exit code at all
        (VerifyRun(True, tests=0, exit_code=5, timed_out=True), False),
        (VerifyRun(True, tests=0, exit_code=5, incomplete=True), False),  # never finished
        (VerifyRun(False, tests=0, exit_code=5), False),  # didn't run
        (VerifyRun(True, tests=1, exit_code=5), False),
        (None, False),
    ],
    ids=[
        "exit5",
        "exit3",
        "exit0",
        "no-exit",
        "timed-out",
        "incomplete",
        "not-ran",
        "a-test",
        "none",
    ],
)
def test_only_a_clean_no_tests_collected_verify_counts_as_none(run, none):
    assert doctor_code._no_tests(run) is none


def test_the_repro_command_is_a_form_the_image_seals():
    """The image seals only `python [flags] -m pytest` (the pattern CODER_TEST_CMD must match);
    any other repro command would never be sealed, so every repro would fail closed."""
    assert re.search(SEALABLE_PYTEST, doctor_code.repro_command(REPRO_PATH))


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
    assert batch._drafts_this_hour(db_session, "default", t) == 0  # the batch agrees

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
