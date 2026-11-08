#!/usr/bin/env python3
"""Replay the incident's recorded scenario through the robot app on fake hardware, then a normal run.

Prints one block per run and a last line RESULT PASS or RESULT FAIL (exit code 0 or 1).
Each run gets a fresh copy of ../sim, so this never changes the hardware state.
"""
import json
import shutil
import sys
import tempfile
from pathlib import Path

WORK = Path(__file__).resolve().parents[2]  # work/harness/tools/health.py -> work/
sys.path.insert(0, str(WORK / "supervisor"))
import monitor  # noqa: E402

NORMAL = {"name": "normal", "trace": "normal.csv", "steps": 120, "arm_faults": {}}
RUNTIME_FILES = ("arm_commands.jsonl", "arm_state.json", "__pycache__")


def run(label, scenario):
    with tempfile.TemporaryDirectory() as tmp:
        sim = Path(tmp) / "sim"
        shutil.copytree(WORK / "sim", sim, ignore=shutil.ignore_patterns(*RUNTIME_FILES))
        result = monitor.run_app(WORK / "device", sim, scenario)
    incident = monitor.classify(result)
    name = f"{label} ({result['scenario']}, {result['steps_wanted']} steps)"
    if incident is None:
        print(f"{name}: PASS")
        return True
    print(f"{name}: FAIL")
    print(f"  tier {incident['tier']} {incident['kind']}: {incident['detail']}")
    if result["log_tail"]:
        print("  last log lines:")
        print("\n".join("    " + line for line in result["log_tail"][-12:]))
    if result["stack_dump"]:
        print("  stack when stopped:")
        print("\n".join("    " + line for line in result["stack_dump"].strip().splitlines()[:15]))
    return False


def main():
    scenario = json.loads((WORK / "scenario.json").read_text())
    ok = run("incident replay", scenario)
    ok = run("normal run", NORMAL) and ok
    print("RESULT PASS" if ok else "RESULT FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
