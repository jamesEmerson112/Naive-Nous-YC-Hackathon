# arm: ultrasonic-gated SO-101 replay (simple demo)

`safe_replay.py` reads the HC-SR04 on the sensor pod, and only if nothing is within `--min-mm` (default
200 mm) does it start a prerecorded motion on the SO-101 follower arm. Once the motion starts, the sensor is
ignored, because the arm reaches for objects right in front of it and would trip the sensor itself. With
`--live-check` it keeps checking during the motion instead: if something comes close, or the sensor stops
reporting, the arm holds where it is and the script stops.

```
pod (STM32 + HC-SR04) --"D mm=.. st=.."--> Pi --(podd socket, or the probe's serial port directly)
                                            |
                                            +--> safe_replay.py --LeRobot--> servo bus adapter --> SO-101
```

| file | what |
|---|---|
| `safe_replay.py` | the demo script |
| `live_replay.py` | same, but watches the sensor for the whole run and holds the arm if anything comes within `--min-mm` (`safe_replay.py --live-check`) |
| `wave.json` | **default motion.** A wave recorded on this arm by hand (kinesthetic, 2026-11-07): 663 frames at 50 fps (13.2 s), in degrees |
| `motions/svla_so101_pickplace_ep0.json` | episode 0 of [lerobot/svla_so101_pickplace](https://huggingface.co/datasets/lerobot/svla_so101_pickplace): 303 frames at 30 fps (10 s), pick-and-place from the folded rest pose and back |
| `heal_test.py` | **self-healing bench test**: a bad pod firmware makes the arm flail, the healer agent fixes it, runs the job itself and checks the motion (below) |
| `arm_status.py` | the arm's live joint state (read straight from the servo bus, torque untouched) and a report of what a job did vs its plan; the agent's `arm_state` / `arm_report` tools |
| `hint_audit.py` | captures everything the agent would read on v1.7 (prompt, tools, kickoff, every read tool through blind mode) and flags anything that gives the answer away |
| `tests/` | offline tests for that loop: the bad image's C math, the triage rule, the gate, the reflex's safety limits, the agent loop (`simpod.py`: simulated pod and arm) |
| `runs/` | (gitignored) one JSON record per job: gate samples, phases, reflex swings, commanded and measured joint positions |
| `extract_motion.py` | regenerates that file (play it with `--motion arm/motions/svla_so101_pickplace_ep0.json`), or pulls another episode/dataset (`--episode 7`, `--repo ...`) |

## Setup (done once on this Pi)
LeRobot 0.7.0 is installed in `~/lerobot-venv` with CPU-only torch:
```bash
python3 -m venv ~/lerobot-venv
~/lerobot-venv/bin/pip install "lerobot[feetech]==0.7.0" pyarrow --extra-index-url https://download.pytorch.org/whl/cpu
```
The `--extra-index-url` matters: without it pip pulls the CUDA build of torch plus triton and the
`nvidia-*` libraries (several GB, useless on a Pi).

## Connecting the arm
1. **Power.** Plug the arm's power supply into the servo bus adapter board on the arm (the small board
   the servo daisy chain plugs into). Use the supply that came with the arm: the 7.4 V STS3215 servos run
   on 5 V and the 12 V versions on 12 V, and a wrong supply can damage them.
2. **USB.** Connect the adapter's USB-C to the Pi. It appears as a second `/dev/ttyACM*` (the J-Link /
   sensor pod is already `/dev/ttyACM0`). Find which one with:
   ```bash
   ~/lerobot-venv/bin/lerobot-find-port        # asks you to unplug and replug the adapter
   ls -l /dev/serial/by-id/                    # stable name, e.g. usb-1a86_USB_Single_Serial_...-if00
   ```
   Prefer the `/dev/serial/by-id/...` path: `ttyACM` numbers swap when devices re-enumerate. The `pi`
   user is already in `dialout`, so no sudo is needed.
3. **Calibrate (once per arm, per computer).** LeRobot needs each joint's range:
   ```bash
   ~/lerobot-venv/bin/lerobot-calibrate --robot.type=so101_follower \
       --robot.port=/dev/serial/by-id/<adapter> --robot.id=romulus_arm
   ```
   Follow the prompts: put the arm in the middle of its range, press Enter, then move every joint through
   its full range. It's saved to `~/.cache/huggingface/lerobot/calibration/robots/so_follower/romulus_arm.json`.
   If the arm was already calibrated on another machine, you can copy that JSON there instead (rename it to
   `romulus_arm.json`, or pass `--robot-id` with its name). If you skip this, `safe_replay.py` starts the
   same calibration itself on first connect.

## Running
Start with the arm folded in its rest pose and the HC-SR04 pointing at the space the arm sweeps through.
The script ramps from wherever the arm is to the wave's first frame over `--ramp-s` seconds, and back at
the end.
```bash
cd ~/sage_hardware_healer/Naive-Nous-YC-Hackathon
~/lerobot-venv/bin/python arm/safe_replay.py --dry-run                 # sensor only, no arm
~/lerobot-venv/bin/python arm/safe_replay.py --arm-port /dev/serial/by-id/<adapter> --speed 0.5
~/lerobot-venv/bin/python arm/safe_replay.py --arm-port /dev/serial/by-id/<adapter>
```
Exit codes: 0 done, 2 refused to start (blocked or sensor untrustworthy), 3 stopped mid-motion.
`--check-only` runs the start gate and stops (exit 0 or 2, no arm needed); every run prints the last distance
samples it judged (`mm=.. us=.. st=..`).

Useful flags: `--min-mm` (distance limit), `--live-check` (also watch the sensor while moving), `--speed` (0.5 = half speed), `--ramp-s` (time to move to the
first frame and back), `--max-step` (LeRobot `max_relative_target`, the largest per-step jump it will send),
`--no-return`.

### Sensor rules
Each `D` sample is **clear**, **blocked** or **unknown**. Every sample in the 1.5 s pre-check must be clear
(at least 6 of them, at 10 Hz). That is the only check by default. With `--live-check`, while moving the
last 0.5 s must also contain at least one sample and no blocked or unknown ones.

| `st` | condition | verdict |
|---|---|---|
| `ok` | `mm >= --min-mm` | clear |
| `ok` | `mm < --min-mm` | blocked |
| `out_of_range` | `us < 116` (closer than 2 cm) | blocked |
| `out_of_range` | longer echo, or none within 60 ms (nothing within 4 m) | clear |
| `no_echo`, `busy`, or no samples | | unknown, so it doesn't move |

If podd is running (`firmware/healer/bin/podd`), the script reads from its socket, so podd keeps logging.
Otherwise it opens the probe's serial port directly. Don't run both that and `make monitor` at once.

## Things to know
- **Units.** `wave.json` is in degrees (no `units` key, so the script uses `use_degrees=True`); it was
  recorded on this arm, so the angles match its calibration. The pick-and-place dataset's actions are in LeRobot's `RANGE_M100_100` units (−100..100 across each joint's
  calibrated range; gripper 0..100), not degrees: `shoulder_lift` and `elbow_flex` sit at exactly −100/+100
  at rest. For that file the script runs the arm with `use_degrees=False` to match, so the motion is scaled onto your
  arm's own calibrated range rather than copying another arm's angles.
- **Dataset label.** The dataset's `robot_type` is `so100_follower`. SO-100 and SO-101 share joint names
  and LeRobot driver code, so the commands carry over. The pick-and-place won't hit a cube on our table;
  it's there to show motion.
- **Torque at the end.** LeRobot disables torque on disconnect (the arm goes limp). That's harmless in the
  rest pose, which is why the script returns there. After a mid-motion stop it holds position and waits for
  Enter before releasing, so you can support the arm first.
- **Sensor placement.** The HC-SR04 sees a cone of about 15° (datasheet). Point it at the arm's workspace.
  The arm must not be in the cone in its rest pose, or it blocks its own start. Moving into the cone
  mid-motion only matters with `--live-check`.

## Self-healing test: bad pod firmware → the arm flails → the agent fixes it and proves the arm works
`heal_test.py` plants a firmware fault that makes the ultrasonic report something close at all times. The arm's
obstacle reflex reacts, so the arm visibly flails. Then the pod healer agent (qwen3.5:4b, live on screen) works
out what's wrong with the `heal` tools, restores the known-good firmware, launches the arm's job itself and
checks the recorded motion before it reports. The test then checks all of it on the bench.

**The fault:** pod release v1.7 (`firmware/demo/bad-images/07-us-dist-16bit`). One misplaced `uint16_t` cast
in the HC-SR04 distance math wraps every distance into 0–32 mm. The echo time stays correct, so the pod's own
telemetry contradicts itself, and podd's `us_mismatch` rule (`firmware/healer/healer/detect.py`) flags that
without any model. Details: `firmware/docs/DEMO.md` §7.

**What the arm does about it:** the job runs `safe_replay.py --on-blocked reflex`. When the start gate sees
something within 200 mm, the obstacle reflex tries to get away from it while it waits for the way to clear.
The base swings away, alternating sides, with quick twitches mixed in, while the wrist rolls and the gripper
flinches. The noisy fake readings drive the swing sizes and pauses, so the motion is irregular. After 8 s it
gives up, returns to its start pose and exits 4. Limits, enforced in code whatever the flags say:
- **Base:** ±20° by default, never more than ±25°; 55°/s by default, never more than 60°/s.
- **Wrist roll:** ±20° at most 90°/s.
- **Gripper:** opens at most 25%.
- **Shoulder, elbow and wrist flex:** hold the folded pose.

On good firmware the gate passes and the arm plays its wave.

**What the agent gets** (`firmware/harness/agent.py`, `--tools arm`):
- **Pod tools:** the observe tools, `src_log`/`src_diff`/`src_read`/`src_search`, `fw_flash`/`fw_rollback` (3 per
  run), `notify_human` and `note`.
- **`arm_run`:** launches the job (4 per run) and returns its log and motion report.
- **`arm_state`:** the arm's joints now (positions, torque, load, temperature).
- **`arm_report`:** what any job did against its plan: frames played, each joint's commanded vs measured range
  (`range_coverage`), reflex swings.
- **Not available:** a shell, source editing, and access to `demo/`, `pod/` or this test.
- **Rules:** restore service, then run the job itself and check that every frame played, there was no reflex,
  and every joint reached its commanded range, before reporting with the numbers.

### Run it (on the Pi, from the repo root)
Before you start:
- The HC-SR04 points at open space (nothing within 200 mm).
- The arm is folded in its rest pose with room to swing ±25° at the base.
- `server/start.sh` runs on the laptop.
- podd runs.

```bash
pgrep -f "python3 healer/bin/podd" >/dev/null || (cd firmware && setsid nohup python3 healer/bin/podd > healer/var/logs/podd.out 2>&1 < /dev/null &)
python3 server/pi_check.py                  # the model answers through the relay
python3 arm/heal_test.py                    # the whole scenario; finds the arm on USB by itself
```
Useful flags:
- `--view brief` shortens the thinking; `--view off` hides it.
- `--runs 3` repeats the scenario.
- `--keep` leaves the pod as the agent left it.
- `--no-arm` checks the gate only and moves nothing.
- `--arm-args "--speed 0.5"` plays the wave at half speed.
- `--sim` runs the real model against the simulated pod and arm in `tests/simpod.py`: a rehearsal that proves
  nothing about the bench.

The test runs five stages:
1. **Reset to v1.0.** The gate must pass (checked without moving).
2. **Deploy v1.7.** podd must open `us_mismatch`, and the operator's job must make the arm flail and give up
   (exit 4).
3. **The agent.** Its thinking streams, then each tool call with the harness's decision and a summary of each
   result. You see the rollback, then `ARM JOB OK`, `playback: 663 of 663 frames` and the range reached per
   joint.
4. **Checks on the bench**, not taken from the agent's words:
   - the agent's flash verified known-good;
   - the agent ran the job after that;
   - that job's recorded motion matches the plan;
   - a fresh gate run passes and triage is clean;
   - the agent named the root cause and reported the motion it checked.
5. **Reset.**

Exit 0 means every check passed.

**Step by step, if you'd rather drive each stage yourself** (from `firmware/`):
```bash
ARM=$(ls /dev/serial/by-id/*1a86* | head -1)
demo/scenario start 07                                                    # plant the bad firmware
~/lerobot-venv/bin/python ../arm/safe_replay.py --arm-port $ARM --on-blocked reflex   # the arm flails, exit 4
cd harness && python3 agent.py run --tools arm --arm-port $ARM --blind-since-deploy --ollama-url http://192.168.50.1:11434
cd .. && python3 ../arm/arm_status.py report                              # what the agent's job did
demo/scenario reset
```
Replay any run later: `python3 firmware/harness/view.py` (newest) or `view.py firmware/harness/runs/<dir>`.

### No hints for the agent
The agent sees only the harness's tools. `python3 arm/hint_audit.py` deploys v1.7, captures everything those
tools would show it, and flags any answer term (uint16, 16-bit, truncation, overflow, cast, the scenario
name...) outside the firmware source; exit 0 means no leaks. What keeps it clean:
- The release is the bare one-line change, with a neutral commit message ("us: tidy the distance conversion")
  and no explanatory comment.
- podd's `us_mismatch` finding states only what it measured and what that rules out (not an obstruction),
  never the bug class or the fix. Restart podd after changing `detect.py`: triage runs inside podd.
- Blind mode hides every record from before the fault: earlier runs' notes, notifications, flash reasons
  and deployments. Without it they give the answer away (`hint_audit.py --no-deploy --minutes 120` shows
  them). `heal_test.py` always runs blind; for `agent.py`, pass `--blind-since-deploy`.
- `demo/`, `firmware/pod/`, the docs, this folder and the run transcripts are out of the tools' reach (no
  shell; the `src_*` tools are confined to the workspace).

### Offline tests (no hardware, no model)
```bash
~/lerobot-venv/bin/python -m pytest arm/tests -q
```
What they cover:
- The v1.0 and v1.7 distance lines, compiled with gcc, checked at every echo value.
- podd's rules and the real gate code on simulated samples.
- The reflex's limits on the simulated arm: held joints, swing and speed caps under any flags, back home,
  irregular motion, a real obstacle that clears.
- The motion report.
- The real agent loop, driven by a scripted model through a good fix, a wrong diagnosis, a job run before the
  fix, and the arm-run cap.
- The live view and its replay.
- `heal_test.py`'s full sequence on the simulated pod and arm, including the abort when the sensor is really
  blocked.
