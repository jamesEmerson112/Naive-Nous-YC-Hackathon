"""Hold control for the toy device (fault D1 planted); the contracts example patch fixes it."""

from enum import Enum

from control.log import log_state

# Distance below which the planted D1 rule opens the gripper while holding.
RELEASE_THRESHOLD_MM = 50


class State(Enum):
    IDLE = "IDLE"
    HOLDING = "HOLDING"
    RELEASE = "RELEASE"


def step(state, dist_mm, cmd):
    """One control step: distance in, gripper command out (see SPEC.md)."""
    if state == State.HOLDING:
        if dist_mm is not None and dist_mm < RELEASE_THRESHOLD_MM:
            log_state(State.HOLDING, State.RELEASE, reason="dist_lt_threshold", dist_mm=dist_mm)
            return State.RELEASE, cmd.open_gripper()
        return State.HOLDING, cmd.hold()
    if state == State.RELEASE:
        return State.IDLE, cmd.open_gripper()
    return State.IDLE, cmd.idle()
