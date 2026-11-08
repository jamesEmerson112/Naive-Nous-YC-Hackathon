"""The sandbox entrypoint (`cloud/coder/run_agent.py`) runs inside the image; these tests
cover the parts that don't need mini-swe-agent or Docker."""

import importlib.util
import json
import re
import shlex
import sys
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


def test_test_mode_fails_closed_when_it_cannot_make_itself_undumpable(
    tmp_path, monkeypatch, capsys
):
    """Without PR_SET_DUMPABLE=0 the code under test could write a forged result line to
    PID 1's stdout, so on Linux test and verify modes report no result instead."""
    monkeypatch.setattr(run_agent, "WORK", str(tmp_path))
    monkeypatch.setattr(run_agent, "make_undumpable", lambda: False)
    monkeypatch.setattr(run_agent.sys, "platform", "linux")
    monkeypatch.setenv("CODER_TEST_CMD", "echo should-not-run")
    for mode in ("test", "verify"):
        monkeypatch.setattr("sys.argv", ["run_agent.py", mode])
        assert run_agent.main() == 0
        last = capsys.readouterr().out.strip().splitlines()[-1]
        result = json.loads(last[len(run_agent.MARKER) :])
        got = result["tests" if mode == "test" else "verify"]
        assert got["exit_code"] is None and "prctl" in got["output"]
        assert "should-not-run" not in json.dumps(result).replace("echo should-not-run", "")


def test_a_pytest_run_reports_its_junit_counts_from_outside_the_work_dir(tmp_path):
    """A planted pytest.py in the work dir never runs (`-P`), and the per-test outcomes come
    through the seal's pipe, not from a file in the work dir or the env."""
    (tmp_path / "test_x.py").write_text(
        "import os\n\n\ndef test_a():\n    assert 'ROMULUS_SEAL_FD' not in os.environ\n\n\n"
        "def test_b():\n    pass\n"
    )
    (tmp_path / "pytest.py").write_text('print("9 passed")\nraise SystemExit(0)\n')
    cmd = f"{shlex.quote(sys.executable)} -m pytest -q"
    out = run_agent.run_tests(cmd, 60, work=str(tmp_path))
    assert out["exit_code"] == 0 and "9 passed" not in out["output"]
    assert out["results"]["tests"] == {"test_x.py::test_a": "passed", "test_x.py::test_b": "passed"}
    assert sorted(p.name for p in tmp_path.iterdir()) == ["pytest.py", "test_x.py"]


def test_the_seal_ignores_everything_after_its_end_marker():
    lines = [
        b'{"id": "t::a", "outcome": "failed"}\n',
        b'{"end": true, "exitstatus": 1}\n',
        b'{"id": "t::a", "outcome": "passed"}\n',  # an atexit forger, too late
        b'{"end": true, "exitstatus": 0}\n',
    ]
    assert run_agent.parse_seal(lines) == {"tests": {"t::a": "failed"}, "exitstatus": 1}
    # A forged "passed" line can't override a real failure for the same id.
    lines = [b'{"id": "t::a", "outcome": "failed"}\n', b'{"id": "t::a", "outcome": "passed"}\n']
    assert run_agent.parse_seal([*lines, b'{"end": true}\n'])["tests"] == {"t::a": "failed"}
    assert run_agent.parse_seal(lines) is None  # no start marker: not a sealed pytest run
    started = [b'{"start": true}\n', *lines]
    assert run_agent.parse_seal(started) == {  # started, never finished
        "tests": {"t::a": "failed"},
        "exitstatus": None,
        "incomplete": True,
    }


def test_wait_for_endpoint_waits_for_the_proxy_then_gives_up():
    import socket

    with socket.socket() as srv:
        srv.bind(("127.0.0.1", 0))
        srv.listen()
        port = srv.getsockname()[1]
        assert run_agent.wait_for_endpoint(f"http://127.0.0.1:{port}/v1", timeout_s=2)
    assert not run_agent.wait_for_endpoint(f"http://127.0.0.1:{port}/v1", timeout_s=0.3)
    assert not run_agent.wait_for_endpoint("not a url", timeout_s=0.1)
