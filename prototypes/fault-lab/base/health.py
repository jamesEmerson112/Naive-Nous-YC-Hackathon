"""Replay the incident and say whether the app is healthy.

Run: python3 health.py
Checks: the app doesn't crash, doesn't hang, and never opens the gripper while holding.
"""
import json
import os
import subprocess
import sys

INCIDENT = "traces/incident.json"
TIMEOUT_S = 5
GRIPPER = 6


def main():
    if os.path.exists("arm_state.json"):
        with open("arm_state.json") as f:
            state = json.load(f)
        if state.get("parked") and not state.get("torque"):
            print("PARKED: the arm is parked with torque off; the app was not run.")
            return 0
    try:
        p = subprocess.run([sys.executable, "app.py", INCIDENT], capture_output=True, text=True, timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired:
        print(f"UNHEALTHY (tier 2, hang): no result after {TIMEOUT_S} s replaying {INCIDENT}.")
        return 1
    if p.returncode != 0:
        print(f"UNHEALTHY (tier 1, crash) replaying {INCIDENT}:")
        print(p.stderr.strip()[-1500:])
        return 1
    holding = False
    for entry in json.loads(p.stdout):
        if entry[0] == "write" and entry[1] == GRIPPER:
            if entry[2] == 0.0:
                holding = True
            elif holding:
                print(f"UNHEALTHY (tier 3, spec violation) replaying {INCIDENT}: the gripper opened "
                      f"(servo {GRIPPER} -> {entry[2]}) while holding. SPEC rule 1 says it must stay closed.")
                return 1
    print("HEALTHY: the incident replay ran to the end without a crash, a hang or a dropped object.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
