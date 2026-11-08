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
from pathlib import Path

import pytest

from romulus_cloud.adapters.coder import LocalAgent, Sandbox, subprocess_runner
from romulus_cloud.services.coder_runs import replay_code_patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "fixtures"))
import coder_fakes as fakes  # noqa: E402

LLM = {k: os.environ.get(k, "") for k in ("LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY")}
IMAGE = "romulus-coder:it"
CODER_DIR = Path(__file__).resolve().parents[2] / "coder"

pytestmark = pytest.mark.skipif(
    os.environ.get("CODER_INTEGRATION") != "1", reason="set CODER_INTEGRATION=1 (needs Docker)"
)


@pytest.fixture(scope="module")
def sandbox() -> Sandbox:
    subprocess.run(["docker", "build", "-q", "-t", IMAGE, str(CODER_DIR)], check=True)
    return Sandbox(image=IMAGE, runner=subprocess_runner)


def test_sandbox_replays_the_device_patch(sandbox):
    result = replay_code_patch(fakes.toy_files(), fakes.TOY_PATCH, sandbox=sandbox)
    print(result.summary())
    assert result.caught_fault and result.passed, (result.base.output, result.patched)


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

    # The agent's diff must also pass a fresh, offline replay with the original verify test.
    verify = {"tests/test_verify_d1.py": (fakes.TOY / "tests" / "test_verify_d1.py").read_bytes()}
    replay = replay_code_patch(fakes.toy_files(), draft.diff, sandbox=sandbox, extra_files=verify)
    print("--- replay\n" + replay.summary())
    assert replay.passed
