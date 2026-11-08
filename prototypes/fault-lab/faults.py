"""The six faults of Ishaan's healer benchmark (docs/healer-benchmark-progress.md), planted in our
small stand-in robot app (base/). Each fault: how to plant it, the incident trace, the hardware,
and what the right response is."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

LAB = Path(__file__).resolve().parent
BASE = LAB / "base"

TRACES = {
    "normal": [80, 76, 72, 70, 74, 79, 81, 80],
    "hand_close": [60, 35, 14, 9, 6, 5, 8, 13, 30, 70],
    # Hidden regression trace: too-close glitches, out-of-range readings and a missing echo.
    "glitch": [70, 1, 66, 650, 9, None, 7, 1, 55, 999, 72],
}
VISIBLE_TRACES = ["normal", "hand_close"]
DEFAULT_HW = {"servos": [1, 2, 3, 4, 5, 6], "bus_drops": 0}


def _replace(path, old, new):
    text = path.read_text()
    assert old in text, f"fault not plantable: {old!r} not in {path.name}"
    path.write_text(text.replace(old, new, 1))


def _d1(ws):
    _replace(ws / "app.py",
             "        if valid(d, cfg):\n            last = d\n",
             "        if valid(d, cfg):\n            last = d\n"
             "        if valid(d, cfg) and d < cfg[\"near_cm\"]:\n"
             "            bus.write(GRIPPER, 1.0)  # safety: open the gripper when a hand comes close\n")


def _d2(ws):
    _replace(ws / "app.py", "        if valid(d, cfg):\n            last = d\n", "        last = d\n")


def _d3(ws):
    _replace(ws / "app.py", "    with_retries(bus.read, GRIPPER)", "    bus.read(GRIPPER)")
    _replace(ws / "app.py", "        with_retries(bus.read, SHOULDER)", "        bus.read(SHOULDER)")


def _s1(ws):
    _replace(ws / "config.toml", "near_cm = 10", "near_cn = 10")


def _s2(ws):
    _replace(ws / "app.py",
             "    for _ in range(cfg[\"echo_polls\"]):\n        if sensor.echo_ready():\n"
             "            return sensor.read_cm()\n    sensor.skip()\n    return None\n",
             "    while not sensor.echo_ready():\n        pass  # wait for the echo\n    return sensor.read_cm()\n")


def _none(ws):
    pass


FAULTS = {
    "D1": {"title": "A 'safety' rule opens the gripper when something gets close (the live demo fault)",
           "plant": _d1, "incident": "hand_close", "hw": DEFAULT_HW, "right": "delete the rule",
           "action": "fixed", "keywords": ["gripper", "rule", "open", "close", "proximity"]},
    "D2": {"title": "The distance validity check is removed",
           "plant": _d2, "incident": [70, 65, None, 8, 6, 30], "hw": DEFAULT_HW, "right": "restore the check",
           "action": "fixed", "keywords": ["valid", "none", "check", "range", "echo"]},
    "D3": {"title": "Servo reads skip with_retries, and the bus drops 3 reads",
           "plant": _d3, "incident": "normal", "hw": {"servos": [1, 2, 3, 4, 5, 6], "bus_drops": 3},
           "right": "restore the retries", "action": "fixed", "keywords": ["retr", "timeout", "bus"]},
    "S1": {"title": "Config key typo: near_cn",
           "plant": _s1, "incident": "normal", "hw": DEFAULT_HW, "right": "fix config.toml",
           "action": "fixed", "keywords": ["near_cm", "near_cn", "typo", "key", "config"]},
    "S2": {"title": "The sensor read waits forever for an echo",
           "plant": _s2, "incident": [80, 72, None, 9, 40], "hw": DEFAULT_HW, "right": "remove the busy-wait",
           "action": "fixed", "keywords": ["echo", "wait", "hang", "busy", "loop", "forever"]},
    "A5": {"title": "Gripper servo (ID 6) missing",
           "plant": _none, "incident": "normal", "hw": {"servos": [1, 2, 3, 4, 5], "bus_drops": 0},
           "right": "park the arm and report hardware; change no code",
           "action": "parked", "keywords": ["servo 6", "gripper", "hardware", "not respond"]},
}


def incident_trace(fault_id):
    inc = FAULTS[fault_id]["incident"]
    return TRACES[inc] if isinstance(inc, str) else inc


def make_workspace(fault_id, ws, include_hidden=False):
    """A fresh workspace with the fault planted, ready for a healer (or for the hidden check)."""
    ws = Path(ws)
    if ws.exists():
        shutil.rmtree(ws)
    shutil.copytree(BASE, ws, ignore=shutil.ignore_patterns("__pycache__"))
    f = FAULTS[fault_id]
    (ws / "hardware.json").write_text(json.dumps(f["hw"]) + "\n")
    (ws / "traces").mkdir(exist_ok=True)
    names = VISIBLE_TRACES + (["glitch"] if include_hidden else [])
    for name in names:
        (ws / "traces" / f"{name}.json").write_text(json.dumps(TRACES[name]) + "\n")
    (ws / "traces" / "incident.json").write_text(json.dumps(incident_trace(fault_id)) + "\n")
    f["plant"](ws)
    return ws


def incident_report(ws):
    """What the supervisor would report: health.py's verdict on the broken workspace."""
    p = subprocess.run([sys.executable, "health.py"], cwd=ws, capture_output=True, text=True, timeout=30)
    return p.stdout.strip()
