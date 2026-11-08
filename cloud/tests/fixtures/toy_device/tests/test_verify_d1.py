"""Verify for the planted D1 fault: a hand near the sensor must not make the arm drop."""

from control.gripper import run_trace, step

HAND_AT_30_MM_FOR_2_S = [30.0] * 100  # 50 Hz


def test_hand_near_while_holding_keeps_the_gripper_closed():
    assert run_trace("HOLDING", HAND_AT_30_MM_FOR_2_S) == [("HOLDING", "close")] * 100


def test_release_still_works_after_a_hand_came_near():
    run_trace("HOLDING", HAND_AT_30_MM_FOR_2_S)
    assert step("HOLDING", 30.0, release_requested=True) == ("RELEASE", "open")
