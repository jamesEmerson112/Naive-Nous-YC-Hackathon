"""Run the robot app the way the supervisor does, on a temp copy of the fake hardware."""
import shutil
import sys
from pathlib import Path

import pytest

DEVICE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(DEVICE / "supervisor"), str(DEVICE / "sim")]
import monitor  # noqa: E402
from fake_sensor import TraceSensor  # noqa: E402


@pytest.fixture
def run(tmp_path, monkeypatch):
    """run(scenario, trace=None) -> (result, commands, readings).

    Copies robot/ and sim/ to a temp folder and runs app.py there through monitor.run_app (crash,
    hang and timeout detection). `trace` is an optional list of readings (None = no echo) to use
    instead of a recorded trace. `commands` is what the fake arm received, one per step;
    `readings` is what the fake sensor returned at each of those steps.
    """
    robot, sim = tmp_path / "robot", tmp_path / "sim"
    shutil.copytree(DEVICE / "robot", robot, ignore=shutil.ignore_patterns("logs", "run", "__pycache__"))
    shutil.copytree(DEVICE / "sim", sim, ignore=shutil.ignore_patterns("arm_*.json*", "__pycache__"))

    def _run(scenario, trace=None):
        if trace is not None:
            lines = ["none" if d is None else str(d) for d in trace]
            (sim / "traces" / scenario["trace"]).write_text("\n".join(lines) + "\n")
        result = monitor.run_app(robot, sim, scenario, timeout_s=10.0)
        commands = monitor._jsonl(sim / "arm_commands.jsonl")
        monkeypatch.setenv("EDGE_TRACE", str(sim / "traces" / scenario["trace"]))
        monkeypatch.setenv("EDGE_TRACE_END", scenario.get("trace_end", "loop"))
        sensor = TraceSensor(sim)
        readings = [sensor.read_cm() for _ in commands]
        return result, commands, readings

    return _run
