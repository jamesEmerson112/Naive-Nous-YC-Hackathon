"""Opt-in: build the real `coder` image and build `firmware/` in it, offline (blocker B8).

Skipped unless CODER_INTEGRATION=1. Needs Docker; the first image build downloads the pinned
STM32 toolchain (about 150 MB):

    CODER_INTEGRATION=1 uv run pytest cloud/tests/integration/test_coder_firmware.py -s

It goes through the same path a firmware replay would: the sandbox's `test` mode with the
firmware check as CODER_TEST_CMD, a scratch copy at /work and `--network none`.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from romulus_cloud.adapters.coder import Sandbox, subprocess_runner

ROOT = Path(__file__).resolve().parents[3]
IMAGE = os.environ.get("CODER_IT_IMAGE") or "romulus-coder:it"  # tag to build
LEFT_BEHIND = shutil.ignore_patterns("opt", "downloads", "dist", "build", "backups", "var")

pytestmark = pytest.mark.skipif(
    os.environ.get("CODER_INTEGRATION") != "1", reason="set CODER_INTEGRATION=1 (needs Docker)"
)


def test_firmware_builds_in_the_coder_sandbox(tmp_path):
    subprocess.run(
        ["docker", "build", "-q", "--build-context", f"fwtoolkit={ROOT / 'firmware' / 'toolkit'}",
         "-t", IMAGE, str(ROOT / "cloud" / "coder")],
        check=True,
    )  # fmt: skip
    work = tmp_path / "work"
    shutil.copytree(ROOT / "firmware", work / "firmware", ignore=LEFT_BEHIND, symlinks=True)

    run = Sandbox(image=IMAGE, runner=subprocess_runner).run(
        work,
        "test",
        env={
            "CODER_TEST_CMD": "python /opt/romulus/firmware_check.py",
            "CODER_TEST_TIMEOUT_S": "900",
        },
        timeout_s=1200,
        network="none",
    )
    output = run.tests.get("output", "")
    print(output)
    assert run.tests.get("exit_code") == 0, output
    check = json.loads(output.strip().splitlines()[-1].removeprefix("ROMULUS_RESULT "))
    assert check["passed"] and not check["toolchain"]["stale"]
    assert "arm-none-eabi-gcc" in check["toolchain"]["gcc"]
    pod = next(b for b in check["builds"] if b["project"] == "pod")
    assert (
        pod["passed"] and 0 < pod["size"]["flash"] <= 32 * 1024 and pod["size"]["ram"] <= 6 * 1024
    )
    assert check["demo_images"]["passed"]
