import sys
from pathlib import Path

import pytest

from romulus_cloud.adapters.coder import (
    CodeDraft,
    CoderError,
    FakeCoder,
    LocalAgent,
    Sandbox,
    build_coder,
    build_task_prompt,
    device_relative,
)
from romulus_contracts.codepatch import summarize_code_patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "fixtures"))
import coder_fakes as fakes  # noqa: E402

PATCH = (
    "--- a/control/gripper.py\n+++ b/control/gripper.py\n@@ -1 +1 @@\n-LIMIT = 900\n+LIMIT = 800\n"
)
KEY = "sk-test-SECRET-123"
INCIDENT = {"id": "inc-7", "device_id": "arm-1", "fault_kind": "dropped_object"}


def make_agent(docker: "fakes.FakeDocker", **kw) -> LocalAgent:
    return LocalAgent(
        base_url="https://llm.example/v1",
        model="test-model",
        api_key=KEY,
        sandbox=Sandbox(image="romulus-coder:test", runner=docker),
        **kw,
    )


def test_fake_coder_passes_patch_through_and_says_so():
    draft = FakeCoder().draft(PATCH, {"id": "inc-1"}, "rev-3")
    assert isinstance(draft, CodeDraft)
    assert draft.diff == PATCH
    assert draft.passed is True
    assert "pass-through: coding agent not wired" in draft.notes
    assert "rev-3" in draft.notes and "inc-1" in draft.notes


def test_factory():
    assert isinstance(build_coder("fake"), FakeCoder)
    agent = build_coder(
        "local_agent", "", "", base_url="https://llm.example/v1", model="m", api_key=KEY
    )
    assert isinstance(agent, LocalAgent)
    assert agent.sandbox.image == "romulus-coder" and agent.step_limit == 30
    with pytest.raises(CoderError, match="LLM_BASE_URL and LLM_MODEL"):
        build_coder("local_agent")
    with pytest.raises(CoderError, match="only mini-swe-agent"):
        build_coder("local_agent", "aider", base_url="u", model="m")
    with pytest.raises(NotImplementedError, match="M8 stub"):
        build_coder("naive_session")
    with pytest.raises(ValueError):
        build_coder("claude")


def test_prompt_has_the_incident_the_hint_and_the_rules():
    prompt = build_task_prompt(fakes.TOY_PATCH, INCIDENT, "c0de001")
    assert "c0de001" in prompt and "inc-7" in prompt and "dropped_object" in prompt
    assert fakes.TOY_PATCH.strip() in prompt
    for rule in ("clean, minimal fix", "regression test", "SPEC.md", "python -m pytest -q"):
        assert rule in prompt


def test_good_agent_run_returns_a_clean_diff_that_passes():
    docker = fakes.FakeDocker(agent=fakes.fix_like_a_careful_engineer)
    draft = make_agent(docker).draft(fakes.TOY_PATCH, INCIDENT, "c0de001", fakes.toy_files())

    assert draft.passed is True, draft.test_output
    assert docker.run_call["stdin"] == build_task_prompt(fakes.TOY_PATCH, INCIDENT, "c0de001")
    # The diff is a code_patch: paths relative to device/, new files included, no caches.
    summary = summarize_code_patch(draft.diff)
    assert summary.ok, summary.problems
    assert summary.paths == ("control/gripper.py", "tests/test_regression_d1.py")
    assert "-        if dist_mm is not None and dist_mm < PROXIMITY_MM:" in draft.diff
    assert "__pycache__" not in draft.diff
    assert "passed" in draft.test_output
    for fact in ("7 steps (limit 30)", "exit Submitted", "1200 in / 300 out", "test-model"):
        assert fact in draft.notes
    assert "tests `python -m pytest -q`: exit 0" in draft.notes


def test_agent_never_sees_git_metadata():
    docker = fakes.FakeDocker(agent=fakes.fix_like_a_careful_engineer)
    make_agent(docker).draft(fakes.TOY_PATCH, INCIDENT, "c0de001", fakes.toy_files())
    assert docker.saw_git_in_work is False


def test_failing_tests_mean_not_passed():
    def blunt_and_wrong(work: Path) -> None:  # touches a file but leaves the bug
        (work / "control" / "gripper.py").write_text(
            (work / "control" / "gripper.py").read_text() + "\n# looked at it\n"
        )

    docker = fakes.FakeDocker(agent=blunt_and_wrong)
    draft = make_agent(docker).draft(fakes.TOY_PATCH, INCIDENT, "c0de001", fakes.toy_files())
    assert draft.passed is False
    assert "test_hand_near_while_holding_keeps_the_gripper_closed" in draft.test_output
    assert "exit 1" in draft.notes


def test_no_change_is_not_a_pass_even_if_tests_pass():
    files = fakes.toy_files()
    del files["device/tests/test_verify_d1.py"]  # the suite now passes on the bug
    draft = make_agent(fakes.FakeDocker()).draft(fakes.TOY_PATCH, INCIDENT, "c0de001", files)
    assert draft.passed is False and draft.diff == ""
    assert "the agent changed nothing" in draft.notes


def test_timeout_kills_the_container_and_fails():
    docker = fakes.FakeDocker(agent=fakes.fix_like_a_careful_engineer, timed_out=True)
    agent = make_agent(docker, time_limit_s=5, test_timeout_s=5)
    draft = agent.draft(fakes.TOY_PATCH, INCIDENT, "c0de001", fakes.toy_files())

    assert draft.passed is False
    assert "sandbox timed out without a result" in draft.notes
    assert "did not run" in draft.test_output
    run, kill = docker.calls
    assert kill["argv"][1:] == ["kill", run["argv"][run["argv"].index("--name") + 1]]
    assert docker.calls[0]["argv"]  # the run came first
    assert draft.diff  # the partial work is still returned for the timeline


def test_sandbox_argv_mounts_only_work_and_keeps_the_key_out_of_argv():
    docker = fakes.FakeDocker(agent=fakes.fix_like_a_careful_engineer)
    make_agent(docker, step_limit=12).draft(fakes.TOY_PATCH, INCIDENT, "c0de001", fakes.toy_files())
    call = docker.run_call
    argv = call["argv"]
    assert argv[:3] == ["docker", "run", "--rm"]
    assert argv.count("-v") == 1 and argv[argv.index("-v") + 1].endswith(":/work")
    assert argv[argv.index("--network") + 1] == "bridge"
    assert "ALL" == argv[argv.index("--cap-drop") + 1]
    assert argv[-2:] == ["romulus-coder:test", "agent"]
    assert KEY not in " ".join(argv)
    assert "LLM_API_KEY" in argv  # passed by name; the value comes from the env
    assert call["env"]["LLM_API_KEY"] == KEY
    assert call["env"]["CODER_STEP_LIMIT"] == "12"


def test_secrets_never_reach_notes_or_test_output():
    leaky = {
        "agent": "mini-swe-agent",
        "exit_status": "AuthenticationError",
        "error": f"bad key {KEY}",
        "steps": 1,
    }
    docker = fakes.FakeDocker(
        agent=fakes.fix_like_a_careful_engineer, agent_result=leaky, extra_output=f"env: {KEY}"
    )
    draft = make_agent(docker).draft(fakes.TOY_PATCH, INCIDENT, "c0de001", fakes.toy_files())
    assert KEY not in draft.notes and KEY not in draft.test_output and KEY not in draft.diff
    assert "[redacted]" in draft.notes and "[redacted]" in draft.test_output


def test_bad_inputs_raise_coder_error():
    agent = make_agent(fakes.FakeDocker())
    with pytest.raises(CoderError, match="needs the code package"):
        agent.draft(fakes.TOY_PATCH, INCIDENT, "c0de001")
    with pytest.raises(CoderError, match="no files under device/"):
        agent.draft(fakes.TOY_PATCH, INCIDENT, "c0de001", {"README.md": b"x"})


def test_device_relative():
    assert device_relative("device/control/gripper.py") == "control/gripper.py"
    assert device_relative("./device/a.py") == "a.py"
    assert device_relative("romulus/patches/ov-1.diff") is None
    assert device_relative("device/../etc/passwd") is None
    assert device_relative("device") is None
