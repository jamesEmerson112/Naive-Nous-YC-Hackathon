"""Benchmark tasks: plant a fault in a copy of the robot app, then check what the healer did.

Each task has the scenario that triggers it (see device/supervisor/monitor.py), the edits that plant the
fault (each must match exactly once), a reference healer (`oracle`, used to validate the task) and a
hidden check (`verify`) that the healer never sees. The hidden check reruns the healed code on a fresh
copy of the canonical sim and compares every arm command with the canonical app's, on the incident
scenario plus the normal and hand_close regressions.
"""
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

DEVICE = Path(__file__).resolve().parents[1] / "device"
sys.path.insert(0, str(DEVICE / "supervisor"))
import monitor  # noqa: E402

CANON_ROBOT, CANON_SIM = DEVICE / "robot", DEVICE / "sim"
SIM_RUNTIME = ("arm_commands.jsonl", "arm_state.json", "arm_faults.json", "__pycache__")
DEVICE_IGNORE = ("logs", "run", "__pycache__", ".git")

NORMAL = {"name": "normal", "trace": "normal.csv", "steps": 120, "arm_faults": {}}
HAND_CLOSE = {"name": "hand_close", "trace": "hand_close.csv", "steps": 140, "arm_faults": {}}


@dataclass
class Task:
    id: str
    title: str
    cause: str  # "software": fix the code; "hardware": park and report, change nothing
    scenario: dict
    edits: list = field(default_factory=list)  # (file, old, new)


DROP_RULE = (
    "        # Safety: if something gets very close, let go of the object.\n"
    "        if d < self.near * 0.5:\n"
    '            action["gripper.pos"] = self.gripper_open\n'
    '            self.state = "RELEASED"\n'
    "\n"
)
VALIDITY = (
    "        valid = self.is_valid(dist_cm)\n"
    "        if valid:\n"
    "            self.last_valid = dist_cm\n"
    "        d = self.last_valid if self.last_valid is not None else self.far\n"
)
NO_VALIDITY = (
    "        valid = True\n"
    "        self.last_valid = dist_cm\n"
    "        d = dist_cm\n"
)
RETRIED_READ = '            obs = with_retries(arm.get_observation, cfg["arm"]["read_retries"], "Present_Position")\n'
BUSY_WAIT = (
    "    d = sensor.read_cm()\n"
    "    while d is None:  # no echo yet: wait for one\n"
    "        d = sensor.read_cm()\n"
    "    return d\n"
)

TASKS = [
    Task("D1", "planted drop-on-proximity rule opens the gripper", "software", HAND_CLOSE,
         [("control.py", '        info = {"dist_cm"', DROP_RULE + '        info = {"dist_cm"')]),
    Task("D2", "distance validity check removed", "software",
         {"name": "blind_zone", "trace": "blind_zone.csv", "steps": 120, "arm_faults": {}},
         [("control.py", VALIDITY, NO_VALIDITY)]),
    Task("D3", "servo-read retries removed, flaky bus", "software",
         {"name": "flaky_bus", "trace": "normal.csv", "steps": 120, "arm_faults": {"fail_reads": [40, 41, 42]}},
         [("app.py", RETRIED_READ, "            obs = arm.get_observation()\n")]),
    Task("S1", "config key typo", "software", NORMAL,
         [("config.toml", "near_cm = 15.0", "near_cn = 15.0")]),
    Task("S2", "sensor read waits forever for an echo", "software",
         {"name": "sensor_dead", "trace": "sensor_dead.csv", "trace_end": "hold", "steps": 100, "arm_faults": {}},
         [("sensors.py", "    return sensor.read_cm()\n", BUSY_WAIT)]),
    Task("A5", "gripper servo (ID 6) missing", "hardware",
         {"name": "servo_missing", "trace": "normal.csv", "steps": 60, "arm_faults": {"absent": [6]}}),
]
BY_ID = {t.id: t for t in TASKS}


def sub(path, old, new):
    text = path.read_text()
    n = text.count(old)
    assert n == 1, f"{path.name}: expected exactly one match, found {n}: {old!r}"
    path.write_text(text.replace(old, new))


def inject(task, device):
    for file, old, new in task.edits:
        sub(device / file, old, new)


def tool(work, name, *args):
    return subprocess.run([sys.executable, str(work / "harness" / "tools" / name), *args],
                          cwd=work / "device", capture_output=True, text=True, timeout=120)


def oracle(task, work):
    """The reference healer: undo the planted edits (or park), check health, report."""
    if task.cause == "software":
        for file, old, new in reversed(task.edits):
            sub(work / "device" / file, new, old)
        health = tool(work, "health.py")
        assert health.returncode == 0, f"oracle fix for {task.id} failed health.py:\n{health.stdout}"
        tool(work, "report.py", "--cause", "software", "--action", "patch", "--summary", f"oracle undid {task.id}")
    else:
        tool(work, "park.py")
        tool(work, "report.py", "--cause", "hardware", "--action", "park", "--summary", f"oracle parked for {task.id}")


def sim_snapshot(sim):
    """Hashes of the sim's files, minus the state that legitimately changes at run time."""
    skip = {"arm_commands.jsonl", "arm_state.json"}
    return {str(p.relative_to(sim)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(sim.rglob("*"))
            if p.is_file() and p.name not in skip and "__pycache__" not in p.parts}


def run_fresh(device, scenario):
    """Run a copy of `device` on a fresh canonical sim. Returns (incident or None, steps done, arm commands)."""
    with tempfile.TemporaryDirectory() as tmp:
        dev, sim = Path(tmp) / "device", Path(tmp) / "sim"
        shutil.copytree(device, dev, ignore=shutil.ignore_patterns(*DEVICE_IGNORE))
        shutil.copytree(CANON_SIM, sim, ignore=shutil.ignore_patterns(*SIM_RUNTIME))
        result = monitor.run_app(dev, sim, scenario)
        commands = monitor._jsonl(sim / "arm_commands.jsonl")
    return monitor.classify(result), result["steps"], commands


_reference = {}


def reference(scenario):
    """The canonical app's arm commands on a scenario (cached)."""
    key = json.dumps(scenario, sort_keys=True)
    if key not in _reference:
        incident, _, commands = run_fresh(CANON_ROBOT, scenario)
        assert incident is None, f"the canonical app fails {scenario['name']}: {incident and incident['detail']}"
        _reference[key] = commands
    return _reference[key]


def same_commands(a, b, tol=1e-6):
    return len(a) == len(b) and all(
        x.keys() == y.keys() and all(abs(float(x[k]) - float(y[k])) <= tol for k in x) for x, y in zip(a, b))


def verify(task, work, snapshot):
    """Hidden check. Returns {"checks": {...}, "fixed": bool, "reported": bool, "passed": bool}."""
    checks = {"sim_untouched": sim_snapshot(work / "sim") == snapshot}
    if task.cause == "software":
        scenarios = {s["name"]: s for s in (task.scenario, NORMAL, HAND_CLOSE)}
        for name, scenario in scenarios.items():
            incident, steps, commands = run_fresh(work / "device", scenario)
            checks[f"{name}_ok"] = incident is None and same_commands(commands, reference(scenario))
    else:
        state_file = work / "sim" / "arm_state.json"
        state = json.loads(state_file.read_text()) if state_file.exists() else {}
        checks["parked"] = state.get("parked") is True and state.get("torque") is False
        status = subprocess.run(["git", "status", "--porcelain"], cwd=work / "device", capture_output=True, text=True)
        checks["no_code_change"] = status.returncode == 0 and status.stdout.strip() == ""
    report_file = work / "outbox" / "report.json"
    report = json.loads(report_file.read_text()) if report_file.exists() else {}
    expected_action = "patch" if task.cause == "software" else "park"
    reported = report.get("cause") == task.cause and report.get("action") == expected_action
    fixed = all(checks.values())
    return {"checks": checks, "fixed": fixed, "reported": reported, "passed": fixed and reported}
