# Morning brief (James): the prototype, everything together

_Final version, Sun Nov 8, about 1:55 AM PST._
- **Branch** `james/arm-sim`, pushed. **Draft PR [#55](https://github.com/jamesEmerson112/Naive-Nous-YC-Hackathon/pull/55)**: it's the demo branch, and Kent's Romulus work merges into it this morning.
- **Stack Map:** https://claude.ai/artifact/7TriXR2KDUm9GxLg3o3aZ2

## What works (simulation, on your Mac)
**One command per story**, after `prototypes/arm-sim/demo/up.sh`. Run them with `PYTHONPATH=. .venv/bin/python -m armsim.stories …` from `prototypes/arm-sim`:

| Story | Result |
|---|---|
| `a` (bad pod image 07, the bench's fault) | the approach sees "reads 12 mm where the pose says 110 mm" and parks; Olir's `podd` flags `us_mismatch`; rollback (a stand-in in sim) and the check at home take 20 s; the arm resumes and picks |
| `b` (stuck sensor mid-job) | "Distance stuck at 130 mm while the arm moved 29 mm", then a restart through `podd`, then the pick. 33 s |
| `b --hermes` | a Naïve card drives Hermes. Done once tonight on card `crd_m27qc…`: Hermes healed the sensor, finished the job, commented `RESULT:`, and the card went to done |
| `stall` | a dead elbow servo fails the checked retry and is handed to a person; Romulus opens a `needs_human` work order |

**Also working:**
- **Romulus on your Mac** (`localhost:8000`) gets `arm-twin-1`'s incidents and `pod-1`'s (through Kent's edge bridge), with the right outcomes and recordings.
- **Backup-video clips** are in `prototypes/arm-sim/runs/media/` (`story-a/b/stall.mp4`, about 0.2–0.3 MB each). Story A there is the image-02 version.
- **The real-arm adapter** (`armsim/real.py`, on Andre's `prims.Arm`) passes its 5 mock tests.

## Overnight on main: Olir's #54 passed on the real bench (12:05 AM)
1. Bad pod release v1.7 (image 07) makes the arm flail.
2. Olir's pod healer (qwen3.5:4b on his laptop) runs a verified `fw_rollback`.
3. It re-runs the arm job: 663 of 663 frames.
4. It names the root cause.

It's the real-hardware version of our story A, built separately: his healer drives the arm with `arm/heal_test.py` and its `arm_run` tool, while ours runs jobs from Naïve cards through Hermes and reports to Romulus. **Main is merged into our branch** (only the `arm/` README and ignore file conflicted, and both sides are kept).

## Decisions for you
1. **Who drives the arm in the demo?** Only one program can own the servo port.
   - **My proposal:** our arm host runs the job (Naïve card → Hermes) and reports to Romulus. Olir's pod healer heals the pod (image 07 → `fw_rollback`). Then our arm host's check passes and Hermes retries.
   - That needs Olir's healer to run without its own `arm_run`, or with `arm_run` calling our host.
2. **One combined demo story on the real bench?** The real pod has no "frozen sensor" fault to plant: its images are 01–07, plus `inject` for crashes and the accelerometer. So I'd merge the stories: Naïve card → Hermes starts the job → image 07 goes out mid-job → arm parks → pod healer rolls back → Hermes retries → `RESULT:` on the card.
3. **Front-end style** (futuristic, glassmorphism, cyberpunk fonts): which screens? Romulus's pages, the Stack Map, or a new demo dashboard?
4. **Live work orders:** for the hardware story, should Romulus post to the real Naïve board (`NAIVE_MODE=live`, plus `WORK_ORDER_PHONE`: whose phone does it call or text)? Or keep fake mode?
5. **Hermes test config:** compaction is still at the 12K test threshold (warnings on every job run). Can I revert it?
6. **Rotate the Naïve key** that was pasted in chat on Saturday. Update both `naive login` and `~/.hermes/.env`.
7. **PR #55:** mark it ready after Kent's Romulus work merges into our branch?
8. **Optional, ask Kent:** mint the board-only session, or raise your role. Tonight Hermes uses your key behind a board-only filter.

## Morning on the hardware (7–10 AM)
**Where things run:**
- **Your Mac:** Romulus (Docker), Hermes, the Naïve board.
- **The Pi:** podd (Olir), the arm host, the edge bridge.

1. **R0, the Pi** (with Andre and Olir):
   1. SSH in; `git fetch && git checkout james/arm-sim`.
   2. Python 3.12 venv with `lerobot[feetech]==0.7.0`, plus `prototypes/arm-sim/requirements.txt` and the two `-e` installs in its README.
   3. Pin both ports by `/dev/serial/by-id`: `ARM_PORT` in `arm/arm.env`, `POD_PORT` for podd. Start podd.
   4. Teach the poses with Andre: `arm/bin/prim-record.sh --pose rest home above_block pick`.
2. **R2, start on the Pi:**
   ```
   ARMSIM_ROMULUS=http://<mac-ip>:8000 prototypes/arm-sim/demo/up.sh --real
   ```
   - On the Mac, forward the arm host's socket for Hermes and the stories:
     ```
     ssh -N -L "$TMPDIR/armsim.sock:/tmp/armsim.sock" pi@<pi>
     ```
   - Then `python -m armsim.stories a --real` (Olir starts image 07), and `b --hermes --real`.
3. **Known gap:** until joint offsets are measured into `armsim/real_calibration.json`, the real arm moves in joint space and only these checks run:
   - frozen sensor;
   - joint stall;
   - podd's incidents.

   The image-07 story needs only podd's `us_mismatch`, so it doesn't depend on the offsets.
4. **R3:** first real runs into `samples/`, then the overnight podd slice from Olir's Pi. 10 AM is the freeze.
