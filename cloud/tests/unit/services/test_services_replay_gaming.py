"""The gate's code replay against test gaming (K6): each test is a patch that tries a trick
to make `replay_code_patch` report a pass for code that isn't correct.

`FakeDocker` runs the image's own `run_tests` / `run_verify` (`cloud/coder/run_agent.py`) on
this interpreter, so the run hardening (`python -P -m pytest`, no outside PYTHON*/PYTEST*)
and the JUnit report outside the work dir are what these tests exercise.
"""

import shlex
import sys
from pathlib import Path

import pytest

from romulus_cloud.adapters.coder import CoderError, Sandbox
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
    assert result.base.report == {"tests": 4, "failures": 0, "errors": 0, "skipped": 0}
    assert result.patched.report["failures"] == 1


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
HARNESS_PATHS = [
    "conftest.py",
    "control/conftest.py",
    "tests/conftest.py",
    "pytest.ini",
    ".pytest.ini",
    "pyproject.toml",
    "setup.cfg",
    "control/tox.ini",
]
GIT_PATHS = [".gitignore", "control/.gitattributes"]


@pytest.mark.parametrize("prefix", ["", "device/"])  # both code_patch path forms (B3)
@pytest.mark.parametrize("path", SHADOW_PATHS + HARNESS_PATHS + GIT_PATHS)
def test_a_patch_planting_a_runner_config_or_git_file_is_refused(path, prefix):
    patch = BREAK.replace("a/control/", f"a/{prefix}control/").replace(
        "b/control/", f"b/{prefix}control/"
    ) + new_file(prefix + path, FAKE_PASS)
    why = patch_refusal(patch)
    assert why and "test harness" in why and path in why, why
    result = replay(patch)
    assert result.patched is None and not result.passed and path in result.patch_error


def test_ordinary_code_and_new_tests_are_not_tampering():
    assert harness_tampering(["control/gripper.py", "tests/test_new.py", "SPEC.md"]) == []
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
        ("python -m pytest -q", "python -P -m pytest -q"),
        ("python3 -m pytest", "python3 -P -m pytest"),
        ("/usr/bin/python3.12 -m pytest", "/usr/bin/python3.12 -P -m pytest"),
        ("make lint && python -m pytest", "make lint && python -P -m pytest"),
        ("cd tests;python -m pytest", "cd tests;python -P -m pytest"),
        ("python -I -m pytest", "python -I -m pytest"),  # -I already implies -P
        ("pytest -q", "pytest -q"),  # the console script never has the cwd on sys.path
        ("python script.py", "python script.py"),  # a script keeps its own dir
    ],
)
def test_python_dash_m_runs_get_dash_p(cmd, pinned):
    assert fakes.run_agent.pin_pytest(cmd) == pinned


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
    assert out["report"]["tests"] == 1 and out["report"]["failures"] == 0


# --- 2. result parsing ----------------------------------------------------------------------


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
    """`os._exit(0)` on import: pytest exits 0 before any test fails, but writes no report."""
    result = replay(code_line("import os; os._exit(0)"))
    assert result.patched is not None and result.patched.exit_code == 0
    assert result.patched.report is None
    assert not result.tests_passed and not result.passed
    assert "no pytest report" in result.summary()


def test_skipping_the_device_suite_from_the_code_is_not_a_pass():
    skip = 'import pytest; pytest.skip("not today", allow_module_level=True)'
    result = replay(code_line(skip))
    assert result.patched is not None and result.patched.exit_code in (0, 5)
    assert not result.tests_passed and not result.passed, result.summary()


def test_a_report_that_disagrees_with_exit_zero_fails():
    run = TestRun(0, "", report={"tests": 4, "failures": 1, "errors": 0, "skipped": 0})
    assert not run.passed
    assert not TestRun(0, "", report={"tests": 0, "failures": 0, "errors": 0, "skipped": 0}).passed
    assert TestRun(0, "").passed  # not a pytest command: the exit code decides


def test_fewer_tests_or_more_skips_than_the_base_fail():
    base = TestRun(0, "", report={"tests": 4, "failures": 0, "errors": 0, "skipped": 0})
    verify = VerifyRun(True, tests=2, passed=2, exit_code=0)
    fewer = TestRun(0, "", report={"tests": 3, "failures": 0, "errors": 0, "skipped": 0})
    skips = TestRun(0, "", report={"tests": 4, "failures": 0, "errors": 0, "skipped": 1})
    for patched in (fewer, skips, TestRun(0, "")):
        assert not CodeReplay(base, verify, patched, verify).passed
    assert CodeReplay(base, verify, base, verify).passed


# --- 3. .gitignore hiding -------------------------------------------------------------------


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


# --- 4. outside env -------------------------------------------------------------------------


def test_outside_python_env_never_reaches_the_tests(tmp_path, monkeypatch):
    (tmp_path / "evil").mkdir()
    (tmp_path / "evil" / "sitecustomize.py").write_text("import os; os._exit(0)\n")
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "evil"))
    monkeypatch.setenv("PYTHONSTARTUP", str(tmp_path / "evil" / "sitecustomize.py"))
    monkeypatch.setenv("PYTEST_ADDOPTS", "-k nothing")
    monkeypatch.setenv("PYTHONSAFEPATH", "1")
    out = fakes.run_agent.run_tests("env", 30, work=str(tmp_path))
    assert "PYTHONPATH" not in out["output"] and "PYTHONSTARTUP" not in out["output"]
    assert "PYTHONSAFEPATH" not in out["output"]  # children must find their own modules
    assert "-k nothing" not in out["output"] and "--junitxml=" in out["output"]
    assert str(tmp_path) not in out["output"].split("--junitxml=")[1].split()[0]
    assert out["report"] is None  # `env` is not pytest
