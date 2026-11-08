"""Verify for the planted D1 fault: a hand near the sensor must not make the arm drop.

It never imports the code under test: the replay's verify process keeps the work dir off
`sys.path`. The gripper logic runs in a child process started in the work dir, and only its
printed JSON comes back.
"""

import json
import subprocess
import sys
from pathlib import Path

WORK = Path(__file__).resolve().parent.parent
HAND_AT_30_MM_FOR_2_S = [30.0] * 100  # 50 Hz


def _call(code: str):
    """Run `code` (which prints one JSON value) against the candidate's control package."""
    prog = "import json\nfrom control.gripper import run_trace, step\n" + code
    out = subprocess.run(
        [sys.executable, "-c", prog], cwd=WORK, capture_output=True, text=True, timeout=60
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_hand_near_while_holding_keeps_the_gripper_closed():
    got = _call(f"print(json.dumps(run_trace('HOLDING', {HAND_AT_30_MM_FOR_2_S!r})))")
    assert got == [["HOLDING", "close"]] * 100


def test_release_still_works_after_a_hand_came_near():
    got = _call(
        f"run_trace('HOLDING', {HAND_AT_30_MM_FOR_2_S!r})\n"
        "print(json.dumps(step('HOLDING', 30.0, release_requested=True)))"
    )
    assert got == ["RELEASE", "open"]
