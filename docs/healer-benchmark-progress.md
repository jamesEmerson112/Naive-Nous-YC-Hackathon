# Device healer: benchmark progress and how we pick the model

Updated 2026-11-07, 5:23 PM PST. The test bed code is local for now (`edge/`, not in the repo yet). It moves
into the repo as `device/` once the model tests finish.

## What we measure

When the robot app on the Pi crashes, hangs or breaks `SPEC.md`, a small local model running in an agent
harness (mini-swe-agent, through Ollama) diagnoses the incident. It patches the code, or parks the arm if
the fault is hardware, checks the fix by replaying the incident, and writes a report for the cloud. The
benchmark measures that loop end to end on a Mac, with fake hardware: a fake SO-101 and recorded HC-SR04
traces.

## The benchmark (frozen during the model tests)

| Task | Planted fault | Detected as | Right response |
|---|---|---|---|
| D1 | A "safety" rule opens the gripper when something gets close (the live demo fault) | tier 3 spec violation | Delete the rule |
| D2 | The distance validity check is removed | tier 1 crash | Restore the check |
| D3 | Servo reads skip `with_retries`, and the bus drops 3 reads | tier 1 crash | Restore the retries |
| S1 | Config key typo: `near_cn` | tier 1 crash (KeyError) | Fix `config.toml` |
| S2 | The sensor read waits forever for an echo | tier 2 hang | Remove the busy-wait |
| A5 | Gripper servo (ID 6) missing | tier 1 crash | Park the arm and report hardware; change no code |

A task passes on a hidden check that the healer never sees:
- **Software tasks:** the healed code is rerun on fresh fake hardware. Every arm command must match the
  original app's, on the incident replay and on two regression traces (normal, hand_close).
- **A5:** the arm is parked with torque off and the code is unchanged.
- **Every task:** the fake hardware is unedited, and the report names the right cause and action.

Sanity checks: the reference fix passes 6/6 and doing nothing passes 0/6. Wrong fixes we tried all fail,
including guarding only the None case on D2, catching the missing-servo error in code, and editing the
fake hardware.

## Harness versions

| Version | What changed | Why |
|---|---|---|
| h1 | Prompt plus skill guides listed by path; free-form `sed` edits; the model ends the run itself | First version |
| h2 | An exact-replace edit tool. A report gate: "fixed" is accepted only if the health check passes, "park" only after parking. SPEC.md and the matching guides go into the prompt | granite edited with broken `sed`, claimed fixes it never made, and never opened the guides |
| h3 | "Hardware" and "park" are accepted only when the incident shows a servo or bus error. The deployed code goes into the prompt. Edits can be SEARCH/REPLACE blocks in a heredoc | granite escaped through "it's hardware, park it", edited without reading the code, and broke shell quoting |

Each version is saved and can be rerun as is.

## Results so far

Mac M5 with Ollama, 16k context, limits of 30 steps and 15 minutes per task, each model at its default
sampling.

Screen on h3, one full run (6 tasks) per config:

| Config | Passed | Output tokens per heal | Median time per task | Largest prompt | Main failure |
|---|---|---|---|---|---|
| granite4.1:3b | 1/6 | 3.0k | 180 s | 16.0k | Edits that never land (4 tasks), context overflow (1) |
| qwen3.5:4b, thinking on | **6/6** | 2.0k | 83 s | 12.3k | none |
| qwen3.5:4b, thinking off | 5/6 | 2.0k (0.6k to 1.0k on its 5 passes) | 63 s | 16.4k | D1 looped until the conversation overflowed the 16k context |
| qwen3:4b-instruct-2507 | running | | | | |

Earlier runs, granite only: h1 0/2 (S1, D2); h2 1/6 in each of two runs, passing only A5.

These are single runs, and the models sample randomly, so the numbers are noisy until the confirmation
runs below.

## How we pick the model

1. **Candidates.** The strongest models under 5B for agent and tool use in 2026 roundups that are in Ollama
   and fit the Pi 5's 8 GB: qwen3.5:4b (3.3 GB, thinking on and off) and qwen3:4b-instruct-2507 (2.5 GB,
   no thinking), with granite4.1:3b (2.1 GB) as the baseline. gemma4:e4b was skipped because at 6.6 GB it
   leaves too little memory on the Pi. qwen3:4b's default tag is the thinking-only build, so the instruct
   build stands in for "qwen3 with thinking off". Vendor benchmark scores aren't independently verified,
   which is why we measure.
2. **Fix the harness first (h3).** On h2, every model would trip over the same two holes: blind edits, and
   parking as an escape. A model comparison there would mostly measure the harness.
3. **Same conditions for every config:** the frozen benchmark, the same limits, each model's default
   sampling, and one run at a time.
4. **Screen:** one full run per config. Stop there if every config passes 1/6 or less.
5. **Leader and runner-up.** The leader has the most passes; ties go to fewer output tokens, our proxy for
   speed on the Pi. The runner-up is the next config within one pass. If the leader thinks, the runner-up
   is the best non-thinking config, so a Pi-friendly option stays in play.
6. **Confirm:** 3 full runs (18 trials) each for the leader and the runner-up, plus 5 trials of D1 on the
   leader, since D1 is the live demo fault.
7. **Harness chart:** the leader on h1, h2 and h3 at 18 trials each, to show how much the harness (the part
   the cloud improves) changes the result.
8. **If there's time:** the leader on h3 without the skill guides, to measure what they add.
9. **Pi speed** can't be measured on a Mac. We report output tokens per heal, so Pi heal time = tokens ÷ the
   Pi's tokens per second, once that's measured on the Pi.

## Next

- Finish the screen (config d), pick the leader and runner-up, then run the confirmation and the harness
  chart.
- Move `edge/` into the repo as `device/` (build plan B1) and add a test suite that runs with one command.
- Not started: anything on the Pi itself (tokens per second, the real arm).
