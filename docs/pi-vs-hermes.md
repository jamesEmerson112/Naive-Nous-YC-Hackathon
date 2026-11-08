# pi vs Hermes: first results

- **Why:** to choose an agent loop (harness) for the robot arm's operator agent. Naïve's built-in `pi` runs in Naïve's cloud. Hermes runs on our own machine and reaches Naïve over its MCP server.
- **When and by whom:** Sat Nov 7, 2026, 5–6 PM PST, on James's Mac, using the console in `prototypes/naive-console/` (`node prototypes/naive-console/server.mjs`, then open http://127.0.0.1:5180).
- **Marking:** numbers are measured. *(inferred)* marks our reading.

## Setup

| | pi (Naïve) | Hermes |
|---|---|---|
| Where it runs | Naïve's cloud | this Mac (Hermes v0.23.0) |
| Model | `openai/gpt-6-luna` or `openai/gpt-6-sol` (agents `hello-luna`, `hello-sol`) | the same model, through the ChatGPT plan (`openai-codex`) |
| Instructions | "You are a helpful assistant." | Hermes's own system prompt plus its injected memory |
| Tools | Naïve's built-ins | Naïve's 368 tools (MCP), `memory`, `session_search` |
| Reasoning effort | model default (Naïve agents can't change it) | default (medium) |
| Paid by | Naïve credits | ChatGPT plan |

- **Same brain:** the console lists only OpenAI-model agents, so both sides always run the same model.
- **Conversations:** a conversation keeps one pi session and one Hermes session until "New conversation".

## 1. One message, fresh conversation

| Test | pi wall time | Hermes wall time | pi cost |
|---|---|---|---|
| "hello world", luna | 8.6 s (4 s model) | 2.4 s (1.6 s model) | $0.0008 |
| "hello world", sol | 8.6 s (5 s model) | 2.0 s (1.3 s model) | $0.043 |

- **Where pi's time goes:** about 2 s of Naïve's own steps before the model call:
  - `tools_resolve` ~0.5 s
  - `model_gate` ~0.8 s
  - transcript and harness load
- **Hermes's time:** mostly the model.

## 2. Memory within a conversation (sol, both robot arm docs)

"Context" is the prompt each side sent on its last model call that turn, which is the whole memory it carries.

| Turn | Message | pi context | pi wall | pi cost | Hermes context | Hermes wall |
|---|---|---|---|---|---|---|
| 1 | SO-101 research notes (11K chars) | 18,903 | 19.6 s | $0.054 | 10,067 | 5.6 s |
| 2 | Fault catalog (25K chars) | 26,278 | 23.3 s | $0.024 | 17,465 | 5.0 s |
| 3 | Recall quiz (Python version, fault D1, e-stop) | 26,501 | 6.3 s | $0.007 | 17,687 | 5.7 s |
| 7 | "Which two documents?" | 27,281 | 10.9 s | $0.006 | 18,230 | 5.0 s |

- **Facts from the documents:** both answered the quiz correctly. Python ≥ 3.12 and `uv sync --locked --extra feetech`, and D1 is the drop-on-proximity rule, verified with a "hand at 3 cm for 2 s" trace.
- **Base prompt:** pi carries about 15.6K tokens of Naïve instructions before any history; Hermes carries about 7K.
- **Cost:** pi's conversation cost about $0.10 for 7 turns; later turns are cheap because Naïve caches the prompt.

## 3. Memory across conversations

| | Taught "SO-101; e-stop is the red button on the left" in one conversation, then asked in a new one |
|---|---|
| Hermes | **Remembered.** It wrote the fact to `~/.hermes/memories/MEMORY.md` with its `memory` tool and injects that file into every new conversation |
| pi | **Didn't remember:** "I only know that you mentioned your robot arm in this message." Naïve documents no notes kept between sessions |

## 4. Compaction (summarizing old history)

| | What we saw |
|---|---|
| pi | No sign of compaction by 27K tokens. Naïve's docs say it summarizes older turns past a token threshold and clears stale tool results, but the threshold and the summary aren't published; a sudden drop in context per turn is the only sign *(inferred)* |
| Hermes | For this test we set the trigger to 12K tokens (`compression.threshold_tokens`; the default is 50% of the window). It fires ("18,253 request tokens >= 12,000 threshold") but **fails to summarize**. Its helper model can't load the ChatGPT login the main chat uses ("Provider 'openai-codex' … no credentials were found", even right after `hermes auth refresh`). It fails safe: "made no progress — skipping", and the full history is kept |

- **Hermes's view of the window:** it treats gpt-6-sol on the ChatGPT route as a **272K** window, not 1M.
- **Fix to try:** give the summarizer its own login (e.g. Nous Portal: `hermes auth add nous`, then `auxiliary.compression.provider: nous`), or turn on debug logging and report it upstream.

## 5. What we can observe

| | Hermes | pi |
|---|---|---|
| Long-term memory | readable files (`USER.md`, `MEMORY.md`) | none documented |
| Exact prompt sent | yes: `hermes sessions export` gives the system prompt and every message, with `compacted` flags | no |
| Compaction | yes: log lines with token counts, plus the summary in the transcript once it works | only inferred from token drops |
| Per-call tokens | totals per turn, plus the latest prompt size in the export | yes, per model call (`span.model_request.end`) |

## 6. Reliability notes

- **Naïve capacity:** Naïve answered `rate_limited: the service is at capacity` several times during these runs. The console now retries after 2, 4, 8 and then 15 s, and reports the waiting time apart from wall time.
- **It hits Hermes too:** Hermes's background service reconnects to Naïve's MCP when it starts. If Naïve is at capacity (HTTP 429), the first Hermes run can fail with "Gateway starting: deadline". Start the service ahead of time with `hermes gateway ensure`.
- **Accounts:** ChatGPT accounts reject some model names (`gpt-5.4-mini`, `gpt-5.5`); `gpt-6-luna` and `gpt-6-sol` work.

## 7. Can it fix the fault?

- **Setup:** the six faults of Ishaan's healer benchmark (`docs/healer-benchmark-progress.md`), planted in a small stand-in robot app (`prototypes/fault-lab/`), on gpt-6-sol, run twice each. Each agent works with its own tools:
  - **pi:** in its Naïve sandbox.
  - **Hermes:** shell and file tools in a throwaway folder, with at most 30 steps.
- **Hidden check:** reruns each fix on fresh fake hardware, including a trace with glitches. For A5 the arm must be parked and the code unchanged.
- **Where to see it:** in the console, under "Can it fix the fault?". Raw results are in `prototypes/fault-lab/results/`.

| Task | Fault | pi fixed? | pi time | pi steps | Hermes fixed? | Hermes time | Hermes steps |
|---|---|---|---|---|---|---|---|
| D1 | A "safety" rule opens the gripper when something gets close (demo fault) | ✅ 2/2 | 67 s | 4.5 | ✅ 2/2 | 40 s | 14 |
| D2 | The distance validity check is removed | ✅ 2/2 | 50 s | 4 | ✅ 2/2 | 42 s | 14 |
| D3 | Servo reads skip `with_retries`; the bus drops 3 reads | ✅ 2/2 | 36 s | 4 | ✅ 2/2 | 42 s | 14.5 |
| S1 | Config key typo `near_cn` | ✅ 2/2 | 48 s | 3 | ✅ 2/2 | 31 s | 11.5 |
| S2 | The sensor read waits forever for an echo | ✅ 2/2 | 49 s | 4 | ✅ 2/2 | 41 s | 14 |
| A5 | Gripper servo (ID 6) missing: park, change no code | ✅ 2/2 | 31 s | 2 | ✅ 2/2 | 25 s | 10.5 |
| **All** | | **12/12** | | | **12/12** | | |

Times and steps are medians of the two runs.

- **Fixing:** both fixed everything.
  - The D2 trap that fooled Ishaan's small models (guarding only `None`) didn't catch either one.
  - On A5 both correctly parked instead of editing code.
- **Speed:** Hermes was faster on 5 of 6 faults; D3 is the exception.
- **Way of working:** pi uses a few big shell commands; Hermes many small file and shell steps.
- **Cost:** pi spent $1.37 of Naïve credits (about $0.11 per fix); Hermes ran on the ChatGPT plan.
- **Compared with Ishaan's small local models:** his best, qwen3.5:4b, passed 10/12. Our stand-in app is small, so these tasks are probably easier than his real benchmark *(inferred)*. Rerun pi and Hermes on his code once `device/`, `harness/` and `bench/` are in the repo.

## What this means for the robot arm agent *(inferred)*

- **Hermes fits the arm's operator agent.** It's 3–4× faster per step, runs next to the arm, keeps long-term notes we can read and edit, and its prompt and memory are fully inspectable.
- **The cost is more moving parts:** a background service, logins, MCP startup, and compaction setup.
- **pi fits office agents in Naïve:** zero setup, metered, durable event log, approvals and streaming. It's a black box for memory, and it can only drive the arm if the arm's API is exposed to the internet (undecided).
- **Next:** get Hermes compaction working, then run a long robot-ops conversation (jobs, incidents, fixes) on both and compare what each still remembers after compaction.
