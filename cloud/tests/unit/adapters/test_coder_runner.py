"""The coder sandbox's runner (`cloud/coder/runner.py`), run for real on the host through
LocalAgent with no agent: git applies the patch and the toy device's tests run. No LLM."""

import pytest

from fake_device.scenarios import D1_CODE_PATCH
from romulus_cloud.adapters.coder import LocalAgent, build_coder

# Applies, but replaces D1 with "always open while holding": the suite and verify both fail.
BAD_PATCH = '''\
--- a/control/hold.py
+++ b/control/hold.py
@@ -18,7 +18,4 @@ def step(state, dist_mm, cmd):
     """One control step: distance in, gripper command out (see SPEC.md)."""
     if state == State.HOLDING:
-        if dist_mm is not None and dist_mm < RELEASE_THRESHOLD_MM:
-            log_state(State.HOLDING, State.RELEASE, reason="dist_lt_threshold", dist_mm=dist_mm)
-            return State.RELEASE, cmd.open_gripper()
-        return State.HOLDING, cmd.hold()
+        return State.RELEASE, cmd.open_gripper()
     if state == State.RELEASE:
'''
INCIDENT = {"id": "inc-d1", "kind": "unexpected_release", "summary": "dropped on proximity"}


@pytest.fixture
def agent(make_settings, host_coder) -> LocalAgent:
    s = make_settings(**host_coder).model_copy(update={"coder_provider": "local_agent"})
    coder = build_coder(s)
    assert isinstance(coder, LocalAgent)
    return coder


def test_the_d1_patch_applies_and_passes_the_tests_and_the_hidden_verify(agent, toy_package):
    draft = agent.draft(D1_CODE_PATCH, INCIDENT, "toy0001", toy_package)
    assert draft.passed, draft.test_output
    assert draft.agent == ""  # no agent drafted it: the device's own patch, tested
    assert "no coding agent (CODER_AGENT=none)" in draft.notes
    assert "checks passed" in draft.notes and "1 verify script(s)" in draft.notes
    assert "5 passed" in draft.test_output
    assert "OK: the hand trace kept the gripper closed" in draft.test_output
    assert "--- a/control/hold.py" in draft.diff and "tests/test_hold_proximity.py" in draft.diff
    assert "verify.py" not in draft.diff  # the hidden check is back in the tree, not the diff
    files = draft.files or {}
    assert b"dist_lt_threshold" not in files["device/control/hold.py"]
    assert "device/tests/test_hold_proximity.py" in files
    assert "device/tasks/d1_drop_on_proximity/verify.py" in files
    assert not any("__pycache__" in name or ".git/" in name for name in files)


def test_a_patch_that_breaks_the_tests_fails_with_their_output(agent, toy_package):
    draft = agent.draft(BAD_PATCH, INCIDENT, "toy0001", toy_package)
    assert not draft.passed
    assert "2 failed" in draft.test_output  # holding now always opens the gripper
    assert "FAIL: step 0 with a hand at 30 mm gave RELEASE" in draft.test_output
    assert "checks failed" in draft.notes


def test_a_patch_that_does_not_apply_fails_and_says_why(agent, toy_package):
    stale = D1_CODE_PATCH.replace("RELEASE_THRESHOLD_MM:", "LIMIT_MM:")
    draft = agent.draft(stale, INCIDENT, "toy0001", toy_package)
    assert not draft.passed and draft.files is None and draft.diff == ""
    assert "does not apply to code toy0001" in draft.notes


def test_an_unknown_agent_fails_and_says_why(agent, toy_package):
    agent.agent = "claude-code"
    draft = agent.draft(D1_CODE_PATCH, INCIDENT, "toy0001", toy_package)
    assert not draft.passed and "unknown coding agent 'claude-code'" in draft.notes


def test_test_mode_runs_the_suite_and_the_verify_scripts(agent, toy_package):
    base = agent.test(toy_package)  # the suite passes on the buggy code; verify catches D1
    assert not base.passed
    assert "4 passed" in base.output and "FAIL: step 0 with a hand at 30 mm" in base.output
    fixed = agent.draft(D1_CODE_PATCH, INCIDENT, "toy0001", toy_package)
    from romulus_cloud.services.registry import build_tarball

    run = agent.test(build_tarball(fixed.files or {}))
    assert run.passed and "5 passed" in run.output, run.output


def test_nothing_to_run_is_not_a_pass(agent, toy_package):
    agent.test_cmd, agent.verify_glob = "", ""
    run = agent.test(toy_package)
    assert not run.passed and "nothing to run" in run.notes
