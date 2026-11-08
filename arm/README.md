# arm/ — SO-101 teleoperation, recording and motion primitives

Runs on the machine the arms are plugged into. LeRobot 0.7.0 pinned (see `docs/so101-research.md`).
Two ways to capture movement, both while teleoperating leader → follower:

| | `bin/record.sh` | `bin/prim-record.sh` |
|---|---|---|
| Output | LeRobot dataset v3 (joints + optional camera video) in `arm/data/` | small JSON trajectories in `prims/library/` |
| Use it for | training a policy later (ACT / SmolVLA), `lerobot-replay` | scripting the demo: play, chain, speed up, loop |

## 1. Setup (once)
```bash
cd arm && ./setup.sh            # venv + LeRobot; creates arm.env
bin/find-ports.sh               # unplug each arm when asked; put ports in arm.env
bin/calibrate.sh                # follower then leader; ids come from arm.env
```
Plug in the **power** of both arms as well as USB. Keep the same `FOLLOWER_ID` / `LEADER_ID` everywhere: calibration is stored per id.

## 2. Teleoperate
```bash
bin/teleop.sh                         # Ctrl-C to stop
bin/teleop.sh --display_data=true     # with the rerun viewer
```

## 3. Teleoperate + record a LeRobot dataset
```bash
bin/record.sh "Wave at the audience" 5 20     # task, episodes, seconds per episode
```
- **Right arrow:** end the episode early. **Left arrow:** redo it. **Esc:** stop.
- Between episodes there's a reset window (`RESET_SECONDS`, default 10).
- On macOS, arrow keys need the terminal app allowed under *Privacy & Security → Accessibility*; without it LeRobot 0.7 falls back to reading keys from the terminal.
- Cameras: set `CAMERAS` in `arm.env` (find indexes with `.venv/bin/lerobot-find-cameras opencv`).
- Replay an episode: `.venv/bin/lerobot-replay --robot.type=so101_follower --robot.port=... --robot.id=... --dataset.repo_id=naive-nous/so101_demo --dataset.root=arm/data/<folder> --dataset.episode=0`

## 4. Primitives: record by teleop, program in Python
```bash
bin/prim-record.sh --pose home rest       # Enter saves the current pose under each name
bin/prim-record.sh wave pick present      # Enter = start, Enter = stop & save, r+Enter = redo
bin/prim-record.sh --kinesthetic wave     # no leader: follower goes limp, move it by hand

bin/prim-play.sh --list
bin/prim-play.sh home wave wave@1.5 present home   # name@speed
bin/prim-play.sh wave --loop 3
```
Program a sequence (`demo.py`, run with `bin/demo.sh`):
```python
from prims import Arm

with Arm() as arm:
    arm.goto("home")              # saved pose, eased from wherever the arm is
    arm.play("wave", speed=1.2)   # eases to the primitive's start, then plays it
    arm.close_gripper()           # gripper 0 = closed, 100 = open
    arm.goto({"shoulder_pan": 20})  # partial poses: other joints stay put
    arm.run(["home", ("play", "wave", 2.0), ("wait", 1)])
```
Joints are in degrees (`use_degrees=True`); the gripper is 0–100. A take records the leader's commanded positions and trims idle frames at both ends. Disconnecting turns torque off, so support the arm when a script ends.

## Safety
- `MAX_RELATIVE_TARGET` in `arm.env` caps how far any joint may move per control step (try 10–20). It applies to teleop, record and primitives.
- Only one process can hold a serial port: stop teleop before running primitives.
