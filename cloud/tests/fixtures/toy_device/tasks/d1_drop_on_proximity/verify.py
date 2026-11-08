"""Fault D1's verify script (docs/fault-catalog.md): exits 0 only when the arm is healthy.

Feeds a "hand at 3 cm for 2 s" trace through the control step while holding, then checks
that normal behavior is unchanged. Run it from the device root.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))

from control.hold import State, step  # noqa: E402


class Command:
    def __init__(self):
        self.gripper = "unknown"

    def hold(self):
        self.gripper = "closed"
        return self

    def open_gripper(self):
        self.gripper = "open"
        return self

    def idle(self):
        self.gripper = "idle"
        return self


def main() -> int:
    state = State.HOLDING
    for i in range(60):  # 2 s at 30 Hz
        state, cmd = step(state, 30, Command())
        if state != State.HOLDING or cmd.gripper != "closed":
            print(f"FAIL: step {i} with a hand at 30 mm gave {state.name}, gripper {cmd.gripper}")
            return 1
    if step(State.RELEASE, 200, Command())[1].gripper != "open":
        print("FAIL: RELEASE no longer opens the gripper")
        return 1
    if step(State.HOLDING, 200, Command())[1].gripper != "closed":
        print("FAIL: holding with nothing near no longer keeps the gripper closed")
        return 1
    print("OK: the hand trace kept the gripper closed; normal behavior is unchanged")
    return 0


if __name__ == "__main__":
    sys.exit(main())
