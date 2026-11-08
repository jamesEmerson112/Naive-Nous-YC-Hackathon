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
