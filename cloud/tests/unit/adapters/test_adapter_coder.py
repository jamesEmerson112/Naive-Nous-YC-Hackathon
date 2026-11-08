import json
import sys

import pytest
from pydantic import SecretStr

from romulus_cloud.adapters import coder as coder_mod
from romulus_cloud.adapters.coder import CodeDraft, CoderError, FakeCoder, LocalAgent, build_coder

PATCH = (
    "--- a/control/gripper.py\n+++ b/control/gripper.py\n@@ -1 +1 @@\n-LIMIT = 900\n+LIMIT = 800\n"
)
KEY = "sk-test-not-a-real-key-123"


def test_fake_coder_passes_patch_through_and_says_so():
    draft = FakeCoder().draft(PATCH, {"id": "inc-1"}, "rev-3")
    assert isinstance(draft, CodeDraft)
    assert draft.diff == PATCH
    assert draft.passed is True and draft.files is None and draft.agent == ""
    assert "pass-through: coding agent not wired" in draft.notes
    assert "rev-3" in draft.notes and "inc-1" in draft.notes


def test_factory(make_settings):
    assert isinstance(build_coder(make_settings()), FakeCoder)
    live = make_settings(
        llm_base_url="https://llm.example/v1",
        llm_model="m-1",
        llm_api_key=SecretStr(KEY),
        coder_command="python runner.py",
        coder_max_steps=7,
    ).model_copy(update={"coder_provider": "local_agent"})
    agent = build_coder(live)
    assert isinstance(agent, LocalAgent)
    assert agent.agent == "mini-swe-agent" and agent.image == "romulus-coder:0.1"
    assert agent.command == ["python", "runner.py"] and agent.max_steps == 7
    assert agent._key == KEY and agent.llm_model == "m-1"
    assert KEY not in repr(live)  # still a SecretStr in the settings
    with pytest.raises(NotImplementedError, match="M8 stub"):
        build_coder(make_settings().model_copy(update={"coder_provider": "naive_session"}))
    with pytest.raises(ValueError):
        build_coder(make_settings().model_copy(update={"coder_provider": "claude"}))


def test_docker_argv_names_the_key_but_never_carries_it():
    agent = LocalAgent(image="img:1", llm_api_key=KEY, network="coder-egress")
    draft = agent._argv("draft", "box")
    assert draft[:4] == ["docker", "run", "--rm", "-i"] and draft[-2:] == ["img:1", "draft"]
    assert "--network" in draft and draft[draft.index("--network") + 1] == "coder-egress"
    assert "LLM_API_KEY" in draft and not any(KEY in a for a in draft)
    assert agent._env("draft")["LLM_API_KEY"] == KEY
    test = agent._argv("test", "box")
    assert test[test.index("--network") + 1] == "none"  # replay runs offline
    assert "LLM_API_KEY" not in test and "LLM_API_KEY" not in agent._env("test")


def _script(code: str) -> list[str]:
    return [sys.executable, "-c", code]


def _agent(code: str, **kw) -> LocalAgent:
    return LocalAgent(command=_script(code), llm_api_key=KEY, **kw)


def test_timeout_raises_a_coder_error(monkeypatch):
    monkeypatch.setattr(coder_mod, "GRACE_S", 0)
    agent = _agent("import time; time.sleep(30)", timeout_s=1)
    with pytest.raises(CoderError, match="timed out after 1 s"):
        agent.test(b"x")


@pytest.mark.parametrize(
    ("stdout", "match"),
    [
        ("hello, not json", "malformed output"),
        ("", "malformed output"),
        ('["a list"]', "malformed output"),
        ('{"diff": 1}', "'diff' is not a str"),
        ('{"error": "git not found"}', "coder sandbox failed: git not found"),
    ],
)
def test_malformed_runner_output_raises(stdout, match):
    agent = _agent(f"import sys; sys.stdin.read(); print({stdout!r})")
    with pytest.raises(CoderError, match=match):
        agent.draft(PATCH, {"id": "inc-1"}, "rev-3", b"pkg")


def test_a_pass_without_a_code_tree_is_malformed():
    out = {"diff": PATCH, "test_output": "", "passed": True, "notes": "", "agent": ""}
    agent = _agent(f"print({json.dumps({**out, 'package_b64': ''})!r})")
    with pytest.raises(CoderError, match="sent no code tree"):
        agent.draft(PATCH, {}, "rev-3", b"pkg")


def test_the_key_is_scrubbed_from_errors_and_never_reaches_test_mode():
    leak = "import os, sys; print('key=' + os.environ.get('LLM_API_KEY', 'unset'), file=sys.stderr)"
    with pytest.raises(CoderError) as err:
        _agent(leak).draft(PATCH, {}, "rev-3", b"pkg")
    assert KEY not in str(err.value) and "key=***" in str(err.value)
    with pytest.raises(CoderError) as err:
        _agent(leak).test(b"pkg")
    assert "key=unset" in str(err.value)


def test_a_missing_package_is_refused():
    with pytest.raises(CoderError, match="no code package"):
        _agent("print('{}')").draft(PATCH, {}, "rev-3", None)


def test_a_missing_sandbox_is_a_coder_error():
    agent = LocalAgent(command=["/nonexistent/romulus-coder"])
    with pytest.raises(CoderError, match="could not start the coder sandbox"):
        agent.test(b"pkg")


def test_runner_is_called_with_the_mode_and_the_request_on_stdin(tmp_path):
    seen = tmp_path / "seen.json"
    code = (
        "import json, sys; req = json.load(sys.stdin); "
        f"open({str(seen)!r}, 'w').write(json.dumps({{'argv': sys.argv[1:], 'req': req}})); "
        "print(json.dumps({'test_output': 'ok', 'passed': True, 'notes': 'n'}))"
    )
    agent = LocalAgent(command=_script(code), test_cmd="pytest -q", verify_glob="tasks/*/v*")
    run = agent.test(b"pkg")
    assert run.passed and run.output == "ok"
    got = json.loads(seen.read_text())
    assert got["argv"] == ["test"]
    assert got["req"]["test_cmd"] == "pytest -q" and got["req"]["package_b64"] == "cGtn"
