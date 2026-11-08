# arm-sim

The simulated SO-101 bench, and since Sun Nov 8 the whole self-healing loop.
- **In sim:** a MuJoCo arm, a virtual sensor pod read by Olir's real `podd`, Romulus, and Hermes driven by a Naïve card.
- **On the bench:** the same code on the real arm (`--arm real --pod real`).

The morning checklist and the open decisions are in [`MORNING.md`](MORNING.md).

## Everything together, in one place
**Setup:** once per machine, after the setup below, add the Romulus client to this venv:
```
uv pip install --python .venv/bin/python -e ../../packages/contracts -e ../../tools/fake_device
(cd ../.. && uv sync --all-packages)          # the edge bridge's env
```

**Start and stop:**
- `demo/up.sh [--gif]` starts:
  - Romulus (Docker, `make up` + `make seed`, if it isn't up);
  - `pod-1` registered as a sensor pod;
  - the arm host, with a virtual pod and Olir's `podd`, reporting as `arm-twin-1`;
  - Kent's edge bridge.
- `demo/down.sh` stops them, except Romulus (run `make down` from the repo root to stop it).

**The stories** (`PYTHONPATH=. .venv/bin/python -m armsim.stories …`):

| Command | What happens |
|---|---|
| `a` (`--image 07` or `02`) | A bad pod image goes out. Image 07, the bench demo's fault (#54), truncates the distance math. The approach sees "reads 12 mm where the pose says 110 mm" and parks; podd flags `us_mismatch`. The pod healer rolls back (a stand-in in sim). The check at home passes and the arm resumes. |
| `b` | A stuck sensor mid-job: "Distance stuck at 130 mm while the arm moved 29 mm". The arm parks, the sensor restarts through podd, the job retries. |
| `b --hermes` | The same, but the job is a new Naïve board card and Hermes does it, ending with `RESULT:` on the card. |
| `stall` | A dead elbow servo: a checked retry fails, the incident is handed to a person (`hardware`), and Romulus opens a work order. |
| `redo` (`--manager fake`) | A wrong action (sim only): the block is bumped and the grasp misses. The arm reverses, the manager corrects the approach (±40 mm, never joint angles), and the arm redoes it; after two misses it goes to a person. |


**The autonomous loop, Mission Control and the healer's manager** (all `PYTHONPATH=. .venv/bin/python -m …`, with the arm host up):

| Command | What it does |
|---|---|
| `armsim.orders add pick\|wave --board naive` | Places an order: an `ARM JOB ord-…` card on the Arm jobs board (`--board file` uses `runs/board.json`) |
| `armsim.dispatch --board naive --exec hermes` | Claims each order card, has Hermes do it, and closes or blocks the card. One dispatcher per arm (lock next to the arm host's socket) |
| `armsim.dispatch --board file --exec scripted --manager fake\|hermes [--wrong-action redo]` | The scripted commander, healing through the worker → manager loop (`armsim/worker.py`, `manager.py`, `escalation.py`): plain code checks the manager's one-action plan against an allowlist, and reads the host again just before it runs it. `--manager hermes` won't start unless the manager's `/v1/toolsets` shows only delegation and memory. A manager `park` hands the incident to a person (the next order waits); a `pod_rollback` plan runs as a wait for the pod's own healer (recorded `wait_for_pod`). `--worker-lm ollama --worker-model lfm3:1.7b` adds the small model (`workerbench/RESULTS.md`) |
| `armsim.soak run --board naive --with-dispatcher hermes --every 600 --hours 4` | The soak: an order every 10 min, half with a planted fault, and a report (`SOAK.md`) |
| `armsim.mission --board naive` | Mission Control on `http://localhost:8765/?look=mission\|neon\|light`: the 3D twin, the story banner, jobs, the commander feed, the heal timeline, the pod and the fleet, plus presenter buttons (hotkeys 1–8) |
| `armsim.level3 train` / `audit runs/<run>.jsonl` | Healer level 3: the run-audit classifier (`docs/healer-data-levels.md`) |

The manager is a Hermes profile on James's Mac: `hermes-manager/README.md`. The design is `docs/worker-manager-healer.md`.

**What each piece does:**
- **The watchdog** (`host.py`) speaks one sentence, parks the arm, and closes an incident only after `verify()`: home, fresh readings, and podd agreeing. Pod incidents park the arm too.
- **The arm reporter** (`romulus.py`) sends a check-in every 10 s, plus one incident report (and a `log_window` recording) per incident once it is over.
- **Hermes** reads the board through `naive_board`, a filter in `~/.hermes/config.yaml` that exposes only the five card tools.

**The real arm** (`armsim/real.py`):
- `RealArm` wraps Andre's `prims.Arm` (arm/arm.env, the checked-in calibration).
- It moves between the poses taught on the arm (`arm/bin/prim-record.sh --pose rest home above_block pick`).
- Until `armsim/real_calibration.json` holds measured joint offsets, IK and the distance prediction stay off, so only these checks run:
  - frozen sensor;
  - joint stall;
  - podd's own incidents.
- Run it on the Pi with podd up:
  ```
  ARMSIM_ROMULUS=http://<mac-ip>:8000 demo/up.sh --real
  ```
  Then run the stories with `--real`, where faults are planted by hand.
- The adapter's mock tests: `PYTHONPATH=. .venv/bin/python tests/test_realarm.py`.

**Setup, once:**
1. `./setup.sh` fetches MuJoCo Menagerie's SO-101 into `vendor/`.
2. `uv run --python 3.12 --with-requirements requirements.txt python -m armsim poses` writes the poses and the wave.

**Run:** `uv run --python 3.12 --with-requirements requirements.txt python -m armsim <command>`
- `demo` goes home, waves, and goes home again. It writes `runs/<id>.jsonl` (an `arm-run/v1` record) and `runs/<id>.gif`.
- `view` shows the same moves in MuJoCo's viewer.

**To watch it live on macOS:** the viewer needs `mjpython`, and `mjpython` needs a Python with a shared `libpython`. `uv`'s standalone Python doesn't have one, so make a venv once from your framework or conda Python:
```
uv venv --python "$(command -v python3)" .venv
uv pip install --python .venv/bin/python -r requirements.txt
```
Then run `.venv/bin/mjpython -m armsim.host --view`. That opens the arm host with a live window, paced to real time.

**Commander (Hermes):**
1. Start the arm host:
   ```
   uv run --python 3.12 --with-requirements requirements.txt python -m armsim.host --gif
   ```
   Add `--view` (with `mjpython`) to watch it live.
2. Link the plugin: `ln -s "$PWD/hermes-plugin/so101-arm" ~/.hermes/plugins/so101-arm`.
3. Enable it: `hermes plugins enable so101-arm`.
4. Send a command: `hermes chat -q "wave, then go home" --oneshot -t so101_arm`.

The plugin's tools ask the host to run one skill each; the LLM never sends joint angles. Its `pre_tool_call` guard blocks motion while a fault is open or a servo reports an alarm. The host listens on `$TMPDIR/armsim.sock`; set `ARMSIM_SOCK` to change that.

**Watchdog (level 1, plain code):** `armsim/level1.py` writes findings in the same shape as Olir's `detect.py`. The rules:
- `us_frozen`: zero echo jitter.
- `us_stuck`: the reading is flat while the arm moves.
- `us_scale` / `us_offset`: the reading disagrees with the kinematic prediction.
- `us_noisy`: too much jitter while the arm is still.
- `us_missing_echo`: no echo where the pose says a surface is in range.
- `joint_stall`: a move that didn't arrive.

The first finding speaks its sentence (`say`; `ARMSIM_SPEAK=live|file|off`), halts and parks the arm, and blocks motion until a repair is verified.

**Fault factory:** `python -m armsim.factory --n 1000 --workers 8` runs labeled sim runs with planted faults, about 0.1 s each across 8 cores. It writes `arm-run/v1` files plus `index.jsonl` to `data/sim-faults/`, and prints which faults level 1 caught.

**Level 2:** `level2/chatts_test.py` asks ChatTS-14B whether it spots a frozen copy of the pod's ultrasonic sample. `level2/run_on_pod.sh` runs it on a RunPod GPU with a 2-hour cap and then stops the pod.

**Units and formats:**
- Arm joints are in degrees and the gripper is 0–100 (0 = closed), the same as LeRobot and Andre's primitive JSON.
- Distance rows use the pod's `D` fields (`mm`, `us`, `st`).

**Known limits:**
- The sim's zero pose isn't the real arm's calibrated zero.
- Pointing straight down, the fingertips can't rise above about 9 cm at 0.17–0.25 m out, because wrist flex tops out near 95°. That's why the block is 3 cm tall.
