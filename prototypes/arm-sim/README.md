# arm-sim

Tonight's simulated SO-101 bench (P1 in James's plan note): the arm host, skills and checks run here first, then swap to the real follower arm at 7:30 AM.

**Setup, once:**
1. `./setup.sh` fetches MuJoCo Menagerie's SO-101 into `vendor/`.
2. `uv run --python 3.12 --with-requirements requirements.txt python -m armsim poses` writes the poses and the wave.

**Run:** `uv run --python 3.12 --with-requirements requirements.txt python -m armsim <command>`
- `demo` goes home, waves, and goes home again. It writes `runs/<id>.jsonl` (an `arm-run/v1` record) and `runs/<id>.gif`.
- `view` shows the same moves in MuJoCo's viewer. On macOS, run it with `mjpython` instead of `python`.

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
