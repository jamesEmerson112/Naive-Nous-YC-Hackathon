"""Opt-in: build the real `coder` image and run it on the toy device (M8, blockers B4, B5).

Skipped unless CODER_INTEGRATION=1. Needs Docker. The agent test also needs an
OpenAI-compatible endpoint and spends real (small) credits:

    CODER_INTEGRATION=1 LLM_BASE_URL=... LLM_MODEL=... LLM_API_KEY=... \
        uv run pytest cloud/tests/integration -s

The LLM_* variables are read when this module is imported, because conftest unsets every
settings variable before each test (so ordinary tests can never call out).
"""

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from romulus_cloud.adapters.coder import LocalAgent, Sandbox, subprocess_runner
from romulus_cloud.services.coder_runs import replay_code_patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "fixtures"))
import coder_fakes as fakes  # noqa: E402

LLM = {k: os.environ.get(k, "") for k in ("LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY")}
IMAGE = os.environ.get("CODER_IT_IMAGE") or "romulus-coder:it"  # tag to build
CODER_DIR = Path(__file__).resolve().parents[2] / "coder"

pytestmark = pytest.mark.skipif(
    os.environ.get("CODER_INTEGRATION") != "1", reason="set CODER_INTEGRATION=1 (needs Docker)"
)


@pytest.fixture(scope="module")
def sandbox() -> Sandbox:
    toolkit = CODER_DIR.parents[1] / "firmware" / "toolkit"  # the STM32 toolchain stage (B8)
    subprocess.run(
        [
            "docker",
            "build",
            "-q",
            "--build-context",
            f"fwtoolkit={toolkit}",
            "-t",
            IMAGE,
            str(CODER_DIR),
        ],
        check=True,
    )
    return Sandbox(image=IMAGE, runner=subprocess_runner)


CONFTEST_SKIP = (
    "diff --git a/conftest.py b/conftest.py\nnew file mode 100644\n--- /dev/null\n"
    '+++ b/conftest.py\n@@ -0,0 +1 @@\n+collect_ignore_glob = ["verify/*"]\n'
)


def test_agent_mode_hides_the_key_and_gives_up_fast(sandbox, tmp_path):
    """No LLM needed: the endpoint is unreachable (--network none). The test command runs
    as a child of the entrypoint, as the agent's commands do, and tries to read the key."""
    work = tmp_path / "work"
    work.mkdir()
    key = "sk-integration-FAKE-0123456789"
    probe = "cat /proc/1/environ; echo; cat /proc/1/mem; env; echo probe-done"
    started = time.monotonic()
    run = sandbox.run(
        work,
        "agent",
        env={
            "LLM_BASE_URL": "http://10.255.255.1:9/v1",
            "LLM_MODEL": "none",
            "LLM_API_KEY": key,
            "CODER_TEST_CMD": probe,
            "CODER_TIME_LIMIT_S": "20",
            "CODER_LLM_TIMEOUT_S": "5",
        },
        stdin="say hi",
        timeout_s=240,
        network="none",
    )
    elapsed = time.monotonic() - started
    output = run.tests.get("output", "")
    print(f"{elapsed:.0f} s; agent: {run.agent}; probe output:\n{output}")
    assert "probe-done" in output and "Permission denied" in output
    assert key not in run.proc.stdout + run.proc.stderr
    assert run.agent.get("exit_status") not in ("", "Submitted", "SandboxError")
    assert elapsed < 120  # the retry cap and request timeout end the run


def test_sandbox_replays_the_device_patch(sandbox):
    result = replay_code_patch(fakes.toy_files(), fakes.TOY_PATCH, sandbox=sandbox)
    print(result.summary())
    assert result.caught_fault and result.passed, result.summary()
    assert result.expected_verify == 2


def test_real_image_is_not_fooled_by_a_conftest_skip(sandbox):
    result = replay_code_patch(fakes.toy_files(), CONFTEST_SKIP, sandbox=sandbox)
    print(result.summary())
    assert not result.passed and "conftest.py (test-runner config)" in result.patch_error


# Breaks the code (a test fails) and plants a pytest that prints a pass and exits 0.
SHADOW_PYTEST = """\
diff --git a/control/gripper.py b/control/gripper.py
--- a/control/gripper.py
+++ b/control/gripper.py
@@ -29,4 +29,4 @@ def step(
     if state == "RELEASE":
         return "IDLE", "open"
-    raise ValueError(f"unknown state {state!r}")
+    return state, "open"
 
diff --git a/pytest.py b/pytest.py
new file mode 100644
--- /dev/null
+++ b/pytest.py
@@ -0,0 +1,2 @@
+print("4 passed in 0.01s")
+raise SystemExit(0)
"""


def test_real_image_runs_the_pinned_pytest_not_a_planted_one(sandbox, monkeypatch):
    """With the patch refusal switched off, the image itself still can't be shadowed."""
    from romulus_cloud.services import coder_runs

    monkeypatch.setattr(coder_runs, "patch_refusal", lambda patch: None)
    result = replay_code_patch(fakes.toy_files(), SHADOW_PYTEST, sandbox=sandbox)
    print(result.summary())
    assert result.patched is not None and not result.tests_passed, result.summary()
    assert "test_unknown_state_is_an_error" in result.patched.output
    assert result.base.results and len(result.base.results) == 4


@pytest.mark.skipif(not (LLM["LLM_BASE_URL"] and LLM["LLM_MODEL"]), reason="needs LLM_* env")
def test_real_agent_fixes_the_toy_device(sandbox):
    agent = LocalAgent(
        base_url=LLM["LLM_BASE_URL"],
        model=LLM["LLM_MODEL"],
        api_key=LLM["LLM_API_KEY"],
        sandbox=sandbox,
        step_limit=30,
        time_limit_s=600,
    )
    incident = {
        "id": "inc-toy-d1",
        "device_id": "arm-1",
        "fault_kind": "dropped_object",
        "summary": "Arm dropped the cube when a hand came within 3 cm while holding it.",
    }
    draft = agent.draft(fakes.TOY_PATCH, incident, "toy-c0de", fakes.toy_files())
    print("\n--- notes\n" + draft.notes)
    print("--- diff\n" + draft.diff)
    print("--- tests\n" + draft.test_output[-1500:])
    if LLM["LLM_API_KEY"]:
        assert LLM["LLM_API_KEY"] not in draft.notes + draft.test_output + draft.diff
    assert draft.passed, draft.notes

    # What the gate trusts: a fresh, offline replay with the held-out verify scripts.
    replay = replay_code_patch(fakes.toy_files(), draft.diff, sandbox=sandbox)
    print("--- replay\n" + replay.summary())
    assert replay.caught_fault and replay.passed
