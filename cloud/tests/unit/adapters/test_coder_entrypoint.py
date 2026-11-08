"""The sandbox entrypoint (`cloud/coder/run_agent.py`) runs inside the image; these tests
cover the parts that don't need mini-swe-agent or Docker."""

import importlib.util
import json
import re
from pathlib import Path

ENTRYPOINT = Path(__file__).resolve().parents[3] / "coder" / "run_agent.py"
_spec = importlib.util.spec_from_file_location("coder_run_agent", ENTRYPOINT)
run_agent = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(run_agent)


def test_action_regex_accepts_the_usual_shell_fences():
    for fence in ("mswea_bash_command", "bash", "sh", "shell"):
        reply = f"THOUGHT: look first.\n\n```{fence}\nls -la\n```\n"
        assert re.findall(run_agent.ACTION_REGEX, reply, re.DOTALL) == ["ls -la"]
    assert re.findall(run_agent.ACTION_REGEX, "```python\nprint(1)\n```", re.DOTALL) == []


def test_test_mode_prints_one_result_line(tmp_path, monkeypatch, capsys):
    (tmp_path / "test_ok.py").write_text("def test_ok():\n    assert True\n")
    monkeypatch.setattr(run_agent, "WORK", str(tmp_path))
    monkeypatch.setenv("CODER_TEST_CMD", "echo ran-the-tests && exit 3")
    monkeypatch.setenv("LLM_API_KEY", "sk-should-not-leak")
    monkeypatch.setattr("sys.argv", ["run_agent.py", "test"])

    assert run_agent.main() == 0
    last = capsys.readouterr().out.strip().splitlines()[-1]
    assert last.startswith(run_agent.MARKER)
    result = json.loads(last[len(run_agent.MARKER) :])
    assert result["mode"] == "test" and "agent" not in result
    assert result["tests"]["exit_code"] == 3 and "ran-the-tests" in result["tests"]["output"]


def test_tests_never_see_the_llm_env(tmp_path, monkeypatch):
    monkeypatch.setattr(run_agent, "WORK", str(tmp_path))
    monkeypatch.setenv("LLM_API_KEY", "sk-should-not-leak")
    out = run_agent.run_tests("env", 30)
    assert "sk-should-not-leak" not in out["output"] and "LLM_API_KEY" not in out["output"]


def test_long_output_is_capped():
    text = "x" * (run_agent.OUTPUT_CAP * 2)
    capped = run_agent._cap(text)
    assert len(capped) < run_agent.OUTPUT_CAP + 100 and "characters cut" in capped
