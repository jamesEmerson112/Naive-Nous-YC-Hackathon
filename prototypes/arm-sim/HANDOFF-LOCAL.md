# Handoff: the real-arm session (local Claude, at the bench)

_From James's cloud session, Sun Nov 8, ~9 AM PST. For a Claude Code session on James's Mac, which is wired to the Pi over Ethernet, with Andre and Olir at the bench. The code freeze is at 10 AM and hacking stops at 11 AM (`docs/hackathons.md`)._

**The goal:** the real SO-101 and the real sensor pod replace the sim, and the stories run on the bench: `a` (bad pod image 07), `b` (stuck sensor) and `stall` (dead elbow). Everything below was proven in sim only. The real arm has run only the adapter's 5 mock tests (`tests/test_realarm.py`).

## 1. Which code, which branch
- **Run `main`.** Do `git pull origin main`. `main` is the last version tested end to end in sim: Mission Control, the autonomous loop, the soak, the stories and the real-arm adapter.
- **Not `james/arm-sim-ydcsss` yet.** That branch is `main` plus the worker/manager review fixes the cloud session is still writing and checking. They change `armsim/dispatch.py`, which every arm run uses, so don't put them on the real arm unchecked. When they pass, the cloud session merges them into `main`. Then `git pull origin main` again, and `main` and the branch are the same.
- **Where bench changes go:** commit small, tested changes straight to `main`. The repo rules apply (`CLAUDE.md`):
  - `git pull --rebase origin main` before you push;
  - never force-push;
  - no AI co-author lines.
  Never push to `james/arm-sim-ydcsss`: the cloud session pushes there every few minutes.
- **Before you push anything under `prototypes/arm-sim/armsim/`,** run the 8 test files from `prototypes/arm-sim`:
  ```
  for t in tests/test_*.py; do PYTHONPATH=. .venv/bin/python "$t" || break; done
  ```

### Files: yours vs the cloud session's
| Yours (edit freely) | The cloud session's (don't edit until it says it's done) |
|---|---|
| `armsim/real.py`, `armsim/real_calibration.json`, `arm/` (with Andre), `demo/up.sh`, `samples/`, this file | `armsim/dispatch.py`, `worker.py`, `manager.py`, `escalation.py`, `redo.py`, `host.py`, `stories.py`, `mission.py`, `mission/index.html`, `tests/`, `hermes-manager/`, `README.md` |

**A bug in one of the cloud session's files:** write down the problem (what you ran, what happened, the log lines) for James to paste into the cloud session. Don't patch it here.

## 2. Where things run
| Machine | Runs |
|---|---|
| **James's Mac** | Romulus (Docker: `make up && make seed` from the repo root), Hermes with the `so101-arm` plugin, the `naive` CLI, and the stories, through a forwarded socket |
| **The Pi** | Olir's `podd` (it owns the pod's serial port), the arm host (`demo/up.sh --real`, which reports to Romulus as `arm-1`, `armsim/host.py:618`), Kent's edge bridge, and Mission Control |

**Mission Control has to run on the Pi,** next to the arm host. It draws the arm from the host's snapshot file (`$ARMSIM_STATE`, `armsim/mission.py:87-116`), and that file can't be forwarded the way the socket can.

## 3. Steps
**R0. The Pi** (with Andre and Olir):
1. SSH in, then `git pull origin main` in the repo.
2. Python 3.12 venv in `prototypes/arm-sim`:
   - `lerobot[feetech]==0.7.0`, `-r requirements.txt`;
   - the two editable installs from the README's "Everything together";
   - `uv sync --all-packages` at the root, for the edge bridge;
   - `prototypes/arm-sim/setup.sh`, which fetches the SO-101 MuJoCo model. The real arm host needs it for its kinematics mirror and won't start without it.
3. **Pin both serial ports** by `/dev/serial/by-id`:
   - `ARM_PORT` in `arm/arm.env`;
   - the pod's port for `podd`, as `POD_PORT`.
   Only `podd` may open the pod's port (`firmware/healer/README.md:36`). With `POD_PORT=auto`, `podd` falls back to the first `/dev/ttyACM*` when the J-Link is missing, and that can be the arm's port (`firmware/healer/healer/serialport.py:24-28`).
4. Start `podd` from `firmware/`: `POD_PORT=/dev/serial/by-id/<J-Link> healer/bin/podd &` (`firmware/healer/README.md:31`).
   - If an edge bridge is already running, start the arm host on its own instead of with `up.sh --real`: `up.sh` starts a second bridge that also reports as `pod-1`.
   - The command: `PYTHONPATH=. .venv/bin/python -m armsim.host --arm real --pod real --var <repo>/firmware/healer/var --romulus http://192.168.50.1:8000`.
5. Teach the poses with Andre: `arm/bin/prim-record.sh --pose rest home above_block pick`.

**R1. The Mac:**
1. **Before `make up`, set `TECHNICIAN_MODE=off` in the repo root's `.env`.** Since Kent's #79, the default is `live` (`cloud/src/romulus_cloud/settings.py:199`). In live mode, every unresolved stall from a real device (`arm-1`, `pod-1`) opens a Naïve Technician case. With Kent's Naïve keys in `.env`, each case is a paid session (up to $3) plus a real email; without them, it is a failed case that says "Naïve technician failed" on the incident. Turn it back on only for Kent's Technician beat (`docs/naive-technician/runbook.md`). With nothing set, each stall test would open a case.
2. Romulus: `make up && make seed`.
3. Revert the Hermes test setting: `hermes config set compression.threshold_tokens null`.
4. Leave the manager profile (`hermes-manager/`) alone until the cloud session's fixes are on `main`.

**R2. Start the arm** on the Pi:
```
ARMSIM_ROMULUS=http://<mac-ip>:8000 prototypes/arm-sim/demo/up.sh --real
```
Then forward its socket to the Mac. Hermes and the stories use it:
```
ssh -N -L "$TMPDIR/armsim.sock:/tmp/armsim.sock" pi@<pi>
```

**R3. The stories,** from `prototypes/arm-sim` on the Mac: `PYTHONPATH=. .venv/bin/python -m armsim.stories <story> --real`. With `--real`, each story asks for its fault to be planted by hand (`armsim/stories.py`):

| Story | What the person does when asked |
|---|---|
| `a --real` | Olir: `firmware/demo/scenario start 07` |
| `b --real` (add `--hermes` for the Naïve card plus Hermes run) | Olir: the stuck-sensor scenario on the pod |
| `stall --real` | Unplug the elbow servo's cable, or block the elbow (see the note below) |

- **The stall needs the elbow blocked both ways.** Use a zip tie or the unplugged cable. With foam, or a hand that only blocks the reach, the checked move home still arrives, `verify` counts the stall fixed, and no work order opens (`docs/naive-technician/demo-clip.md`, "How to jam it").
- **Limit the stall to 2 takes, 2 minutes apart.** The servo heats up while stalled (same doc).
- **Which Romulus gets the stall decides whether Kent's Technician runs.** Kent's own stack runs live, and his runbook points the Pi at it (`ARMSIM_ROMULUS=http://<kent-ip>:8000`, `runbook.md`); there, each stall take is a Naïve session and a real email. For plain bench tests, point the Pi at your Mac with `TECHNICIAN_MODE=off`.
- **The sim arm next to the real pod (`--arm sim --pod real`) reports as `arm-twin-1`, kind sim.** It used to report as the real `arm-1`; fixed on `main` (`armsim/host.py`, `romulus_identity`).
- **What an unplugged cable does is untested.** The host may report a missing servo rather than a stall *(inferred)*. Try it once, and write down what happens.

**R4. Mission Control** on the Pi:
```
PYTHONPATH=. .venv/bin/python -m armsim.mission --board file --host 0.0.0.0 --romulus http://<mac-ip>:8000
```
- It prints `http://<pi-ip>:8765/?token=…`; open that on the Mac (`armsim/mission.py:38-43`). From another machine, the buttons need that token.
- For the autonomous loop on the real arm without Hermes, run this on the Pi too:
  ```
  PYTHONPATH=. .venv/bin/python -m armsim.dispatch --board file --exec scripted
  ```
- **With `--board naive`,** Mission Control and the dispatcher need the `naive` CLI logged in on the Pi *(inferred: the board talks to the CLI, `armsim/board.py`)*. If it isn't, keep the file board here; the Naïve card plus Hermes path is story `b --hermes --real` from the Mac.

**R5. Data:** copy the first real runs (`runs/<id>.jsonl`) into `samples/`, plus Olir's overnight `podd` slice. Level 3 was trained on sim only, so these are its real test (`docs/healer-data-levels.md`).

## 4. Known gaps on the real arm
- **Calibration:** until joint offsets are measured into `armsim/real_calibration.json`, IK and the distance prediction stay off. Only the frozen-sensor and joint-stall rules and `podd`'s own incidents can fire (README, "The real arm"). Story `a` needs only `podd`'s `us_mismatch`, so it doesn't depend on the offsets.
- **The wrong-move redo** (`stories redo`) is sim only. `stories redo --real` refuses to run (`armsim/stories.py:166-168`), and the host refuses the scan and offset moves off the sim.
- **The real Hermes manager** (`--manager hermes`) waits for the cloud session's fixes.

## 5. Safety
- **No model sits in the real-time loop.** The watchdog's plain-code rules park the arm. Hermes's `pre_tool_call` guard blocks motion while an incident is open, and the LLM never sends joint angles (README).
- **Someone stays at the arm,** with its power in reach, whenever it moves *(inferred)*.

## 6. Bench log (add a line per run, newest last)
| Time | Story or step | Result | Notes and log paths |
|---|---|---|---|
| Sun 9:14 AM | Setup on James's Mac. The arm and pod will plug into the Mac by USB, so no Pi. | Backend and frontend up. Arm not plugged in yet; nothing moved. | Romulus rebuilt on main (`make up && make seed`, `/healthz` ok). Mission Control on `localhost:8765` (`runs/logs/mission.log`). LeRobot 0.7.0 in the arm-sim venv, and `arm/.venv` made. Pose guard `0e18ee8` on main. All 8 test files pass, run with `TMPDIR=/tmp` and a venv path with no spaces (two macOS path issues in the tests). Stopped 5 leftover sim edge bridges. Still to do: Andre's poses, the ports, podd (`HEALER_VAR=$TMPDIR/pod-real`). |
| Sun 9:44 AM | Setup on Olir's Pi (`olirpi`), cabled to the Mac. The Mac is `192.168.50.1` on its USB Ethernet adapter, the Pi `192.168.50.2`. The arm and pod are on the Pi. | Ready, no motion. `status` answers through the forwarded socket. `arm-1` and `pod-1` check in to the Mac's Romulus. Mission Control draws the real arm's joints and the pod's distance. | The Pi's repo is on main `29cdc17`. Arm-sim venv on the Pi (Python 3.13, LeRobot 0.7.0); `setup.sh` run. `ARM_PORT=/dev/serial/by-id/usb-1a86_USB_Single_Serial_5AB9069018-if00`. `podd` restarted with `POD_PORT=/dev/serial/by-id/usb-SEGGER_J-Link_000772067452-if00`. The arm host was started on its own because Olir's bridge was already running; its log is `runs/logs/host.log` on the Pi. Servos read 12 V, 31–33 °C, calibration matching the file. Mission Control on the Pi at `:8765`, tunnelled to the Mac's `localhost:8765` with the socket (`runs/logs/tunnel.pid` on the Mac). Still to do: Andre's poses (the guard refuses every move until they exist), then the stories on James's go. |
| Sun 10:05 AM | First real move: Andre's `wave.json`, run through the arm host on the Pi (`skill wave`). It's his recorded chips pick-and-place (bag next to the arm into the box), not a wave. | Ran in 17.9 s and arrived (max error 0.53°). Ended folded; then `park` (torque off). No incidents. | Poses: `rest` and `home` are the folded pose read live (`9297646`); `above_block` and `pick` aren't taught and are refused. The servos then showed overload bits that jumped between joints. Cause: `real.py` masked LeRobot 0.7's already-signed `Present_Load`, so -78 read as 94.6% (the base logged a median of 2.78 N·m while folded). Fixed in `ee507e2`; after restarting the host, all alarm bits read 0. LeRobot also clamped some wrist steps (`MAX_RELATIVE_TARGET`). Log: `runs/20261108-100302-6eeb80.jsonl` on the Pi. |
