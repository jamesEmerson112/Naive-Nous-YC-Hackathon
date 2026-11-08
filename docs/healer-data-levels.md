# Healer data levels: rules, a time-series model, a learned model

_James's plan, Sun Nov 8 ~2:40 AM PST. The checklist lives in `hub/src/content/proposal.ts` (items L1, L2, L3a, L3b)._

The healer reads the same data at three levels. Each level only takes over what the one below it can't see.
**The rule doesn't change at any level:** no model sits in the real-time control loop. The watchdog's plain-code rules still park the arm; a model only adds a second opinion or an explanation *(Romulus rule, CLAUDE.md)*.

| Level | What reads the trace | Where it runs | Status |
|---|---|---|---|
| 1 | Plain-code rules: `prototypes/arm-sim/armsim/level1.py` (arm), Olir's `firmware/healer/detect.py` (pod) | The arm host, every tick | Done |
| 2 | A time-series model (ChatTS-14B) asked whether a trace looks wrong | One Runpod GPU, on demand | Ran Sun 3 AM: catches dropouts and spikes; misses a frozen trace alone, catches it next to a healthy one |
| 3 | A model trained on labeled traces from the fault factory plus bench recordings | CPU for a small classifier; Runpod only for a fine-tune | L3a classifier done (sim only, below); L3b, in the loop, not started |

## James's decisions (Sun ~3 AM)
- **Worker and manager:**
  - A small local language model (the worker) watches the logs all the time. When something goes wrong, it tells the manager.
  - The manager is the Hermes harness, run by Naïve: possibly several agents with different jobs.
  - The manager works out the fix and sends it back; the worker starts it.
  - The worker carries out only allowlisted, reversible actions, and plain code checks the plan first *(inferred: keeps the no-LLM-in-the-control-loop rule)*.
- **The manager's model:** ChatGPT 6 "sol" at default reasoning.
- **Two categories of bugs:**
  1. Everything the healer handles today: sensor, pod firmware, joints and software faults.
  2. **A wrong action:** the arm did the wrong thing (missed the grasp, wrong place, bumped something). The system reverses the action, then the LLM corrects the command and re-runs it.
- **Hardware for the worker:** whatever is available. Benchmark on CPU in the cloud container and on Runpod GPUs, and pick for a laptop-class machine.
- **Runpod budget:** up to $10 tonight, every pod capped and terminated.

## Level 1: rules (done)
- **Rules:** `us_frozen`, `us_stuck`, `us_scale`, `us_offset`, `us_noisy`, `us_missing_echo` and `joint_stall` (`prototypes/arm-sim/README.md`).
- **Score** (fault factory, 1,000 runs, `prototypes/arm-sim/MORNING.md`):
  - 0 of 142 healthy runs flagged.
  - 115 of 115 dead servos caught.
  - Other planted faults caught in 23–54% of runs.
- **The gap:** subtle faults (an offset, a scale error, a reversed joint) look healthy to fixed thresholds. That gap is what levels 2 and 3 are for.
- **Known hole on the pod:** `detect.py` has no frozen-ultrasonic rule, so a stuck distance fires nothing (team status in CLAUDE.md).

## Level 2: a time-series model (ran Sun 3 AM)
- **The test:** `prototypes/arm-sim/level2/chatts_test.py`. Olir's real 300-reading ultrasonic sample, plus planted copies: frozen, dropouts, spikes. Only the 1 µs jitter separates the frozen copy from the healthy one, and Olir's rules miss it.
- **Run:** `level2/runpod_l2.py launch | status | fetch | terminate` on one Runpod GPU, through the REST API. The job has a 2-hour `timeout`.
- **Result** (Sun Nov 8, ~3 AM PST; `prototypes/arm-sim/level2/results/`):
  - **Hardware and cost:** NVIDIA L40S 48 GB, community cloud, $0.79/h. The model loaded and answered in about 2 minutes. All three attempts together cost about $0.11, and every pod was terminated.

  | Trace | ChatTS-14B's answer | Right? |
  |---|---|---|
  | Healthy | "functioning normally. No faults detected." | ✓ |
  | Frozen (no jitter) | "The sensor appears functional … No anomalies detected." | ✗ (the fault level 1 also misses) |
  | Dropouts (24 zero readings) | "recurring sharp drops indicate consistent interference or sensor misalignment" | ✓ |
  | Spikes (6 large echoes) | "spikes could indicate transient obstructions or sensor noise" (it also claims a drift that isn't there) | ✓, but with a wrong detail |
  | **Pair:** healthy A vs frozen B | "Sensor B is faulty … shows zero noise, which is unrealistic for ultrasound" | ✓ |

- **What it means** *(inferred)*:
  - Shown one trace alone, ChatTS reads "flat" as "stable" and misses the frozen sensor.
  - Shown a healthy reference beside it, it catches it.
  - So the worker's prompt should always include a reference window: the same pose from a healthy run, or the sensor's own recent history.
  - The cheaper fix is a zero-variance check in plain code (level 1, `acc_frozen` ported to `us`).
  - A 14B model is also too big for the worker: it needs a 48 GB GPU.
- **Failed attempts, for the next person:**
  1. torch 2.4 in the image: transformers 4.52 refuses `torch.load` below torch 2.6 (CVE-2025-32434).
  2. torch 2.6 with the image's old torchvision: `torchvision::nms does not exist`.
  - **Fix:** install `torch==2.6.0 torchvision==0.21.0 torchaudio==2.6.0` from the cu124 index first.
  - **Python's user agent:** Runpod's Cloudflare blocks the default one with error 1010, so send a plain client name.

## Level 3: learned from data (L3a done, L3b next)
- **L3a, a small classifier on CPU** *(inferred design)*:
  1. Regenerate the fault factory (`python -m armsim.factory --n 2000 --workers 8`, about 0.1 s a run).
  2. Cut each run into fixed windows of the distance trace and the joint errors.
  3. Train gradient-boosted trees (scikit-learn) on simple features: jitter, slope versus the arm's motion, the residual against the kinematic prediction, arrival error.
  4. Hold out whole runs.
  - **Gate:** beat level 1 on the subtle faults with **0 false alarms** on healthy runs.
  - **Add real data:** Olir's overnight podd logs and the first real arm runs (`samples/`).
- **L3a result** (Sun ~3:20 AM PST; `prototypes/arm-sim/armsim/level3.py`, `python -m armsim.level3 train`, 16 s on CPU):
  - **Setup:**
    - 2,000 fault-factory runs (seed 1), split 60/20/20 into train, calibrate and test.
    - Gradient-boosted trees on 38 features from each run's distance rows (the residual against the kinematic prediction, jitter, the longest unchanged echo streak, no-echo while in range) and joint rows (tracking error, wrong direction, load).
    - It alarms when P(healthy) < 0.98, a threshold set on the calibration split.
  - **Fresh batch** (600 runs, seed 99, never seen in training):

    | Planted fault | Runs | Level 1 caught | Level 3 caught |
    |---|---|---|---|
    | none (false alarms) | 74 | 0 | **0** |
    | joint_offset | 66 | 21 | 66 |
    | joint_reversed | 78 | 23 | 78 |
    | joint_stuck | 65 | 65 | 65 |
    | us_frozen | 68 | 40 | 68 |
    | us_noisy | 77 | 14 | 77 |
    | us_offset | 78 | 30 | 78 |
    | us_scale | 94 | 56 | 94 |

  - **Leakage checks:**
    - Shuffled labels score 0.115, about chance (0.125).
    - Without the row-count features, real labels still score 0.99.
  - **Caveat:** this is in-distribution, simulator-only data, and the simulator's faults are cleaner than real ones. The first real runs (`samples/`) are the test that counts *(inferred)*.
- **L3b, in the loop** *(inferred design)*:
  - The classifier runs beside the watchdog as a second opinion. When it flags a run the rules passed, it opens an incident at `warn` severity, which the commander checks with `arm_check`. It never moves the arm.
  - **Optional:** a LoRA fine-tune of Olir's `qwen3.5:4b` on the labeled traces, so the healer can explain a finding in one sentence.

## Runpod budget: $15.67 on the account
**GPU only where a CPU can't do the job:**

| Step | GPU | Hours (capped) | Cost |
|---|---|---|---|
| L2: ChatTS-14B test | RTX A6000 48 GB | ≤ 2 | ≤ $1.20 (community $0.33/h, secure $0.59/h) |
| L3b: optional LoRA of a 4B model | RTX A6000 48 GB | ≤ 2 | ≤ $1.20 |
| Worker-model benchmark (decided Sun 3 AM) | 48 GB cards at ≤ $0.79/h | capped | ≤ $10 in total tonight |

- **Prices:** the Stack Map's Runpod table (`hub/src/content/proposal.ts`, `PODS`).
- **No Isaac Sim on this budget:** MuJoCo already covers the sim.
- **Every pod:**
  - a hard time cap, then it stops itself;
  - terminate it after its results are fetched, so an idle disk doesn't keep billing;
  - James is told the GPU, hours and cost before it starts.
