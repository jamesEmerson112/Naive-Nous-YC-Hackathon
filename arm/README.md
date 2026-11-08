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
