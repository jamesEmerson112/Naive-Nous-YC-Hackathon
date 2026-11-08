# Device healer: benchmark progress and how we picked the model

Updated 2026-11-07, 6:17 PM PST. Model testing is done; we stopped before the end of the confirmation plan
(see step 6 below). The code is local for now, not in the repo yet, in three folders: `device/` (the robot
app, fake hardware and supervisor; the code package), `harness/` (the h1, h2 and h3 bundles) and `bench/`
(the benchmark).

## The pick

**qwen3.5:4b with thinking on, on harness h3.** It passed 10 of 12 trials, more than any other config, and
it's the only config that passed D1, the live demo fault (2/2). It uses about as many output tokens per heal
as with thinking off (2.2k against 2.0k), because without thinking it loops on the hard tasks. At 3.3 GB it
fits the Pi 5's 8 GB.

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

Sanity checks: the reference fix passes 6/6 and doing nothing passes 0/6 (rechecked after the move into
`device/`, `harness/` and `bench/`). Wrong fixes we tried all fail, including guarding only the None case
on D2, catching the missing-servo error in code, and editing the fake hardware.

## Harness versions

| Version | What changed | Why |
|---|---|---|
| h1 | Prompt plus skill guides listed by path; free-form `sed` edits; the model ends the run itself | First version |
| h2 | An exact-replace edit tool. A report gate: "fixed" is accepted only if the health check passes, "park" only after parking. SPEC.md and the matching guides go into the prompt | granite edited with broken `sed`, claimed fixes it never made, and never opened the guides |
| h3 | "Hardware" and "park" are accepted only when the incident shows a servo or bus error. The deployed code goes into the prompt. Edits can be SEARCH/REPLACE blocks in a heredoc | granite escaped through "it's hardware, park it", edited without reading the code, and broke shell quoting |

Each version is saved in `harness/` and can be rerun as is.

## Results

All on h3: Mac M5 with Ollama, 16k context, limits of 30 steps and 15 minutes per task, each model at its
default sampling.

| Config | Trials | Passed | D1 | D2 | D3 | S1 | S2 | A5 | Median time per task | Output tokens per heal |
|---|---|---|---|---|---|---|---|---|---|---|
| qwen3.5:4b, thinking on | 12 (2 runs) | **10/12** | 2/2 | 1/2 | 1/2 | 2/2 | 2/2 | 2/2 | 105 s | 2.2k |
| qwen3.5:4b, thinking off | 10 (1 run, plus 4 tasks of a stopped run) | 7/10 | 0/2 | 1/2 | 2/2 | 2/2 | 1/1 | 1/1 | 63 s (full run) | 2.0k (full run) |
| qwen3:4b-instruct-2507 | 6 | 3/6 | 0/1 | 0/1 | 0/1 | 1/1 | 1/1 | 1/1 | 104 s | 2.6k |
| granite4.1:3b | 6 | 1/6 | 0/1 | 0/1 | 0/1 | 0/1 | 0/1 | 1/1 | 180 s | 3.0k |

With thinking off, no thinking text came back. On the tasks it solved, it used 0.6k to 1.0k output tokens,
against 0.8k to 4.6k with thinking on.

How each config failed:
- **qwen3.5:4b, thinking on:** on D2, a wrong fix that passed the health check: the new code never updates
  the last valid distance, so the arm never lifts. The health check doesn't test lifting. On D3, the
  conversation overflowed the 16k context (a 16.3k prompt) and it hit the step limit.
- **qwen3.5:4b, thinking off:** malformed edit blocks and tool calls that loop, on D1 and D2 (one run
  overflowed the context at 16.4k), and the step limit on D1. It never passed D1.
- **qwen3:4b-instruct-2507:** gave up on D1 to D3, and wrote malformed edit blocks, then claimed the edit
  had worked though nothing changed.
- **granite4.1:3b:** edits that never land (4 tasks hit the 30-step limit), and one context overflow.

Earlier runs, granite only: h1 0/2 (S1, D2); h2 1/6 in each of two runs, passing only A5.

## How we picked the model

1. **Candidates.** The strongest models under 5B for agent and tool use in 2026 roundups that are in Ollama
   and fit the Pi 5's 8 GB: qwen3.5:4b (3.3 GB, thinking on and off) and qwen3:4b-instruct-2507 (2.5 GB,
   no thinking), with granite4.1:3b (2.1 GB) as the baseline. gemma4:e4b was skipped because at 6.6 GB it
   leaves too little memory on the Pi. qwen3:4b's default tag is the thinking-only build, so the instruct
   build stands in for "qwen3 with thinking off". Vendor benchmark scores aren't independently verified,
   which is why we measured.
2. **Fix the harness first (h3).** On h2, every model would trip over the same two holes: blind edits, and
   parking as an escape. A model comparison there would mostly measure the harness.
3. **Same conditions for every config:** the frozen benchmark, the same limits, each model's default
   sampling, and one run at a time.
4. **Screen:** one full run per config. qwen3.5:4b scored 6/6 with thinking on and 5/6 with it off,
   qwen3:4b-instruct-2507 3/6, granite4.1:3b 1/6.
5. **Leader and runner-up.** The leader has the most passes; ties go to fewer output tokens, our proxy for
   speed on the Pi. The runner-up is the next config within one pass, and if the leader thinks, the best
   non-thinking config, so a Pi-friendly option stays in play. That made qwen3.5:4b with thinking on the
   leader and with thinking off the runner-up.
6. **Confirm.** The plan was 3 full runs each for the leader and the runner-up, plus 5 trials of D1 on the
   leader. We stopped after 2 runs of the leader (2 trials of D1) and 1 run plus 4 tasks of the runner-up,
   to move on to building. By then the leader was ahead overall (10/12 against 7/10) and on D1 (2/2 against
   0/2).
7. **Harness chart** (the leader on h1, h2 and h3): skipped.
8. **The leader without the skill guides:** skipped.
9. **Pi speed** can't be measured on a Mac. We report output tokens per heal, so Pi heal time ≈ tokens ÷
   the Pi's tokens per second, plus the time to process prompts of 5k to 16k tokens.

## Next

- On the Pi: measure qwen3.5:4b's tokens per second and prompt processing time, which give the heal time.
- Harness h4, from the failures above: the health check tests lifting when something is near; the edit
  tool accepts a short end marker and says plainly when nothing changed; a context budget for long runs.
- Land `device/`, `harness/` and `bench/` in the repo (build plan B1), with a test suite in
  `device/tests/` that runs with one command.
- Not started: the real arm.
