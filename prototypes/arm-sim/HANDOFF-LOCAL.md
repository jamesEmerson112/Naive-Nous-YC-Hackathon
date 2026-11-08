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
   - `uv sync --all-packages` at the root, for the edge bridge.
3. **Pin both serial ports** by `/dev/serial/by-id`:
   - `ARM_PORT` in `arm/arm.env`;
   - the pod's port for `podd`.
   Only `podd` may open the pod's port (`firmware/healer/README.md:36`).
4. Start `podd` from `firmware/`: `healer/bin/podd &` (`firmware/healer/README.md:31`).
5. Teach the poses with Andre: `arm/bin/prim-record.sh --pose rest home above_block pick`.

**R1. The Mac:**
1. Romulus: `make up && make seed`.
2. Revert the Hermes test setting: `hermes config set compression.threshold_tokens null`.
3. Leave the manager profile (`hermes-manager/`) alone until the cloud session's fixes are on `main`.

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
| | | | |
