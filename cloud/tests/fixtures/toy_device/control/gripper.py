"""Gripper control step: one distance reading in, one gripper command out.

No hardware access, so the logic runs on any host (fault catalog, rule 3). States are
IDLE, HOLDING and RELEASE; commands are "open" and "close". See SPEC.md.
"""

PROXIMITY_MM = 50
"""Readings under this many millimetres count as "something is close"."""


def step(
    state: str,
    dist_mm: float | None,
    *,
    grasp_done: bool = False,
    release_requested: bool = False,
) -> tuple[str, str]:
    """Return (next_state, gripper_command) for one control step."""
    if state == "IDLE":
        if grasp_done:
            return "HOLDING", "close"
        return "IDLE", "open"
    if state == "HOLDING":
        if release_requested:
            return "RELEASE", "open"
        if dist_mm is not None and dist_mm < PROXIMITY_MM:
            return "RELEASE", "open"
        return "HOLDING", "close"
    if state == "RELEASE":
        return "IDLE", "open"
    raise ValueError(f"unknown state {state!r}")


def run_trace(state: str, trace: list[float | None]) -> list[tuple[str, str]]:
    """Feed a distance trace through `step`, one reading per control step."""
    out = []
    for dist in trace:
        state, command = step(state, dist)
        out.append((state, command))
    return out
