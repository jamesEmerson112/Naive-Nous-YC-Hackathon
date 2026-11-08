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
| `heal_test.py` | **self-healing bench test**: a bad pod firmware blocks the arm, the healer agent fixes it, the arm runs (below) |
| `tests/` | offline tests for that loop: the bad image's C math, the triage rule, the gate, the agent loop (`simpod.py` is the simulated pod) |
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

## Self-healing test: bad pod firmware → agent fixes it → arm runs
`heal_test.py` plants a firmware fault that makes the ultrasonic report something close at all times, lets
the pod healer agent (qwen3.5:4b, live on screen) find it with the `heal` tools, and checks on the bench that
the agent flashed the known-good image and that the arm's job runs again.

**The fault:** pod release v1.7 (`firmware/demo/bad-images/07-us-dist-16bit`). One misplaced `uint16_t` cast
in the HC-SR04 distance math wraps every distance into 0–32 mm, so the gate always says `object at ~15 mm`.
The echo time stays correct, so the pod's own telemetry contradicts itself, and podd's `us_mismatch` rule
(`firmware/healer/healer/detect.py`) flags that without any model. Details: `firmware/docs/DEMO.md` §7.

**What the agent gets** (`firmware/harness/agent.py`, `--tools arm`): the observe tools, `src_log`/`src_diff`/
`src_read`/`src_search`, `fw_flash`/`fw_rollback` (3 per run), `notify_human`, `note`, and `arm_test`, which runs
this folder's `safe_replay.py` on the bench (gate only, or the real motion with `--arm-port`; 4 per run). No
shell, no source editing, and no access to `demo/`, `pod/` or this test. It's woken by the arm's failure log
and podd's incidents.

### Run it (on the Pi, from the repo root)
Before you start: the HC-SR04 points at open space (nothing within 200 mm), `server/start.sh` runs on the
laptop, and podd runs:
```bash
pgrep -f healer/bin/podd || (cd firmware && nohup healer/bin/podd > healer/var/logs/podd.out 2>&1 &)   # once
python3 server/pi_check.py                                  # the model answers through the relay
python3 arm/heal_test.py                                    # the test: gate only, the arm doesn't move
```
With the arm connected and calibrated, so the agent's `arm_test` plays the wave:
```bash
python3 arm/heal_test.py --arm-port /dev/serial/by-id/<adapter> --arm-args "--speed 0.5"
```
Useful flags: `--runs 3` (repeat), `--view brief` (shorter thinking) or `--view off`, `--keep` (leave the pod
as the agent left it), `--sim` (real model against the simulated pod in `tests/simpod.py`: a rehearsal when
the bench is busy; proves nothing about the bench).

The test prints five stages: (1) reset to v1.0, and the gate must pass; (2) deploy v1.7, podd must open
`us_mismatch` and the gate must refuse; (3) the agent, streaming its thinking, each tool call with the gate's
decision, and each result; (4) checks made on the bench, not taken from the agent's words: known-good is
running (over SWD), the agent's own flash verified it, its `arm_test` after the flash passed, a fresh gate
run passes, triage is clean, and it named the root cause; (5) reset. Exit 0 = every check passed.

**Verify it yourself, independently of the test:**
```bash
cd firmware
demo/scenario start 07                                      # plant the fault
python3 ../arm/safe_replay.py --check-only                  # REFUSES: object at ~15 mm; samples show us≈5000+ but mm<33
healer/bin/heal triage                                      # critical us_mismatch
cd harness && python3 agent.py run --tools arm --ollama-url http://192.168.50.1:11434   # the agent, live
cd .. && healer/bin/heal chip_status                        # known-good build running
python3 ../arm/safe_replay.py --check-only                  # passes now
demo/scenario reset
```
Replay any run later: `python3 firmware/harness/view.py` (newest) or `view.py firmware/harness/runs/<dir>`.

### Offline tests (no hardware, no model)
```bash
~/lerobot-venv/bin/python -m pytest arm/tests -q
```
They compile the v1.0 and v1.7 distance lines with gcc and check every echo value, run podd's rules and the
real gate code on simulated samples, drive the real agent loop with a scripted model through a good fix, a
wrong diagnosis and the arm-run cap, render the live view and its replay, and run `heal_test.py`'s full
sequence on the simulated pod, including the abort when the sensor is really blocked.
