"""Opt-in: B1's proof in the real `coder` image with `--network none` (no LLM).

- device/'s own suite passes on the package the cloud ships (git-tracked files only);
- the replay with the bench's held-out verify catches planted fault D1 and passes the fix,
  given as a `code_patch` in Ishaan's path format (`--- a/robot/control.py`).

    CODER_INTEGRATION=1 uv run pytest cloud/tests/integration/test_device_code_sandbox.py -s

Uses the image `make coder-image` builds (CODER_IMAGE overrides; default romulus-coder).
"""

import os
import subprocess
from pathlib import Path

import pytest

from romulus_cloud.adapters.coder import Sandbox
from romulus_cloud.devicecode import check, package, verify_from_bench
from romulus_cloud.services import code_lockin
from romulus_cloud.services.coder_runs import replay_code_patch

REPO = Path(__file__).resolve().parents[3]
IMAGE = os.environ.get("CODER_IMAGE") or "romulus-coder"

pytestmark = pytest.mark.skipif(
    os.environ.get("CODER_INTEGRATION") != "1", reason="set CODER_INTEGRATION=1 (needs Docker)"
)


@pytest.fixture(scope="module")
def sandbox() -> Sandbox:
    found = subprocess.run(["docker", "image", "inspect", IMAGE], capture_output=True)
    if found.returncode != 0:
        pytest.skip(f"no {IMAGE} image: run make coder-image")
    return Sandbox(image=IMAGE)


def test_device_suite_passes_in_the_coder_image_offline(sandbox):
    _, files = package.from_git(REPO)
    run = check.run_package_tests(files, sandbox=sandbox)
    passed, report = check.describe(run)
    print(report)
    assert passed, report
    assert " passed" in run.tests["output"]


def test_replay_catches_planted_d1_and_passes_the_fix(sandbox):
    tasks = verify_from_bench.load_tasks(REPO / "bench")
    _, files = package.from_git(REPO)
    planted = verify_from_bench.planted(tasks, "D1", files)
    verify = {
        f"verify/{k}": v for k, v in verify_from_bench.generate(REPO / "bench", files).items()
    }
    patch = code_lockin.package_diff(planted, files)
    assert "--- a/robot/control.py" in patch

    result = replay_code_patch(planted, patch, sandbox=sandbox, verify_files=verify)
    print(result.summary())
    assert result.caught_fault and result.passed, result.summary()
    assert result.expected_verify == 6


# --- PR #53 review: the reviewer's attacks, on the real device/, bench D1 and the image ----

FORGE = """
import importlib.util as _u, os as _os, sys as _sys, sysconfig as _sc
_n = __name__
_spec = _u.spec_from_file_location(_n, _os.path.join(_sc.get_paths()["stdlib"], _n + ".py"))
_m = _u.module_from_spec(_spec)
_sys.modules[_n] = _m
_spec.loader.exec_module(_m)
from _pytest.reports import TestReport as _TR
_orig = _TR.from_item_and_call.__func__
def _f(cls, item, call):
    r = _orig(cls, item, call)
    if r.outcome == "failed":
        r.outcome, r.longrepr = "passed", None
    return r
_TR.from_item_and_call = classmethod(_f)
_sys.stderr.write("REV53-FORGE-ACTIVE " + _n + "\\n")
"""
FULL_ATTACK = {
    "hashlib.py": FORGE,
    "tests/test_zz_rev53.py": "pytest_plugins = ['rev53plug']\ndef test_ok():\n    pass\n",
    "tests/rev53plug.py": FORGE.replace("_n = __name__", '_n = "string"'),
}


@pytest.fixture(scope="module")
def d1():
    tasks = verify_from_bench.load_tasks(REPO / "bench")
    _, files = package.from_git(REPO)
    planted = verify_from_bench.planted(tasks, "D1", files)
    verify = {
        f"verify/{k}": v for k, v in verify_from_bench.generate(REPO / "bench", files).items()
    }
    return files, planted, verify


def _attack(sandbox, d1, extra):
    _, planted, verify = d1
    new = {**planted, **{f"device/{name}": body.encode() for name, body in extra.items()}}
    patch = code_lockin.package_diff(planted, new)
    return replay_code_patch(planted, patch, sandbox=sandbox, verify_files=verify)


def test_the_reviewers_full_attack_is_refused(sandbox, d1):
    result = _attack(sandbox, d1, FULL_ATTACK)
    assert result.patched is None and "hashlib.py" in result.patch_error, result.summary()


def test_the_reviewers_full_attack_fails_even_without_the_refusal(sandbox, d1, monkeypatch):
    """The blocker: D1 unfixed, `hashlib.py` at the root, a forging plugin from a new test
    file. Before the fix: `passed == True`, verify 6/6. Now verify never imports the tree
    and the new test file only runs advisory."""
    from romulus_cloud.services import coder_runs

    monkeypatch.setattr(coder_runs, "patch_refusal", lambda patch: None)
    result = _attack(sandbox, d1, FULL_ATTACK)
    print(result.summary())
    assert not result.passed and not result.verify_passed, result.summary()
    assert "REV53-FORGE-ACTIVE" not in result.patched_verify.output
    assert result.patched_verify.passed < result.expected_verify == 6


def test_gutting_the_existing_tests_does_not_pass_the_device_step(sandbox, d1):
    _, planted, _ = d1
    spec = planted["device/tests/test_spec.py"].decode()
    gutted = "\n".join(
        ln if not ln.strip().startswith("assert ") else ln[: len(ln) - len(ln.lstrip())] + "pass"
        for ln in spec.splitlines()
    )
    result = _attack(sandbox, d1, {"tests/test_spec.py": gutted + "\n"})
    print(result.summary())
    assert not result.tests_passed and not result.passed, result.summary()


def test_a_legitimate_fix_with_a_new_test_passes(sandbox, d1):
    files, _, _ = d1
    extra = {
        "robot/control.py": files["device/robot/control.py"].decode(),
        "tests/test_rev53_extra.py": "def test_extra():\n    assert 1 + 1 == 2\n",
    }
    result = _attack(sandbox, d1, extra)
    print(result.summary())
    assert result.passed, result.summary()
    assert result.advisory is not None and result.advisory.passed
    assert "tests/test_rev53_extra.py::test_extra" in result.advisory.results
