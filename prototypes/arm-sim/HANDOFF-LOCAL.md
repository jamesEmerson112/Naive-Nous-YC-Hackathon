# Handoff: the real-arm session (local Claude, at the bench)

_From James's cloud session, Sun Nov 8, ~9 AM PST. For a Claude Code session on James's Mac, which is wired to the Pi over Ethernet, with Andre and Olir at the bench. The code freeze is at 10 AM and hacking stops at 11 AM (`docs/hackathons.md`)._

**The goal:** the real SO-101 and the real sensor pod replace the sim, and the stories run on the bench: `a` (bad pod image 07), `b` (stuck sensor) and `stall` (dead elbow). Everything below was first proven in sim only.

**Where it stands (Sun ~10:35 AM):** the real arm on the Pi has run Andre's recorded chips pick-and-place 4 times through the arm host. The 4th was a Naïve card that Hermes ran by itself: order → card → dispatcher → Hermes → `RESULT:` → done. No alarms and no incidents. The fault stories (`a`, `b`, `stall`) haven't run on the real arm yet. See the bench log in section 6.

## 1. Which code, which branch
- **Run `main`.** Do `git pull origin main`. `main` is the last version tested end to end in sim: Mission Control, the autonomous loop, the soak, the stories and the real-arm adapter.
- **`james/arm-sim-ydcsss` is merged into `main`** (Sun ~10:35 AM, on James's call). It carries the cloud session's worker/manager review fixes:
  - `host.py`: a hand-off now stops the host's own automatic moves;
  - `dispatch.py`: the scripted/manager path (the Hermes card path is unchanged);
  - `worker.py`, `manager.py`, `escalation.py`, `redo.py`, `stories.py` and `hermes-manager/`.

  All 8 test files passed on the merge. Later pushes from the cloud session to that branch need merging again.
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
2. Python 3.12+ venv in `prototypes/arm-sim` (the Pi's own Python 3.13 works with LeRobot 0.7.0):
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
5. **Poses.**
   - The arm host's skills move between named poses in `arm/prims/library/poses.json`.
   - Andre had no time to teach them. So `rest` and `home` are both the folded pose, read live from the servos (`9297646`). All of Andre's recordings start and end there.
   - `above_block` and `pick` aren't taught. `RealArm.goto` refuses them, so `reach`, `approach` and `lift` stop before any servo command.
   - With no `poses.json` at all, every move is refused. Without that, `host.Skills` would fall back to the sim's poses, which don't match the real arm.
   - To turn on pick-and-place, Andre runs `arm/bin/prim-record.sh --pose above_block pick` with the arm host stopped (it holds the arm's port). His recorder needs `arm/.venv`; run `arm/setup.sh` on the Pi first.

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
**What ran on the bench:**
- **Network:** the Pi is `pi@192.168.50.2` on a direct cable. The Mac's USB Ethernet adapter was set by hand: `sudo networksetup -setmanual "AX88179B" 192.168.50.1 255.255.255.0`.
- **One tunnel carries both** the socket and Mission Control:
  ```
  ssh -N -o ExitOnForwardFailure=yes -o StreamLocalBindUnlink=yes -L "$TMPDIR/armsim.sock:/tmp/armsim.sock" -L 8765:127.0.0.1:8765 pi@192.168.50.2
  ```
- **Two macOS catches:**
  - The Mac's SSH key has to be on the Pi first: run `ssh-copy-id pi@192.168.50.2` in the Terminal app.
  - The Claude app's terminal pane gets "No route to host" for the Pi, because it lacks macOS's Local Network permission. Run SSH from Claude Code's own shell or from the Terminal app.

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
- **Through the tunnel,** open `http://localhost:8765` on the Mac. It arrives on the Pi's loopback, so no token is needed. Mission Control's job panel shows the Pi's file board, so a Naïve card job shows only as the arm moving.
- **The Naïve card + Hermes job that worked (from `prototypes/arm-sim` on the Mac):**
  ```
  PYTHONPATH=. .venv/bin/python -m armsim.orders add wave --board naive
  PYTHONPATH=. .venv/bin/python -m armsim.dispatch --board naive --exec hermes --max-jobs 1 --every 10
  ```
  - Hermes isn't installed on the Pi, so the dispatcher runs on the Mac.
  - Naïve answered `429 rate_limited: the service is at capacity` several times. The dispatcher never starts a job it couldn't claim, and it retries at each poll.
  - A card left in `doing` by a failed claim is treated as stale and blocked. Move it back with `naive board move <board> <card> --status todo`.
  - There's no Naïve-free Hermes path yet. The plan for one is a new `armsim/hermes_file.py` executor over the file board, with Hermes on `-t so101_arm` only.

**R5. Data:** copy the first real runs (`runs/<id>.jsonl`) into `samples/`, plus Olir's overnight `podd` slice. Level 3 was trained on sim only, so these are its real test (`docs/healer-data-levels.md`).

## 4. Known gaps on the real arm
- **The 3D twin is on.** `armsim/real_calibration.json` is `estimated` (`f698caf`): offsets 0 and signs +1, checked by camera against the folded pose and Andre's motion. LeRobot's calibrated degrees already line up with the MuJoCo model. `estimated` draws the twin only (`RealArm.kinematics_ok`).
- **Calibration:** until joint offsets are `measured` into `armsim/real_calibration.json`, IK and the distance prediction stay off. Only the frozen-sensor and joint-stall rules and `podd`'s own incidents can fire (README, "The real arm"). Story `a` needs only `podd`'s `us_mismatch`, so it doesn't depend on the offsets.
- **The wrong-move redo** (`stories redo`) is sim only. `stories redo --real` refuses to run (`armsim/stories.py:166-168`), and the host refuses the scan and offset moves off the sim.
- **The real Hermes manager** (`--manager hermes`): the cloud session's fixes are merged, but the manager profile hasn't run against a real Hermes (`hermes-manager/README.md`).
- **Andre's `wave.json` is his chips pick-and-place** (the bag next to the arm, into the box), not a wave. The `wave` skill, Hermes's `arm_wave` and Mission Control's "Wave" button all play it. A recording replays a fixed path, so the bag must sit where it was when Andre recorded, or the gripper closes on air.
- **The ultrasonic sensor isn't on the arm** right now. podd reads a steady ~659 mm, so distance doesn't follow the arm, and story `b` needs it back on the wrist.
- **Story `a`'s pod healer needs a model.** `qwen3.5:4b` is pulled on the Mac, but Ollama listens only on localhost there. Run `server/start.sh` (Olir's relay) to serve it at `192.168.50.1:11434` for the Pi.
- **Camera:** Claude and the Terminal app never get a macOS camera prompt. Run ffmpeg from VS Code's terminal (VS Code already has camera access):
  ```
  ffmpeg -f avfoundation -pixel_format nv12 -framerate 30 -i "1:none" -vf "fps=1,scale=960:-1" -update 1 -y /tmp/armcam/latest.jpg
  ```
  Device 1 is the iPhone (Continuity Camera).

## 5. Safety
- **No model sits in the real-time loop.** The watchdog's plain-code rules park the arm. Hermes's `pre_tool_call` guard blocks motion while an incident is open, and the LLM never sends joint angles (README).
- **Someone stays at the arm,** with its power in reach, whenever it moves *(inferred)*.
- **Pose guard** (`armsim/real.py`): with no taught poses the real arm refuses every move, and an untaught pose (`above_block`, `pick`) is refused. The sim's poses never drive the real arm.
- **Servo load** is read signed (`ee507e2`). Before that fix, LeRobot 0.7's signed `Present_Load` showed up as false overloads.

## 6. Bench log (add a line per run, newest last)
| Time | Story or step | Result | Notes and log paths |
|---|---|---|---|
| Sun 9:14 AM | Setup on James's Mac. The arm and pod will plug into the Mac by USB, so no Pi. | Backend and frontend up. Arm not plugged in yet; nothing moved. | Romulus rebuilt on main (`make up && make seed`, `/healthz` ok). Mission Control on `localhost:8765` (`runs/logs/mission.log`). LeRobot 0.7.0 in the arm-sim venv, and `arm/.venv` made. Pose guard `0e18ee8` on main. All 8 test files pass, run with `TMPDIR=/tmp` and a venv path with no spaces (two macOS path issues in the tests). Stopped 5 leftover sim edge bridges. Still to do: Andre's poses, the ports, podd (`HEALER_VAR=$TMPDIR/pod-real`). |
| Sun 9:44 AM | Setup on Olir's Pi (`olirpi`), cabled to the Mac. The Mac is `192.168.50.1` on its USB Ethernet adapter, the Pi `192.168.50.2`. The arm and pod are on the Pi. | Ready, no motion. `status` answers through the forwarded socket. `arm-1` and `pod-1` check in to the Mac's Romulus. Mission Control draws the real arm's joints and the pod's distance. | The Pi's repo is on main `29cdc17`. Arm-sim venv on the Pi (Python 3.13, LeRobot 0.7.0); `setup.sh` run. `ARM_PORT=/dev/serial/by-id/usb-1a86_USB_Single_Serial_5AB9069018-if00`. `podd` restarted with `POD_PORT=/dev/serial/by-id/usb-SEGGER_J-Link_000772067452-if00`. The arm host was started on its own because Olir's bridge was already running; its log is `runs/logs/host.log` on the Pi. Servos read 12 V, 31–33 °C, calibration matching the file. Mission Control on the Pi at `:8765`, tunnelled to the Mac's `localhost:8765` with the socket (`runs/logs/tunnel.pid` on the Mac). Still to do: Andre's poses (the guard refuses every move until they exist), then the stories on James's go. |
| Sun 10:05 AM | First real move: Andre's `wave.json`, run through the arm host on the Pi (`skill wave`). It's his recorded chips pick-and-place (bag next to the arm into the box), not a wave. | Ran in 17.9 s and arrived (max error 0.53°). Ended folded; then `park` (torque off). No incidents. | Poses: `rest` and `home` are the folded pose read live (`9297646`); `above_block` and `pick` aren't taught and are refused. The servos then showed overload bits that jumped between joints. Cause: `real.py` masked LeRobot 0.7's already-signed `Present_Load`, so -78 read as 94.6% (the base logged a median of 2.78 N·m while folded). Fixed in `ee507e2`; after restarting the host, all alarm bits read 0. LeRobot also clamped some wrist steps (`MAX_RELATIVE_TARGET`). Log: `runs/20261108-100302-6eeb80.jsonl` on the Pi. |
| Sun 10:16 AM | Runs 2 and 3 of Andre's chips pick-and-place (`skill wave`), after the load fix, filmed on the iPhone through Continuity Camera. | Both replayed cleanly: 17.9 s, arrived (max error 0.53°), back to folded. All alarm bits 0, no incidents. Peak load 36% of stall. The gripper missed the bag both times; James says that comes from the recording and the arm works as expected. | Camera: ffmpeg from VS Code's terminal (VS Code has camera permission; Claude and Terminal never get a prompt) writes `/tmp/armcam/latest.jpg` once a second. Frames are in `/tmp/armcam/run2` and `run3` on the Mac. The ultrasonic sensor isn't on the arm right now (podd reads a steady ~659 mm), so distance readings don't follow the arm. |
| Sun 10:27 AM | Run 4: the first Naïve card + Hermes job on the real arm. `orders add wave --board naive` made card `crd_ccag3pcjdydr3vtdn8yrfc6cms`; `dispatch --board naive --exec hermes --max-jobs 1` ran it. | Done with nobody typing after the order. Hermes sent `go_home`, `wave` (Andre's chips motion) and `go_home`, then posted `RESULT:` and closed the card: "Waved hello and returned home; no faults." 107 s from claim to done (Hermes ran 95 s, 25 tool calls). | The first three tries stopped at the claim on Naïve's `429 rate_limited: the service is at capacity`. The dispatcher didn't move the arm until the claim landed. Camera frames are in `/tmp/armcam/run5` on the Mac; the motion shows in row 4 of `sheet.jpg`. |
| Sun 10:48 AM | Run 5: a Naïve card + Hermes job on the merged main (`a166e51`, with `james/arm-sim-ydcsss`), then the 3D twin turned on (`f698caf`). | Card `crd_qh49rphak6ab81q2v4p8dy1qw3` done: "Waved hello and returned the arm home; no faults required healing." Hermes sent `go_home`, `wave` and `go_home`; 254 s from claim to done. After the restart, Mission Control draws the real arm in 3D, folded as it sits. | Naïve was degraded: 14 `rate_limited: the service is at capacity` errors logged today (request ids in `runs/events.jsonl` on the Mac). A live test of 5 reads gave 1 at-capacity, 1 `internal_error` and 3 slow successes. The dispatcher waited 12 minutes in all. Twin calibration: `real_calibration.json` `estimated` (offsets 0, signs +1), checked by rendering the model against iPhone frames; IK and the distance prediction stay off. |
