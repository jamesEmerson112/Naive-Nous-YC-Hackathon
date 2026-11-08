# arm/ — record SO-101 movements by hand

One SO-101 arm, no teleoperation: switch its torque off, move it by hand, and save the
movement as a small JSON trajectory (a *primitive*) in `prims/library/`. LeRobot 0.7.0 is
pinned (see `docs/so101-research.md`).

## Setup (once per machine)
```bash
cd arm && ./setup.sh          # venv + LeRobot; creates arm.env
ls /dev/tty.usbmodem*         # with the arm plugged in (USB + 12 V power)
```
Put that port in `ARM_PORT` in `arm.env`.

The arm's calibration is checked in at `calibration/naive_follower.json` (`ARM_ID`). It is
written to the motors automatically the first time a machine connects, so no calibration
step is needed for this arm.

## Record
```bash
bin/prim-record.sh wave                  # one movement
bin/prim-record.sh wave pick present     # several in a row
bin/prim-record.sh --pose home rest      # single named poses instead (Enter saves each)
```
1. **Support the arm**: its torque goes off as soon as the script starts.
2. Press **Enter** to start a take; the status line shows `[REC 3.2s]`.
3. Move the arm, then press **Enter** to stop and save. Wait for `Saved '<name>'`.
4. `r` + Enter while recording discards the take so you can redo it.

Ctrl-C quits and **loses a take in progress**. A take auto-saves after 120 s
(`--max-seconds`). Idle frames at the start and end are trimmed (`--no-trim` keeps them).
Joints are in degrees; the gripper is 0 (closed) to 100 (open).

## Recordings
`prims/library/*.json`: `wave`, `recording_2`. Each file is `{name, fps, joints, frames}`,
one row of 6 joint values per frame at 50 fps.

To play one back from Python:
```python
from prims import Arm

with Arm() as arm:
    arm.play("wave")               # eases to the start pose, then plays it
    arm.play("recording_2", speed=0.7)
```
Playback switches torque on and drives the arm: keep the area clear. When the script ends
the torque goes off again, so end recordings in the folded rest pose.
