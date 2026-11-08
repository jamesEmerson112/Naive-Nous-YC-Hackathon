import pytest
from control.gripper import run_trace, step


def test_idle_stays_open_until_a_grasp():
    assert step("IDLE", 300) == ("IDLE", "open")
    assert step("IDLE", 300, grasp_done=True) == ("HOLDING", "close")


def test_holding_keeps_the_gripper_closed_with_nothing_near():
    assert run_trace("HOLDING", [400, 380, None, 360]) == [("HOLDING", "close")] * 4


def test_release_request_opens_then_returns_to_idle():
    assert step("HOLDING", 400, release_requested=True) == ("RELEASE", "open")
    assert step("RELEASE", 400) == ("IDLE", "open")


def test_unknown_state_is_an_error():
    with pytest.raises(ValueError):
        step("FLYING", 100)
