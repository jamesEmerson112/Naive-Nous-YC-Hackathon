# Healer data levels: rules, a time-series model, a learned model

_James's plan, Sun Nov 8 ~2:40 AM PST. The checklist lives in `hub/src/content/proposal.ts` (items L1, L2, L3a, L3b)._

The healer reads the same data at three levels. Each level only takes over what the one below it can't see.
**The rule doesn't change at any level:** no model sits in the real-time control loop. The watchdog's plain-code rules still park the arm; a model only adds a second opinion or an explanation *(Romulus rule, CLAUDE.md)*.

| Level | What reads the trace | Where it runs | Status |
|---|---|---|---|
| 1 | Plain-code rules: `prototypes/arm-sim/armsim/level1.py` (arm), Olir's `firmware/healer/detect.py` (pod) | The arm host, every tick | Done |
| 2 | A time-series model (ChatTS-14B) asked whether a trace looks wrong | One Runpod GPU, on demand | Script written, never run |
| 3 | A model trained on labeled traces from the fault factory plus bench recordings | CPU for a small classifier; Runpod only for a fine-tune | Data generator done; no model yet |

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

## Level 2: a time-series model (next)
- **The test:** `prototypes/arm-sim/level2/chatts_test.py`. Olir's real 300-reading ultrasonic sample, plus planted copies: frozen, dropouts, spikes. Only the 1 µs jitter separates the frozen copy from the healthy one, and Olir's rules miss it.
- **Run:** `level2/run_on_pod.sh` on one GPU pod. It has a 2-hour `timeout`, then stops the pod itself.
- **Done when:** the four verdicts are recorded in this doc, saying whether ChatTS tells frozen from healthy.

## Level 3: learned from data (next)
- **L3a, a small classifier on CPU** *(inferred design)*:
  1. Regenerate the fault factory (`python -m armsim.factory --n 2000 --workers 8`, about 0.1 s a run).
  2. Cut each run into fixed windows of the distance trace and the joint errors.
  3. Train gradient-boosted trees (scikit-learn) on simple features: jitter, slope versus the arm's motion, the residual against the kinematic prediction, arrival error.
  4. Hold out whole runs.
  - **Gate:** beat level 1 on the subtle faults with **0 false alarms** on healthy runs.
  - **Add real data:** Olir's overnight podd logs and the first real arm runs (`samples/`).
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
