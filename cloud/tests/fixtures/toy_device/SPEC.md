# Toy device spec

A stand-in for the real `device/` code (blocker B1) that the coding-agent sandbox is tested
on. It carries one planted fault shaped like D1 in `docs/fault-catalog.md`.

## Gripper (`control/gripper.py`)

- The control loop runs at 50 Hz. Each step takes one ultrasonic distance reading in
  millimetres (`None` when there is no echo) and returns the next state and a gripper command.
- IDLE: the gripper is open. A finished grasp moves to HOLDING and closes it.
- HOLDING: the gripper stays closed. **While holding, never open the gripper because of the
  ultrasonic reading.** Only an explicit release request opens it.
- RELEASE: the gripper opens, then the arm returns to IDLE.

## Tests

`python -m pytest -q` from this directory runs the device suite in `tests/`. It passes on the
planted bug: the suite never covered the case.

`verify/test_verify_d1.py` is the fault's verify check (a hand at 30 mm for 2 s while holding
must not open the gripper). It fails on the bug. The coder holds `verify/` out: the agent
never sees it, and the replay runs it as its own pytest call with a clean config.
