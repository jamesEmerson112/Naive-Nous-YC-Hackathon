from control.hold import State, step

from tests.fakes import FakeCommand


def test_holding_with_nothing_near_keeps_the_gripper_closed():
    state, command = step(State.HOLDING, 200, FakeCommand())
    assert state == State.HOLDING and command.gripper == "closed"


def test_holding_with_no_reading_keeps_the_gripper_closed():
    state, command = step(State.HOLDING, None, FakeCommand())
    assert state == State.HOLDING and command.gripper == "closed"


def test_release_opens_the_gripper_and_goes_idle():
    state, command = step(State.RELEASE, 200, FakeCommand())
    assert state == State.IDLE and command.gripper == "open"


def test_idle_stays_idle():
    state, command = step(State.IDLE, 30, FakeCommand())
    assert state == State.IDLE and command.gripper == "idle"
