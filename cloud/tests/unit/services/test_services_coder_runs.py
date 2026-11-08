import sys
from pathlib import Path

import pytest

from romulus_cloud.adapters.coder import CoderError, Sandbox
from romulus_cloud.services.coder_runs import replay_code_patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "fixtures"))
import coder_fakes as fakes  # noqa: E402

BAD_CONTEXT = fakes.TOY_PATCH.replace("if release_requested:", "if nothing_like_this:")
VERIFY = (fakes.TOY / "verify" / "test_verify_d1.py").read_bytes()

# Fixes nothing; only hides the verify scripts from collection (reviewer finding 3).
CONFTEST_SKIP = """\
diff --git a/conftest.py b/conftest.py
new file mode 100644
--- /dev/null
+++ b/conftest.py
@@ -0,0 +1 @@
+collect_ignore_glob = ["verify/*", "tests/test_verify*"]
"""

# Same idea through pytest config: deselect the verify test by keyword.
INI_DESELECT = """\
diff --git a/pytest.ini b/pytest.ini
--- a/pytest.ini
+++ b/pytest.ini
@@ -1,3 +1,4 @@
 [pytest]
 pythonpath = .
 testpaths = tests
+addopts = -k "not hand_near"
"""

# A module-level skip planted in the code the verify test imports.
SKIP_FROM_CODE = """\
diff --git a/control/__init__.py b/control/__init__.py
new file mode 100644
--- /dev/null
+++ b/control/__init__.py
@@ -0,0 +1,2 @@
+import pytest
+pytest.skip("not today", allow_module_level=True)
"""

# Reviewer finding 2: a symlink out of the scratch dir, then a hidden verify file through it.
SYMLINK_OUT = """\
diff --git a/vdir b/vdir
new file mode 120000
--- /dev/null
+++ b/vdir
@@ -0,0 +1 @@
+{target}
\\ No newline at end of file
"""

TOUCHES_VERIFY = """\
diff --git a/verify/test_verify_d1.py b/verify/test_verify_d1.py
--- a/verify/test_verify_d1.py
+++ b/verify/test_verify_d1.py
@@ -1 +1 @@
-\"\"\"Verify for the planted D1 fault: a hand near the sensor must not make the arm drop.\"\"\"
+\"\"\"Nothing to see.\"\"\"
"""


def replay(docker=None, patch=fakes.TOY_PATCH, files=None, **kw):
    return replay_code_patch(
        files or fakes.toy_files(),
        patch,
        sandbox=Sandbox(image="romulus-coder:test", runner=docker or fakes.FakeDocker()),
        **kw,
    )


def test_device_patch_fixes_what_the_verify_scripts_catch():
    docker = fakes.FakeDocker()
    result = replay(docker)

    assert result.caught_fault and result.passed, result.summary()
    assert result.expected_verify == 2
    assert result.base.passed  # the device suite never covered the case
    assert result.base_verify.passed == 1 and "hand_near" in result.base_verify.output
    assert result.summary() == (
        "base: tests pass, verify 1/2 passed; patched: tests pass, verify 2/2 passed"
    )
    modes = [c["argv"][-1] for c in docker.calls]
    assert modes == ["verify", "test", "verify", "test"]
    for call in docker.calls:  # no agent, no network, no LLM env
        argv = call["argv"]
        assert argv[argv.index("--network") + 1] == "none"
        assert not any(a.startswith("LLM_") for a in argv)


@pytest.mark.parametrize(
    "patch,refused",
    [
        (CONFTEST_SKIP, "conftest.py (test-runner config)"),
        (INI_DESELECT, "pytest.ini (test-runner config)"),
        (SKIP_FROM_CODE, None),
    ],
)
def test_hiding_the_verify_scripts_is_not_a_pass(patch, refused):
    result = replay(patch=patch)
    assert result.caught_fault
    assert not result.passed, result.summary()
    assert not result.verify_passed
    if refused:  # test-runner config is refused outright now, before anything runs
        assert result.patched is None and refused in result.patch_error


def test_the_reviewers_symlink_case_never_writes_outside(tmp_path):
    outside = tmp_path / "host_dir"
    outside.mkdir()
    result = replay(
        patch=SYMLINK_OUT.format(target=outside),
        verify_files={"verify/test_extra.py": b"def test_x():\n    assert True\n"},
    )
    assert result.patched is None and not result.passed
    assert "symlink (mode 120000)" in result.patch_error
    assert list(outside.iterdir()) == []


def test_verify_files_must_stay_under_verify():
    with pytest.raises(CoderError, match="must be under verify/"):
        replay(verify_files={"vdir/test_v.py": b"x"})
    with pytest.raises(CoderError, match="must be under verify/"):
        replay(verify_files={"../evil.py": b"x"})


def test_a_patch_that_touches_the_verify_scripts_is_refused():
    result = replay(patch=TOUCHES_VERIFY)
    assert result.patched is None and "held-out verify/" in result.patch_error


def _replace_verify(path: str) -> str:
    """Reviewer's case: swap the verify test for two empty tests (same count), fix nothing."""
    old = VERIFY.decode().splitlines()
    new = ["def test_a():", "    pass", "", "", "def test_b():", "    pass"]
    return (
        f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n"
        f"@@ -1,{len(old)} +1,{len(new)} @@\n"
        + "".join(f"-{line}\n" for line in old)
        + "".join(f"+{line}\n" for line in new)
    )


@pytest.mark.parametrize(
    "path",
    [
        "VERIFY/test_verify_d1.py",  # on APFS / Docker Desktop mounts this is verify/
        "Verify/test_verify_d1.py",
        "vErIfY/TEST_VERIFY_D1.py",
        "./verify/test_verify_d1.py",
        "./VERIFY/test_verify_d1.py",
    ],
)
def test_verify_scripts_are_protected_in_any_case_or_spelling(path):
    result = replay(patch=_replace_verify(path))
    assert result.patched is None and not result.passed, result.summary()
    assert "held-out verify/" in result.patch_error


def test_a_verify_scripts_flag_alone_fails_the_replay():
    from romulus_cloud.services.coder_runs import CodeReplay, TestRun, VerifyRun

    ok = TestRun(0, "")
    verify = VerifyRun(True, tests=2, passed=2, exit_code=0)
    clean = CodeReplay(ok, verify, ok, verify)
    assert clean.passed
    flagged = CodeReplay(ok, verify, ok, verify, flags=("verify_scripts: VERIFY/x.py (added)",))
    assert not flagged.passed


def test_patch_that_does_not_apply():
    result = replay(patch=BAD_CONTEXT)
    assert result.patched is None and not result.passed
    assert "git apply failed" in result.patch_error


def test_verify_scripts_the_caller_holds_are_laid_over_both_runs():
    files = fakes.toy_files()
    del files["device/verify/test_verify_d1.py"]
    result = replay(files=files, verify_files={"verify/test_verify_d1.py": VERIFY})
    assert result.caught_fault and result.passed and result.expected_verify == 2


def test_no_verify_scripts_means_device_tests_only():
    files = {k: v for k, v in fakes.toy_files().items() if "/verify/" not in k}
    result = replay(files=files)
    assert result.expected_verify == 0 and result.passed and not result.caught_fault
    assert "verify none" in result.summary()


def test_sandbox_timeout_is_a_failure():
    result = replay(fakes.FakeDocker(timed_out=True))
    assert result.base.timed_out and not result.passed
    assert "verify timed out" in result.summary()
