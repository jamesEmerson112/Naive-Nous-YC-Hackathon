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
| `heal_test.py` | **self-healing bench test**: the arm loops; a hand near the ultrasonic trips a latent pod firmware bug and the arm flails; the healer agent fixes it, runs the job itself and checks the motion; three more loops prove it (below) |
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
Exit codes: 0 done, 2 refused to start (blocked or sensor untrustworthy), 3 stopped mid-motion, 4 the
obstacle reflex gave up (`--on-blocked reflex`).
`--check-only` runs the start gate and stops (exit 0 or 2, no arm needed); every run prints the last distance
samples it judged (`mm=.. us=.. st=..`).

Useful flags: `--min-mm` (distance limit), `--live-check` (also watch the sensor while moving, and stop if
something comes close), `--watch` (watch it for the whole job, and pause instead: carry on once clear, or back off
and run the reflex after `--hold-s`), `--loops N` (play the recording N times), `--speed` (0.5 = half speed), `--ramp-s` (time to move to the
first frame and back), `--max-step` (LeRobot `max_relative_target`, the largest per-step jump it will send),
`--no-return`.

### Sensor rules
Each `D` sample is **clear**, **blocked** or **unknown**. Every sample in the 1.5 s pre-check must be clear
(at least 6 of them, at 10 Hz). That is the only check by default. With `--live-check`, while moving the
last 0.5 s must also contain at least one sample and no blocked or unknown ones.
With `--watch`, the two newest samples both blocked pause the arm (one stray echo doesn't), and it carries on
once every sample of the last `--clear-s` (1 s) is clear. No sample in 0.5 s, or the two newest both unknown,
also pauses it; if that lasts `--hold-s`, the job stops (exit 3).

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
  mid-motion only matters with `--live-check` or `--watch`. On this bench the wave brings the reading down to
  ~338 mm at the closest (podd's log, Nov 8, 00:13), well clear of the 200 mm limit.

## Self-healing test: a latent firmware bug → the arm flails after a hand → the agent fixes it → the arm proves it
`heal_test.py` deploys a pod release with a latent bug. The arm loops its wave while watching the ultrasonic.
Then you hold a hand in front of the sensor for a second and take it away. The sensor sees the hand right, but
its reading never clears, so the arm's obstacle reflex makes it flail. Then the pod healer agent (qwen3.5:4b,
live on screen) works out what's wrong with the `heal` tools, restores the known-good firmware first and checks
the arm's motion. Then it may fix the release forward (edit the source, build, flash) and check the arm again on its
own build. The arm then loops its job three more times to prove it works (with your hand again if the agent left its
own build running), and the test stops. Everything is checked on the bench.

**The fault:** pod release v1.8 (`firmware/demo/bad-images/08-us-near-hold`). It adds a near-object hold to the
HC-SR04 driver: the nearest reading under 200 mm is meant to be held until nothing has been near for 500 ms, so
a hand moving through the beam can't fall between two 10 Hz pings. But the hold has no release: it records when
something was last near (`held_ms`) and never reads it, and `HCSR04_HOLD_MS` is never used, so once anything has
come near it never lets go. The distances are right until a hand comes, and right while it's there. After it leaves they stay
at the hand's until the pod is reset or reflashed. The echo time stays correct, so the pod's own telemetry then
contradicts itself, and podd's `us_mismatch` rule (`firmware/healer/healer/detect.py`) flags that without any
model. Details: `firmware/docs/DEMO.md` §8. The older always-on fault, v1.7 (§7), is still in
`demo/bad-images/` for driving by hand.

**What the arm does about it:** the operator's job runs `safe_replay.py --loops 20 --watch --on-blocked reflex`,
which reads the sensor the whole time, loops and moves included.
1. **Something comes within 200 mm:** the arm holds still where it is.
2. **The way reads clear again for 1 s:** it carries on from there.
3. **Still blocked after `--hold-s` (3 s):** it backs off to its rest pose and runs its obstacle reflex. The base
   swings away, alternating sides, with quick twitches mixed in, while the wrist rolls and the gripper flinches;
   the readings drive the swing sizes and pauses, so the motion is irregular.
4. **Still blocked after 8 s of reflex:** it gives up and exits 4. If the way clears during the reflex instead,
   the arm goes back to where it paused and carries on.

Limits, enforced in code whatever the flags say:
- **Base:** ±20° by default, never more than ±25°; 55°/s by default, never more than 60°/s.
- **Wrist roll:** ±20° at most 90°/s.
- **Gripper:** opens at most 25%.
- **Shoulder, elbow and wrist flex:** hold the folded pose.

On v1.8, the hand pauses the arm, and the arm flails once the hand has left, because the reading never clears.
On good firmware, the arm carries on a second after the hand leaves.

**What the agent gets** (`firmware/harness/agent.py`, `--tools arm`):
- **What wakes it:**
  - the looped job's log;
  - one line from the operator ("a hand came close to the ultrasonic sensor during the job and was taken away
    again after about a second. Nothing has been near the sensor since.");
  - podd's open incidents.
- **Pod tools:** the observe tools, `src_log`/`src_diff`/`src_read`/`src_search`, `src_edit`/`src_revert`/
  `src_commit`, `fw_build`, `fw_flash`/`fw_rollback` (5 per run; a call that never reaches the chip, or that asks
  for the image already running, is refused or refunded and doesn't count), `notify_human` and `note`.
- **`arm_run`:** launches the job (5 per run): one wave, gated at the start, no loop. Returns its log and motion
  report.
- **`arm_state`:** the arm's joints now (positions, torque, load, temperature).
- **`arm_report`:** what any job did against its plan: frames played, each joint's commanded vs measured range
  (`range_coverage`), reflex swings, loops and pauses.
- **Not available:** a shell, and access to `demo/`, `pod/` or this test. Source edits reach only the pod
  firmware's workspace (reset by `demo/scenario reset`).
- **Rules** (one set: `firmware/healer/SYSTEM.md` "How to work" and `ARM_RULES` in `firmware/harness/agent.py`):
  1. stabilize: `fw_rollback` to known-good straight away, then run the job and check the motion;
  2. repair: find the defect in the release's diff, `src_edit`, `fw_build`, `fw_flash` the build, confirm triage
     is clean, then run the job again on that build;
  3. if the build fails any check, roll back to known-good: that is always an acceptable end state.

  The harness enforces the order: until the pod runs known-good and an `arm_run` passed on it, source edits,
  builds and flashes of anything else are refused (not counted).

  The agent is done only when the firmware it leaves running passed the job after its flash (every frame, no
  reflex, every joint at its commanded range). It reports with the numbers.

### Run it (on the Pi, from the repo root)
Before you start:
- The HC-SR04 points at the arm's workspace with nothing within 250 mm. On this bench it reads ~371 mm, and the
  arm's own wave brings it down to ~338 mm.
- The arm is folded in its rest pose with room to swing ±25° at the base.
- `server/start.sh` runs on the laptop.
- podd runs.

```bash
pgrep -f "python3 healer/bin/podd" >/dev/null || (cd firmware && setsid nohup python3 healer/bin/podd > healer/var/logs/podd.out 2>&1 < /dev/null &)
python3 server/pi_check.py                  # the model answers through the relay
python3 arm/heal_test.py                    # the whole scenario; finds the arm on USB by itself
```
**Your part:** when the screen says `▶▶ NOW` (after the arm's first loop), hold your hand **5–15 cm** in front of
the sensor for about a second, then take it away. Don't go closer than 2 cm: below that the pod reports
`out_of_range`, which the gate doesn't read as a distance, so the fault won't show. During the three loops at the
end you can do it again: the arm pauses and carries on once you take the hand away.

Useful flags:
- `--view brief` shortens the thinking; `--view off` hides it.
- `--hand-after 2` waits two loops before the prompt. `--loops-max` caps how long it waits for you: 20 loops,
  ~7 min.
- `--hold-s 5` makes the arm wait longer, paused, before it backs off and flails.
- `--final-loops 5` runs more proof loops at the end.
- `--keep` leaves the pod as the agent left it. `--runs 3` repeats the scenario.
- `--no-arm` moves nothing: the job runs its timing and sensor checks only (`--dry-run`), and the hand still
  counts.
- `--arm-args "--speed 0.5"` plays the wave at half speed.
- `--sim` runs the real model against the simulated pod, arm and hand in `tests/simpod.py`. The hand comes
  30 s in, during loop 2. It's a rehearsal that proves nothing about the bench.

The test runs seven stages:
1. **Reset to v1.0.** The gate must pass (checked without moving).
2. **Deploy v1.8.** The bug must be dormant: the gate passes and podd reports nothing.
3. **The arm loops; you bring the hand.** Its log streams live. The checks:
   - at least one full loop played first;
   - the arm paused for the hand, and the samples that paused it agree with their echo;
   - after the hand left, the reported distance stayed under 200 mm while its echo said clear;
   - the arm flailed and gave up (exit 4);
   - podd opened `us_mismatch`.
4. **The agent.** Its thinking streams, then each tool call with the harness's decision and a summary of each
   result. You see the rollback, then `ARM JOB OK`, `playback: 663 of 663 frames` and the range reached per
   joint. If it goes on to a forward fix, you then see `src_edit`, `fw_build`, `fw_flash` of its build and a
   second `ARM JOB OK`.
5. **Three more loops**, run by the test with the sensor watched: every loop and frame must play. **If the agent
   left its own build running**, the screen says `▶▶ OPERATOR` again: bring the hand after loop 1. The arm must
   pause and carry on. That is the only test of the agent's fix against the trigger: it can't bring a hand itself,
   and with nothing near, a build that still has the bug looks fine.
6. **Checks on the bench**, not taken from the agent's words:
   - the agent's first flash was a verified rollback to known-good;
   - the chip runs known-good, or the agent's own build that `fw_flash` verified;
   - the agent ran the job after its last flash, and that job's recorded motion matches the plan;
   - the proof loops passed (and, on the agent's build, the arm carried on after the hand);
   - a fresh gate run passes and triage is clean;
   - the agent named the root cause and reported the motion it checked.
7. **Reset.**

Exit 0 means every check passed.

**Step by step, if you'd rather drive each stage yourself** (from `firmware/`):
```bash
ARM=$(ls /dev/serial/by-id/*1a86* | head -1)
demo/scenario start 08                                                    # plant the bad firmware (dormant)
~/lerobot-venv/bin/python ../arm/safe_replay.py --arm-port $ARM --on-blocked reflex --loops 20 --watch
#   ...hand in front of the sensor for a second after loop 1: the arm pauses, then flails, exit 4
cd harness && python3 agent.py run --tools arm --arm-port $ARM --blind-since-deploy --ollama-url http://192.168.50.1:11434
cd .. && python3 ../arm/arm_status.py report                              # what the agent's job did
~/lerobot-venv/bin/python ../arm/safe_replay.py --arm-port $ARM --on-blocked reflex --loops 3 --watch
demo/scenario reset
```
With `start 07` instead, the old always-on fault needs no hand: the plain job (`--on-blocked reflex`) flails
straight away.
Replay any run later: `python3 firmware/harness/view.py` (newest) or `view.py firmware/harness/runs/<dir>`.

### No hints for the agent
The agent sees only the harness's tools. `python3 arm/hint_audit.py` deploys v1.7 (the always-on fault: it needs
no hand), captures everything those
tools would show it, and flags any answer term (uint16, 16-bit, truncation, overflow, cast, the scenario
name...) outside the firmware source; exit 0 means no leaks. What keeps it clean:
- The release is the bare one-line change, with a neutral commit message ("us: tidy the distance conversion")
  and no explanatory comment.
- v1.8's commit message and comments describe the feature as meant (a near-object hold), never the mistake.
  `tests/test_heal_loop.py` checks that what wakes the agent on v1.8 has no answer term in it: `held_mm`,
  refresh, keep-alive, latch, stale, stuck, the version, or the hold time.
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
- v1.8's `near_hold()`, compiled from the patch with gcc (and the fix), checked ping for ping against the
  simulator. The checks: right before a hand, right during it, stuck after it (the fix lets go after 500 ms),
  and the arm's own pass at 338 mm never held. The patch never uses `HCSR04_HOLD_MS` and nothing clears
  `held_mm`.
- podd's rules and the real gate code on simulated samples.
- The reflex's limits on the simulated arm: held joints, swing and speed caps under any flags, back home,
  irregular motion, a real obstacle that clears.
- `--loops` and `--watch`: a passing hand pauses the arm, which carries on where it was. A hand that stays makes
  it back off, flail and go back. v1.8 makes it flail after the hand has left and give up at rest. Without an
  arm the same job runs dry.
- The motion report.
- The real agent loop, driven by a scripted model through a good fix, a wrong diagnosis, a job run before the
  fix, and the arm-run cap. Forward fixes: the agent's edited `hcsr04.c` is compiled with gcc in the simulator
  and runs ping by ping, so a correct fix lets go after the hand and a wrong one doesn't; also a fix without the
  rollback first, and a build flashed after the last arm check.
- The live view and its replay.
- What wakes the agent on v1.8: no answer terms in it.
- `heal_test.py`'s full sequence on the simulated pod, arm and hand, plus two aborts: the sensor really blocked,
  and nobody bringing a hand.
## Recording and playing primitives by hand (`prims/`, Andre's `arm/teleop-recording`)

_Merged from Andre's branch on Sun Nov 8 so the arm host can drive the real arm with `prims.Arm`. The flat files above (`wave.json`, `recording_*.json`, `naive_follower.json`, `prim-record.sh`) are byte-identical copies of the ones under `prims/library/`, `calibration/` and `bin/`._

One SO-101 arm, no teleoperation: switch its torque off, move it by hand, and save the
movement as a small JSON trajectory (a *primitive*) in `prims/library/`. LeRobot 0.7.0 is
pinned (see `docs/so101-research.md`).

### Setup (once per machine)
```bash
cd arm && ./setup.sh          # venv + LeRobot; creates arm.env
ls /dev/tty.usbmodem*         # with the arm plugged in (USB + 12 V power)
```
Put that port in `ARM_PORT` in `arm.env`.

The arm's calibration is checked in at `calibration/naive_follower.json` (`ARM_ID`). It is
written to the motors automatically the first time a machine connects, so no calibration
step is needed for this arm.

### Record
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

### Recordings
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
