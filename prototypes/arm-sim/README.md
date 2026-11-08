# arm-sim

Tonight's simulated SO-101 bench (P1 in James's plan note): the arm host, skills and checks run here first, then swap to the real follower arm at 7:30 AM.

**Setup, once:**
1. `./setup.sh` fetches MuJoCo Menagerie's SO-101 into `vendor/`.
2. `uv run --python 3.12 --with-requirements requirements.txt python -m armsim poses` writes the poses and the wave.

**Run:** `uv run --python 3.12 --with-requirements requirements.txt python -m armsim <command>`
- `demo` goes home, waves, and goes home again. It writes `runs/<id>.jsonl` (an `arm-run/v1` record) and `runs/<id>.gif`.
- `view` shows the same moves in MuJoCo's viewer. On macOS, run it with `mjpython` instead of `python`.

**Units and formats:**
- Arm joints are in degrees and the gripper is 0–100 (0 = closed), the same as LeRobot and Andre's primitive JSON.
- Distance rows use the pod's `D` fields (`mm`, `us`, `st`).

**Known limits:**
- The sim's zero pose isn't the real arm's calibrated zero.
- Pointing straight down, the fingertips can't rise above about 9 cm at 0.17–0.25 m out, because wrist flex tops out near 95°. That's why the block is 3 cm tall.
