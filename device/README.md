# device: the code that runs on the robot

`device/` is the code package. The cloud ships it as a tarball of this folder, and code patches use paths
relative to it, like `--- a/robot/control.py`. The harness bundles (`../harness/`, which ship separately)
and the benchmark (`../bench/`) live outside it.

## Layout

| Path | What it is |
|---|---|
| `robot/` | The robot app: hold an object while the ultrasonic sensor drives the arm. `SPEC.md` is the contract, `config.toml` the settings, `hal.py` the only hardware access (`backend = "mock"` or `"real"`). |
| `sim/` | Fake hardware: `FakeArm` (the parts of LeRobot's SO101Follower the app uses, plus fault injection through `arm_faults.json`) and `TraceSensor` (replays recorded HC-SR04 traces from `traces/`). |
| `supervisor/monitor.py` | Runs the app on a scenario and classifies the outcome. Tier 1 is a crash, tier 2 a hang (stale heartbeat; it dumps the stack with SIGUSR1), tier 3 a spec violation (gripper above 15 while holding). |
| `tests/` | The test suite: SPEC.md's rules on the fake hardware, and the supervisor's `classify()`. |

## Test

From `device/`, with only this folder present:

```bash
uv run --no-project --with pytest pytest
```

It needs Python 3.11 or newer and nothing else: no network once pytest is installed, and no Ollama,
LeRobot or torch. It takes about a second. The tests run the app through the supervisor and check
SPEC.md's rules, not exact arm commands, so a legitimate patch still passes: the gripper never opens, the
arm lifts away when something is near and holds its pose when the area is clear, bad readings are ignored,
flaky servo reads are retried, the loop never blocks on a dead sensor, and a missing servo or a dead bus
still stops the app. `tests/test_classify.py` covers the supervisor's `classify()`.

The thresholds at the top of `tests/test_spec.py` mirror `robot/config.toml` (near 15 cm, far 60 cm,
valid 2 to 400 cm, hold-pose shoulder lift -20). A patch that changes those config values on purpose
must update them too.

## Switching to the real hardware (Pi 5)

Not tested on the Pi yet; `RealArm.park()` is untested.

1. Python 3.12 or newer (LeRobot 0.7 needs it; Pi OS ships 3.11): `uv venv --python 3.12`, then
   `uv pip install "lerobot[feetech]==0.7.0" gpiozero lgpio`. Building lgpio may need
   `sudo apt install swig liblgpio-dev`.
2. Find the arm's port with `lerobot-find-port`, put it in `config.toml` (`[arm] port`; prefer the
   `/dev/serial/by-id/...` path), and calibrate once with the same id as `config.toml`:
   `lerobot-calibrate --robot.type=so101_follower --robot.port=<port> --robot.id=follower`.
3. HC-SR04: TRIG on GPIO23 and ECHO on GPIO24 (BCM). ECHO is 5 V, so it needs a divider down to 3.3 V.
   The Pi 5 needs gpiozero's lgpio pin factory, which `hal.py` sets.
4. Set `backend = "real"` in `config.toml` and run `python app.py` from `robot/`. It runs forever at
   `loop_hz`; only one process can own the servo port.

## Not built yet

- An always-on supervisor daemon on the Pi that records the live sensor trace, writes `incident.json` and calls the healer (today only the benchmark in `../bench/` drives it).
- The cloud side of the device: check-in, incident upload from `outbox/`, and harness and firmware pulls (Kent's v0 contracts).
- The pi agent adapter, the STM32 firmware tasks (iteration 2) and the camera.
