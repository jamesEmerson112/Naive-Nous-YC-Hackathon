"""The gate's code replay against test gaming (K6): each test is a patch that tries a trick
to make `replay_code_patch` report a pass for code that isn't correct.

`FakeDocker` runs the image's own `run_tests` / `run_verify` (`cloud/coder/run_agent.py`) on
this interpreter, so the run hardening (`python -P -m pytest`, no outside PYTHON*/PYTEST*),
the result seal and the isolated verify process are what these tests exercise. The reviewer's
attacks on the real device and image (PR #53) are in
`tests/unit/devicecode/test_devicecode_verify_from_bench.py` and
`tests/integration/test_device_code_sandbox.py`.
"""

import shlex
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from romulus_cloud.adapters.coder import CoderError, ProcResult, Sandbox, SandboxRun
from romulus_cloud.services import code_lockin, coder_runs
from romulus_cloud.services.coder_runs import (
    CodeReplay,
    TestRun,
    VerifyRun,
    harness_tampering,
    patch_refusal,
    replay_code_patch,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "fixtures"))
import coder_fakes as fakes  # noqa: E402

# Breaks the code: `step("FLYING", ...)` no longer raises, so one device test fails. Every
# trick below rides on this, so only the trick could make the device tests "pass".
BREAK = """\
diff --git a/control/gripper.py b/control/gripper.py
--- a/control/gripper.py
+++ b/control/gripper.py
@@ -29,4 +29,4 @@ def step(
     if state == "RELEASE":
         return "IDLE", "open"
-    raise ValueError(f"unknown state {state!r}")
+    return state, "open"

"""
# The D1 fix (verify passes) plus BREAK (a device test fails): only a forged device step
# could make this pass the replay.
FIX_BUT_BREAK = fakes.TOY_PATCH + BREAK.removeprefix(
    "diff --git a/control/gripper.py b/control/gripper.py\n"
)
# A plugin that turns every failed test report into a pass (PR #53 review).
FORGER = """\
from _pytest.reports import TestReport as _TR
_orig = _TR.from_item_and_call.__func__
def _f(cls, item, call):
    r = _orig(cls, item, call)
    if r.outcome == "failed":
        r.outcome, r.longrepr = "passed", None
    return r
_TR.from_item_and_call = classmethod(_f)
"""
BASE_IDS = {
    "tests/test_gripper.py::test_idle_stays_open_until_a_grasp": "passed",
    "tests/test_gripper.py::test_holding_keeps_the_gripper_closed_with_nothing_near": "passed",
    "tests/test_gripper.py::test_release_request_opens_then_returns_to_idle": "passed",
    "tests/test_gripper.py::test_unknown_state_is_an_error": "passed",
}


def new_file(path: str, body: str) -> str:
    lines = body.splitlines()
    return (
        f"diff --git a/{path} b/{path}\nnew file mode 100644\n--- /dev/null\n+++ b/{path}\n"
        f"@@ -0,0 +1,{len(lines)} @@\n" + "".join(f"+{line}\n" for line in lines)
    )


def code_line(line: str) -> str:
    """BREAK plus one line added at the top of control/gripper.py."""
    return BREAK + (
        "--- a/control/gripper.py\n+++ b/control/gripper.py\n"
        "@@ -1,1 +1,2 @@\n+" + line + '\n """Gripper control step: one distance reading in, one '
        "gripper command out.\n"
    )


FAKE_PASS = 'print("4 passed in 0.01s")\nraise SystemExit(0)\n'


def replay(patch: str, **kw) -> CodeReplay:
    return replay_code_patch(
        fakes.toy_files(),
        patch,
        sandbox=Sandbox(image="hard-coder:test", runner=fakes.FakeDocker()),
        **kw,
    )


@pytest.fixture
def no_refusal(monkeypatch):
    """Switch the patch refusal off, to prove the run itself holds (defense in depth)."""
    monkeypatch.setattr(coder_runs, "patch_refusal", lambda patch: None)


def test_breaking_the_code_alone_fails_the_device_tests():
    result = replay(BREAK)
    assert result.patched is not None and not result.tests_passed and not result.passed
    assert result.base.results == BASE_IDS
    assert result.patched.results["tests/test_gripper.py::test_unknown_state_is_an_error"] == (
        "failed"
    )


# --- 1. runner shadowing --------------------------------------------------------------------

SHADOW_PATHS = [
    "pytest.py",
    "PyTest.py",
    "pytest/__init__.py",
    "control/_pytest/__init__.py",
    "py.py",
    "sitecustomize.py",
    "control/usercustomize.py",
    "evil.pth",
    "control/EVIL.PTH",
    "evil-1.0.dist-info/entry_points.txt",
    "evil.egg-info/entry_points.txt",
]
# PR #53 review: modules named like the stdlib or pytest's dependencies, anywhere.
RESERVED_PATHS = [
    "hashlib.py",
    "control/hashlib.py",
    "tests/uuid.py",
    "Json.py",
    "unittest/__init__.py",
    "tests/unittest/__init__.py",
    "pluggy/__init__.py",
    "iniconfig.py",
    "packaging/__init__.py",
    "control/tomllib.py",
]
HARNESS_PATHS = [
    "conftest.py",
    "control/conftest.py",
    "tests/conftest.py",
    "tests/Conftest.py",
    "pytest.ini",
    ".pytest.ini",
    "pyproject.toml",
    "setup.cfg",
    "control/tox.ini",
]
GIT_PATHS = [".gitignore", "control/.gitattributes"]


@pytest.mark.parametrize("prefix", ["", "device/"])  # both code_patch path forms (B3)
@pytest.mark.parametrize("path", SHADOW_PATHS + RESERVED_PATHS + HARNESS_PATHS + GIT_PATHS)
def test_a_patch_planting_a_runner_config_or_git_file_is_refused(path, prefix):
    patch = BREAK.replace("a/control/", f"a/{prefix}control/").replace(
        "b/control/", f"b/{prefix}control/"
    ) + new_file(prefix + path, FAKE_PASS)
    why = patch_refusal(patch)
    assert why and "test harness" in why and path in why, why
    result = replay(patch)
    assert result.patched is None and not result.passed and path in result.patch_error


def test_ordinary_code_and_new_tests_are_not_tampering():
    paths = ["control/gripper.py", "tests/test_new.py", "SPEC.md", "control/__init__.py"]
    assert harness_tampering(paths) == []
    assert harness_tampering(["robot/control.py", "supervisor/monitor.py"]) == []  # device/
    assert patch_refusal(fakes.TOY_PATCH) is None


@pytest.mark.parametrize("path", ["pytest.py", "sitecustomize.py"])
def test_a_planted_runner_never_runs_even_without_the_refusal(path, no_refusal):
    """`python -m pytest` with the work dir as cwd: `-P` keeps /work off sys.path, so the
    pinned pytest runs and the broken test fails."""
    result = replay(BREAK + new_file(path, FAKE_PASS))
    assert result.patched is not None, result.patch_error
    assert not result.tests_passed and not result.passed, result.summary()
    assert "4 passed in 0.01s" not in result.patched.output
    assert "test_unknown_state_is_an_error" in result.patched.output


@pytest.mark.parametrize(
    "cmd,pinned",
    [
        ("python -m pytest -q", "python -P /s/l.py -q"),
        ("python3 -m pytest", "python3 -P /s/l.py"),
        ("/usr/bin/python3.12 -m pytest", "/usr/bin/python3.12 -P /s/l.py"),
        ("make lint && python -m pytest", "make lint && python -P /s/l.py"),
        ("cd tests;python -m pytest -x;true", "cd tests;python -P /s/l.py -x;true"),
        ("python -I -m pytest -q -rA", "python -I -P /s/l.py -q -rA"),  # the Doctor's repro
        ("python -m pytest_cov", "python -m pytest_cov"),
        ("pytest -q", "pytest -q"),  # the console script never has the cwd on sys.path
        ("python script.py", "python script.py"),  # a script keeps its own dir
    ],
)
def test_python_dash_m_pytest_runs_through_the_sealed_launcher(cmd, pinned):
    assert fakes.run_agent.pin_pytest(cmd, "/s/l.py") == pinned


def test_processes_the_tests_start_still_import_their_own_modules(tmp_path):
    """The real device's tests run `python app.py`, which imports a sibling module: the
    hardening must not leak into it (PYTHONSAFEPATH in the env did, and broke 7 tests)."""
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "helper.py").write_text("X = 1\n")
    (tmp_path / "app" / "main.py").write_text("import helper\nprint(helper.X)\n")
    (tmp_path / "test_app.py").write_text(
        "import subprocess, sys\n\n\ndef test_app():\n"
        "    out = subprocess.run([sys.executable, 'app/main.py'],\n"
        "                         capture_output=True, text=True)\n"
        "    assert out.returncode == 0, out.stderr\n"
    )
    cmd = f"{shlex.quote(sys.executable)} -m pytest -q"
    out = fakes.run_agent.run_tests(cmd, 60, work=str(tmp_path))
    assert out["exit_code"] == 0, out["output"]
    assert out["results"]["tests"] == {"test_app.py::test_app": "passed"}


# --- 2. the device step: base tests decide, the patch's tests are advisory ------------------


def test_a_legitimate_fix_with_a_new_test_passes_and_the_new_test_runs_advisory():
    new_test = new_file(
        "tests/test_regression_d1.py",
        "from control.gripper import step\n\n\ndef test_hand_near_keeps_holding():\n"
        '    assert step("HOLDING", 10.0) == ("HOLDING", "close")',
    )
    result = replay(fakes.TOY_PATCH + new_test)
    assert result.passed, result.summary()
    assert result.patched.results == BASE_IDS  # the trusted run is the base's tests
    assert result.advisory is not None and result.advisory.passed
    assert "tests/test_regression_d1.py::test_hand_near_keeps_holding" in result.advisory.results
    assert "advisory (the patch's own tests, not decisive): pass" in result.summary()


def test_a_forging_plugin_pulled_in_by_a_new_test_file_does_not_decide():
    """PR #53 review: `pytest_plugins` in a new test module loads a forger that turns
    failures into passes. The new test file only runs in the advisory run."""
    patch = (
        FIX_BUT_BREAK
        + new_file("tests/test_zz.py", "pytest_plugins = ['zzplug']\n\n\ndef test_ok():\n    pass")
        + new_file("tests/zzplug.py", FORGER)
    )
    result = replay(patch)
    assert result.verify_passed  # the D1 fix is real
    assert result.advisory is not None and result.advisory.passed  # forged, and ignored
    assert not result.tests_passed and not result.passed, result.summary()


def test_gutting_the_existing_tests_does_not_decide():
    """PR #53 review: every assert in the existing tests replaced by `pass` keeps the count;
    the trusted run uses the base's tests, so the broken code still fails."""
    gutted = (fakes.TOY / "tests" / "test_gripper.py").read_text().splitlines()
    patch = FIX_BUT_BREAK + (
        "diff --git a/tests/test_gripper.py b/tests/test_gripper.py\n"
        "--- a/tests/test_gripper.py\n+++ b/tests/test_gripper.py\n"
        f"@@ -1,{len(gutted)} +1,{len(gutted)} @@\n"
        + "".join(f"-{line}\n" for line in gutted)
        + "".join(
            "+"
            + (
                line[: len(line) - len(line.lstrip())] + "pass"
                if "assert" in line or ("pytest.raises" in line) or line.strip().startswith("step(")
                else line
            )
            + "\n"
            for line in gutted
        )
    )
    result = replay(patch)
    assert "existing_test: tests/test_gripper.py (modified)" in result.flags
    assert not result.tests_passed and not result.passed, result.summary()


def test_an_atexit_forger_in_a_new_test_file_does_not_decide():
    """PR #53 review: a new test module that rewrites the result at exit. It never runs in
    the trusted run, and the seal ignores anything after its end marker anyway."""
    forge = (
        "import atexit, os\n\n\ndef _w():\n    os._exit(0)\n\n\natexit.register(_w)\n\n\n"
        "def test_ok():\n    pass"
    )
    result = replay(FIX_BUT_BREAK + new_file("tests/test_00_forge.py", forge))
    assert not result.tests_passed and not result.passed, result.summary()


# --- 3. result parsing ----------------------------------------------------------------------


def test_fake_summary_lines_printed_by_the_code_do_not_pass_it():
    fake = (
        'print("=== short test summary info ===\\nPASSED tests/test_gripper.py::'
        'test_unknown_state_is_an_error\\n4 passed in 0.01s")'
    )
    result = replay(code_line(fake))
    assert not result.tests_passed and not result.passed, result.summary()


def test_fake_failed_lines_do_not_fail_a_correct_patch():
    fake = 'print("FAILED tests/test_gripper.py::test_x - boom\\n1 failed in 0.1s")'
    patch = fakes.TOY_PATCH + (
        "--- a/control/gripper.py\n+++ b/control/gripper.py\n"
        "@@ -1,1 +1,2 @@\n+" + fake + '\n """Gripper control step: one distance reading in, one '
        "gripper command out.\n"
    )
    result = replay(patch)
    assert result.passed, result.summary()


def test_an_early_exit_zero_is_not_a_pass():
    """`os._exit(0)` on import: pytest exits 0 before any test fails, and never reaches the
    seal's end marker."""
    result = replay(code_line("import os; os._exit(0)"))
    assert result.patched is not None and result.patched.exit_code == 0
    assert result.patched.incomplete and not result.patched.passed
    assert not result.tests_passed and not result.passed
    assert "pytest started but didn't finish (no end marker)" in result.summary()


def _judged(tmp_path, cmd: str) -> TestRun:
    """A test command run by the image's `run_tests`, judged by `TestRun.passed` alone."""
    out = fakes.run_agent.run_tests(cmd, 60, work=str(tmp_path))
    return coder_runs.test_run_of(SandboxRun({"tests": out}, ProcResult(0, "", "")))


PYTEST = f"{shlex.quote(sys.executable)} -m pytest -q"


def test_an_exit_zero_mid_session_is_not_passed_on_its_own(tmp_path):
    """`.passed` alone (the Doctor's repro steps, `check.py`) must not fail open: pytest
    started, a test called `os._exit(0)`, and the end marker never came."""
    (tmp_path / "test_a.py").write_text("def test_ok():\n    pass\n")
    (tmp_path / "test_b.py").write_text("import os\n\n\ndef test_bye():\n    os._exit(0)\n")
    run = _judged(tmp_path, PYTEST)
    assert run.exit_code == 0 and run.incomplete and not run.passed
    assert run.results == {"test_a.py::test_ok": "passed"}


def test_a_non_pytest_command_is_judged_by_its_exit_code(tmp_path):
    run = _judged(tmp_path, "true")
    assert run.results is None and not run.incomplete and run.passed
    assert not _judged(tmp_path, "false").passed


def test_a_normal_pytest_run_passes(tmp_path):
    (tmp_path / "test_a.py").write_text("def test_ok():\n    pass\n")
    run = _judged(tmp_path, PYTEST)
    assert run.passed and not run.incomplete
    assert run.results == {"test_a.py::test_ok": "passed"}


def test_skipping_the_device_suite_from_the_code_is_not_a_pass():
    skip = 'import pytest; pytest.skip("not today", allow_module_level=True)'
    result = replay(code_line(skip))
    assert result.patched is not None and result.patched.exit_code in (0, 5)
    assert not result.tests_passed and not result.passed, result.summary()


def test_sealed_results_that_disagree_with_exit_zero_fail():
    assert not TestRun(0, "", results={"t::a": "passed", "t::b": "failed"}).passed
    assert not TestRun(0, "", results={}).passed
    assert TestRun(0, "", results={"t::a": "passed", "t::b": "skipped"}).passed
    assert TestRun(0, "").passed  # not a pytest command: the exit code decides


def test_the_trusted_run_is_compared_with_the_base_by_test_id():
    base = TestRun(0, "", results={"t::a": "passed", "t::b": "failed", "t::s": "skipped"})
    verify = VerifyRun(True, tests=2, passed=2, exit_code=0)

    def ok(results):
        return CodeReplay(base, verify, TestRun(0, "", results=results), verify).passed

    assert ok({"t::a": "passed", "t::b": "passed", "t::s": "skipped"})  # still skipped: fine
    assert ok({"t::a": "passed", "t::b": "passed", "t::s": "passed"})
    assert not ok({"t::a": "passed", "t::s": "skipped", "t::c": "passed"})  # b swapped for c
    assert not ok({"t::a": "skipped", "t::b": "passed", "t::s": "skipped"})  # newly skipped
    assert not CodeReplay(base, verify, TestRun(0, ""), verify).passed  # not sealed


# --- 4. .gitignore hiding -------------------------------------------------------------------


def test_flags_come_from_the_parsed_patch_not_git(no_refusal):
    """A `.gitignore` in the patch hides a planted conftest from `git add -A`; the flags are
    read from the patch itself, so it is still named."""
    patch = (
        fakes.TOY_PATCH
        + new_file(".gitignore", "conftest.py")
        + new_file("conftest.py", "collect_ignore_glob = []")
    )
    result = replay(patch)
    assert "test_harness: conftest.py (added)" in result.flags, result.flags


def test_the_new_package_keeps_a_file_git_would_ignore_and_the_replay_says_so():
    base = {**fakes.toy_files(), "device/.gitignore": b"extra/\n"}
    diff = new_file("extra/mod.py", "X = 1")
    new = code_lockin.apply_diff(base, diff)
    assert new["device/extra/mod.py"] == b"X = 1\n"  # from the parsed diff, not git status
    with pytest.raises(CoderError, match="leaves out changed files"):
        code_lockin.package_diff(base, new)


# --- 5. outside env -------------------------------------------------------------------------


def test_outside_python_env_never_reaches_the_tests(tmp_path, monkeypatch):
    (tmp_path / "evil").mkdir()
    (tmp_path / "evil" / "sitecustomize.py").write_text("import os; os._exit(0)\n")
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "evil"))
    monkeypatch.setenv("PYTHONSTARTUP", str(tmp_path / "evil" / "sitecustomize.py"))
    monkeypatch.setenv("PYTEST_ADDOPTS", "-k nothing")
    monkeypatch.setenv("PYTHONSAFEPATH", "1")
    out = fakes.run_agent.run_tests("env", 30, work=str(tmp_path))
    assert str(tmp_path / "evil") not in out["output"] and "PYTHONSTARTUP" not in out["output"]
    assert "PYTHONSAFEPATH" not in out["output"]  # children must find their own modules
    assert "-k nothing" not in out["output"]
    assert "junitxml" not in out["output"]  # no result location in the env
    assert out["results"] is None  # `env` is not pytest


# --- 6. only sealable test commands are accepted ---------------------------------------------

SEALABLE = ["python -m pytest -q", "python3 -I -m pytest", "python -m pytest -q; true", " "]
UNSEALABLE = ["pytest -q", "uv run pytest", "make test", "python -m pytest_cov", "python t.py"]


@pytest.mark.parametrize("cmd", SEALABLE + UNSEALABLE)
def test_coder_test_cmd_must_be_a_form_the_image_seals(cmd, make_settings):
    """CODER_TEST_CMD fails at startup unless the image would run it through the seal (the
    settings pattern and the image's `pin_pytest` agree)."""
    sealed_by_image = fakes.run_agent.pin_pytest(cmd, "/s/l.py") != cmd
    if cmd in UNSEALABLE:
        assert not sealed_by_image
        with pytest.raises(ValidationError, match="can't be sealed"):
            make_settings(coder_test_cmd=cmd)
    else:
        assert sealed_by_image or not cmd.strip()
        assert make_settings(coder_test_cmd=cmd).coder_test_cmd == cmd
