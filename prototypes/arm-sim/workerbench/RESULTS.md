# Worker-model benchmark: dataset, baselines, how to run a model

_Built Sun Nov 8, ~4 AM PST, as step 10 of `docs/worker-manager-healer.md` §5, to the spec in §4 of that doc and in `docs/research/worker-benchmark-data.md`. Models were scored on one Runpod GPU from ~4:40 AM PST (next section); the tables after it are the baselines. *(inferred)* marks my own conclusions._

## Model results: one Runpod GPU (Sun Nov 8, from 4:40 AM PST)
_All runs are done and both pods are terminated (`GET /pods` returned `[]`). Cost: **2.69 pod-hours at $0.79/h, about $2.13** (pod 1: 1.53 h, $1.21; pod 2: 1.17 h, $0.92), under the $2.40 budget (3 h at $0.79/h). The two pods ran one after the other, never at the same time: a bug stopped pod 1 early (see "How the run went")._

### Recommendation for the worker model *(inferred from the numbers below)*
- **Use `lfm3:1.7b` with thinking off as the laptop worker, but only in the role the design gives it.** Against the qwen3.5:4b baseline on `--quick`, it wins on false alarms (11/60 against 25/60, p = 0.009), real-data alarms (0/5 against 4/5) and messages, and ties on detection (gap recall 29/64 against 32/64, p = 0.72). Its role is writing the escalation after level 1 fires (design §1, track E). Keep qwen3.5:4b as the fallback, since it is the model proven on the bench (`firmware/harness/HANDOFF.md:11-13`).
  - On the full test (1,433 items) it had half the false alarms of qwen3.5:9b: 79/447 healthy, of which 50 are the motion group (below), and 29/397 without it. It raised 0 of 5 real-data alarms and had the best message score, 3.97.
  - It is the smallest (1.1 GB) and the fastest per call: p50 2.1 s against 3.6 s for qwen3.5:9b on the same GPU, one call at a time. On a laptop the gap should be wider, since speed scales with active parameters, 1.7B against 9B.
  - **Its weak points:** gap recall 0.44 against qwen3.5:9b's 0.62, schema_ok 0.88, and a kind score of 0.38. Watch the license too: commercial use only under $10M a year (design §3).
- **`qwen3.5:9b` detects more and alarms more.**
  - On the same 1,148 items it had higher gap recall, 165/277 against lfm3's 132/277 (Fisher p = 0.006).
  - It also had twice the false alarms, 122/366 against 62/366 (p = 4e-7).
  - Its kind score is better, 0.46 against 0.38. It is 6.6 GB.
- **`qwen3.5:4b`, the current baseline, alarmed on 25 of 60 healthy items and 4 of 5 real windows on `--quick`.** It is not better than either finalist; it did not run on the full test.
- **No model is fit to open incidents by itself.** Every model fails the validity and false-alarm gates: schema_ok 0.88–0.92 against the gate's 0.98, and false-alarm upper bounds of 0.21 and 0.38 against 0.01. Plain code must keep the final say (design §1). The model's answer should only add the kind, the evidence and the message to an incident that level 1 has already opened.
- **The next fixes are in the prompt and the parser, not the model.** Each needs a rerun on the same items to confirm:
  1. **Every model trusts podd's false `us_intermittent` `INC` line.** It alarmed on 50 of 50 motion windows (lfm3) and 41 of 41 (qwen3.5:9b). Fix podd's rule, or tell the prompt that level-1 lines can be wrong more forcefully, then rerun the motion group on track D.
  2. **No model corrected level 1's wrong kind:** 0 of 89 (lfm3) and 0 of 71 (qwen3.5:9b) on the full test.
  3. **Most invalid answers are evidence-format errors:** an id with text attached, a comma list in one string, or a range past the last line. A `pattern` on the evidence items in `ANSWER_SCHEMA`, or a tolerant id parser, would turn most of them valid. Whether Ollama's grammar honours `pattern` is not checked.
- **Latency on a laptop is still owed.** What carries over from the GPU numbers is the size of each call: about 2,600–3,000 prompt tokens and 80–105 output tokens with thinking off, against 2,500 output tokens with thinking on. The real number is `run.py --endpoint http://192.168.50.1:11434/v1 --model lfm3:1.7b` from the Pi (design §3).

**Setup** (`workerbench/runpod_wb.py`, log in `runs/wb/runpod/plan.log`):
- **Pods:** two, one after the other, on the same NVIDIA L40S 48 GB host (`kldbzozpc4vi`, community cloud, Taiwan), $0.79/h (launch response). Each lived under 3 h. Image `ollama/ollama:latest`, which reported Ollama **0.40.15** (`/api/version`). Env `OLLAMA_CONTEXT_LENGTH=16384`, `OLLAMA_NUM_PARALLEL=4`.
- **Tags:** all five were checked on `ollama.com/library/<name>/tags` before the pull (`verify-tags`). Default tags (Q4): `qwen3.5:4b` 3.3 GB, `lfm3:1.7b` 1.1 GB, `qwen3.5:2b` 2.7 GB, `qwen3.5:9b` 6.6 GB, `lfm2.5:24b` 15 GB. `d1:2b` was skipped: it answers with probabilities on its own endpoint, not chat, so `run.py` can't score it.
- **What ran:** `run.py` here, against `https://<pod-id>-11434.proxy.runpod.net/v1`. Track E, profile lite, `--json-mode schema`, `max_tokens` 512, thinking off with `reasoning_effort: "none"`. Every model's smoke call came back with empty `reasoning` and `size_vram` equal to `size` (on the GPU), except lfm2.5:24b (below).
- **Items:** the 236 `--quick` items, in a fixed shuffled order (`runs/wb/runpod/quick-test-shuffled.jsonl`), so a run cut at its time cap is still a random sample. Same items as `run.py --quick`.
- **Latency:** one call at a time. It is an L40S plus the Cloudflare round trip from this container to Taiwan (0.36–1.3 s for `GET /api/version`, measured), **not** a laptop number. qwen3.5:4b's first ~75 items ran while the other models were still downloading on the pod; its p50 was 3.6 s there and 3.1 s after.

### Phase 1: quick subset (236 items: 64 gap items, 60 healthy, 5 real), thinking off, ranked
Ranked by **balanced = gap macro recall − healthy false-alarm rate** *(inferred; my change)*. The design ranks by gap recall first (§4), but that assumes the gates have already been passed. On `--quick` none can be (60 healthy items), and gap recall alone puts the always-alarm baseline at 1.0. Always-alarm and always-healthy both score 0 on balanced.

| # | model | schema_ok | gap recall macro (micro) | healthy false alarms (95% upper) | real-data alarms | balanced | kind score | message (0–5) | latency p50 / p95 (s) | prompt / output tokens |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | `qwen3.5:9b` | 0.907 | 0.465 (0.438) | 13/60 (0.322) | 2/5 | 0.249 | **0.511** | 3.86 | 3.61 / 4.99 | 2975 / 80 |
| 2 | `lfm3:1.7b` | 0.881 | 0.426 (0.453) | **11/60 (0.285)** | **0/5** | 0.243 | 0.429 | **3.98** | **2.10 / 4.61** | 2651 / 106 |
| 3 | `qwen3.5:2b` | 0.475 | 0.215 (0.172) | 1/60 (0.077), see below | 0/5 | 0.199 | 0.108 | 3.33 | 2.31 / 4.20 | 2975 / 71 |
| 4 | `qwen3.5:4b` (baseline) | **0.920** | **0.579 (0.500)** | 25/60 (0.531) | 4/5 | 0.162 | 0.418 | 3.74 | 3.20 / 5.27 | 2975 / 83 |
| – | `lfm2.5:24b` | 0.0 | 0.0 | 0/28 | 0/4 | – | – | – | 6.60 / 7.52 | 2619 / 512 |
| ref | `l1` baseline (quick) | 1.0 | 0.0 | 6/60 | 0/5 | – | – | – | – | – |

**Gates** (design §4): no model passes any of them on this subset. schema_ok is below 98% for all. The false-alarm bound can't go under 0.049 with 60 items, and the observed rates are 18–42% for the three usable models. qwen3.5:4b and qwen3.5:9b alarm on real data. Only p95 ≤ 30 s passes, but on a GPU, which says little about a laptop.

**More columns** (same runs):

| model | recall macro (all visible) | level 1 / level 1 + model recall | kind exact / route right, given an alarm | kind corrections | severity / escalate accuracy | evidence hit / precision | false alarms by healthy group |
|---|---|---|---|---|---|---|---|
| qwen3.5:9b | 0.740 | 0.636 / 0.796 | 0.68 / 0.67 | 0/16 | 0.62 / 0.76 | 0.71 / 0.53 | motion 5/6, holding 4/6, real 2/5, clean 1/7, sparse_no_echo 1/6, others 0 |
| lfm3:1.7b | 0.676 | 0.636 / 0.801 | 0.57 / 0.66 | 0/16 | 0.66 / 0.79 | 0.61 / 0.43 | motion 6/6, clean 2/7, ts_batch 2/6, sparse_no_echo 1/6, others 0 |
| qwen3.5:2b | 0.242 | 0.636 / 0.699 | 0.43 / 0.50 | 0/16 | 0.57 / 0.69 | 0.48 / 0.36 | motion 1/6, others 0 |
| qwen3.5:4b | 0.670 | 0.636 / 0.818 | 0.58 / 0.60 | 0/16 | 0.60 / 0.59 | 0.72 / 0.52 | motion 6/6, holding 5/6, real 4/5, clean 4/7, sparse_no_echo 2/6, 1/6 each in 08_dormant, covered, home and reflash |

**Gap recall by kind** (gap items only; n per kind is 4–16, so each cell is a handful of windows):

| model | clock_skew | joint_offset | joint_stuck | us_frozen | us_mismatch | us_noisy | us_offset | us_out_of_range | us_scale |
|---|---|---|---|---|---|---|---|---|---|
| gap n | 8 | 4 | 4 | 16 | 4 | 12 | 4 | 8 | 4 |
| qwen3.5:9b | 0.13 | 0.50 | 0.75 | 0.44 | 0.50 | 0.50 | 0.50 | 0.38 | 0.50 |
| lfm3:1.7b | 0.00 | 0.50 | 0.50 | 0.75 | 0.00 | 0.08 | 0.25 | 1.00 | 0.75 |
| qwen3.5:2b | 0.00 | 0.25 | 0.00 | 0.19 | 0.00 | 0.00 | 0.50 | 0.25 | 0.75 |
| qwen3.5:4b | 0.88 | 0.75 | 0.50 | 0.25 | 0.50 | 0.58 | 1.00 | 0.00 | 0.75 |

**What phase 1 shows** *(inferred unless a number is quoted)*:
1. **Every model believes the false `INC` line.** podd's `us_intermittent` fires on each wave end (the "motion" group; see "What the baselines show" below). Four of the five models alarm on 5 or 6 of those 6 windows; qwen3.5:2b alarms on 1. Track E hands the model level 1's mistakes, and the models repeat them.
2. **No model corrects level 1's kind.** Kind corrections are 0 of 16 for every model, so the joint_offset and joint_reversed misnaming (level 1's kind score 0.10 and 0.00) survives the worker.
3. **The models skip `INC` lines that are right.** Level 1 fires on every acc_frozen, tx_overrun and link_garbled window, yet qwen3.5:4b alarms on 3 of 8, 2 of 8 and 3 of 8 of them.
4. **Most invalid answers are evidence-format mistakes, not broken JSON.** `json_schema` held the JSON shape for every model that answered. The failures are ids with text attached (qwen3.5:2b: 117 of 124, for example `"INC1 src=podd"`), comma lists inside one string (`"E02, E04, E06"`), or ranges past the window's last line (`D01-D98` in a 30-line window). Strict scoring makes each one "no alarm".
   - This flatters qwen3.5:2b's false-alarm count: 21 of its 60 healthy answers are invalid, and 7 of those say `"status": "fault"`.
5. **qwen3.5:4b alarms most and is wrong most.** It has the best gap recall, 0.58, but alarms on 25 of 60 healthy items and 4 of 5 real windows.
   - The false-alarm gap to the next two models is unlikely to be chance: Fisher exact, two-sided, p = 0.03 against qwen3.5:9b's 13/60 and p = 0.009 against lfm3:1.7b's 11/60.
   - Its gap-recall lead is likely chance: 32 vs 29 and 32 vs 28 of 64, p = 0.72 and 0.60.
6. **lfm2.5:24b cannot be run with thinking off on Ollama 0.40.15.** It returned about 1.5k characters of reasoning and hit the 512-token cap on every call, 105 of 105, with no answer.
   - It did the same with `reasoning_effort: "none"`, `reasoning: {effort: "none"}`, `"think": false`, and native `/api/chat` with `think: false`.
   - Its `/api/show` has `"thinking": null`: no metadata to map "none" to off (docs.ollama.com, OpenAI compatibility).
   - With thinking allowed and `max_tokens` 4096, its smoke call used all 4,096 tokens on reasoning and still gave no answer (105 s). Its 4 benchmark calls each ran past 300 s and were stopped.
   - So it is not a worker candidate with this prompt. The offline-manager idea (design §2) would need its own test with a far larger token budget.

### Phase 2: thinking on (a sample only)
Settings: `reasoning_effort: "medium"`, `max_tokens` 4096, streaming, 300 s per call, 4 calls at a time. The run shared the GPU with the full test, so its latency is not single-stream.

**qwen3.5:9b, 19 items** (the first 19 of the shuffled quick order; stopped there so the full test could use the GPU), against the same 19 items with thinking off:

| qwen3.5:9b on 19 items | schema_ok | alarms on 16 visible faults | kind exact | alarms on 3 healthy | output tokens, mean | latency p50 |
|---|---|---|---|---|---|---|
| thinking on | 10/19 | 7/16 | 4 | 1/3 | 2,502 (9 of 19 hit the 4,096 cap) | 136 s |
| thinking off | 16/19 | 13/16 | 8 | 2/3 | 85 | 3.6 s |

- Answers that stopped on their own used 701–2,343 output tokens. The other 9 were cut at 4,096, with no answer.
- **lfm3:1.7b:** only its smoke call ran: 2,592 output tokens, a valid answer, 66 s. Its benchmark run was stopped before its first answer, for time.
- **lfm2.5:24b:** 4 of 4 calls ran past 300 s (phase 1, item 6).
- **qwen3.5:4b and qwen3.5:2b:** not run with thinking. qwen3.5:4b was last on the balanced score, and the qwen3.5:2b card warns that thinking can loop (design §3).

**What it shows** *(inferred)*: thinking costs about 30 times the output tokens and did worse on this sample. At 2,500 tokens even 50 tok/s would take 50 s per item, already past the worker's 30 s budget (design §4), and a laptop is slower than an L40S. So **thinking stays off for the worker.** Nineteen items are too few to say more about quality.

### Phase 3: the full test split (1,433 items), thinking off, the top 2 of phase 1
Settings: 4 calls at a time per model, with both models on the GPU together. Latency here is under load, not single-stream; use phase 1's latency.
- **qwen3.5:9b was cut at its time cap after 1,148 items.** Those are the first 1,148 of a fixed shuffled order, so they are a random 80% of the split.
- **lfm3:1.7b finished all 1,433.** Its row on the same 1,148 items is given too, for a like-for-like comparison.
- **Retried calls:** pod 1 was terminated during qwen3.5:9b's first attempt (below). 4 of its calls had timed out at 120 s while the thinking run held the GPU. Those 4 were asked again on pod 2. No call failed on pod 2.

| model | n | schema_ok | gap recall macro (micro, n) | healthy false alarms (95% upper) | without the motion group | real-data alarms | balanced | kind score | message (0–5) | prompt / output tokens |
|---|---|---|---|---|---|---|---|---|---|---|
| `lfm3:1.7b` | 1,433 | 0.883 | 0.437 (0.465, 340) | 79/447 (0.209) | 29/397 (0.098) | **0/5** | 0.260 | 0.383 | **3.97** | 2560 / 104 |
| `qwen3.5:9b` (cut at 80%) | 1,148 | **0.922** | **0.617 (0.596, 277)** | 122/366 (0.376) | 81/325 (0.292) | 1/3 | **0.283** | **0.463** | 3.75 | 2867 / 81 |
| `lfm3:1.7b`, the same 1,148 items | 1,148 | 0.879 | 0.445 (0.477, 277) | **62/366 (0.205)** | **21/325 (0.092)** | 0/3 | 0.276 | 0.381 | 3.94 | – |
| `l1` baseline (full test) | 1,433 | 1.0 | 0.0 (340) | 50/447 (0.139) | 0/397 | 0/5 | – | 0.512 | 3.69 | – |

**False alarms by healthy group** (alarms / n; the arm groups are holding, home, motion and clean; the others are pod groups):

| model | motion | clean | holding | home | sparse_no_echo | reflash | 08_dormant | covered | ts_batch | real |
|---|---|---|---|---|---|---|---|---|---|---|
| lfm3:1.7b | 50/50 | 18/124 | 0/37 | 0/81 | 5/30 | 2/30 | 1/30 | 1/30 | 2/30 | 0/5 |
| qwen3.5:9b | 41/41 | 42/98 | 21/32 | 2/64 | 3/24 | 4/26 | 5/23 | 3/28 | 0/27 | 1/3 |

**More columns:**

| model | recall macro (all visible) | level 1 / level 1 + model recall | kind exact / route right, given an alarm | kind corrections | severity / escalate accuracy | evidence hit / precision | latent items alarmed (not scored) |
|---|---|---|---|---|---|---|---|
| lfm3:1.7b | 0.647 | 0.599 / 0.785 | 0.53 / 0.61 | 0/89 | 0.67 / 0.79 | 0.48 / 0.34 | 101/138 |
| qwen3.5:9b | 0.803 | 0.593 / 0.835 | 0.57 / 0.60 | 0/71 | 0.59 / 0.76 | 0.60 / 0.46 | 96/102 |

**What phase 3 shows:**
- **The quick subset predicted the full test well.** lfm3:1.7b had 11/60 false alarms on quick and 79/447 on the full test (p = 0.86). qwen3.5:9b went from 13/60 to 122/366 (p = 0.07).
- **The two finalists swap places by metric.** qwen3.5:9b is ahead on gap recall and kind score; lfm3:1.7b has half the false alarms. Both differences are significant on the same 1,148 items (Fisher exact: gap p = 0.006, false alarms p = 4e-7).
  - balanced puts them close, 0.283 and 0.276, so the choice depends on what costs more: a missed subtle fault or a false page to the manager *(inferred)*.
  - Each false alarm becomes a manager call, a model round of 20–60 s (design §2). For tonight's demo, fewer false pages is the safer side, which is why the recommendation above picks lfm3:1.7b *(inferred)*.
- **qwen3.5:9b alarms on a held block** (21/32) and on clean arm windows (42/98). lfm3:1.7b alarms on 0/37 held-block windows.
- **The "latent" items may be mislabeled.** They are planted faults with nothing visible yet, and both models alarm on most of them (73% and 94%). Level 1 itself fires on 57 of qwen3.5:9b's 102. The `latent` label in `gen_arm.py` needs a look before these items are scored *(inferred)*.

### How the run went (for the next person)
- **Pod 1** (`5pg8sqwflon4c9`, 12:40–14:11 UTC) ran phase 1 and the thinking samples.
  - The plan was restarted twice on the same pod: once to change the ranking to balanced, and once to move lfm2.5:24b into the thinking phase. Each restart killed only the plan process, not the pod. Finished runs were reused and cut runs resumed (`--resume`).
  - It ended when a bug stopped the plan: `report_partial` raised on an empty answers file after I stopped lfm3's thinking run before its first answer. The plan's `finally` terminated the pod, as designed.
  - The bug is fixed: an empty run now logs "no answers" and the plan goes on. One leftover run.py, started by the dying plan's phase-3 thread, recorded 246 failed calls against the dead pod. They were deleted, and run.py children are now stopped in the `finally`.
- **Pod 2** (`mccmanpqxsllvf`, 14:13–15:23 UTC, cap 80 min) ran only `full qwen3.5:9b lfm3:1.7b`. On the same host, the pulls were slower: 348 s for qwen3.5:9b against 83 s on pod 1.
- **Throughput:** 4 calls at a time per model, with both models on the GPU together, gave each model about its one-at-a-time rate or less: qwen3.5:9b about 12 items/min (17/min one at a time in phase 1) and lfm3:1.7b 26–28/min (28/min). For the next run, budget time from phase 1's single-stream rate *(inferred)*.

## What it measures
- One 6 s log window, rendered as lines with ids. The worker model answers six keys (`status`, `kind`, `severity`, `evidence`, `escalate`, `message`). Plain code checks the answer strictly.
- **Headline: gap recall.** This is recall on visible faults where level 1, as it runs today, raises nothing on the same window.
- **The gates** (design §4):
  - `schema_ok` ≥ 98%;
  - false-alarm 95% upper bound ≤ 1% on healthy items;
  - 0 alarms on real data;
  - p95 latency ≤ 30 s.
- **The tracks:**
  - Track E is the default. It is the window plus `INC` lines for the level-1 findings at that moment.
  - Track D is the same window without them.

## Files (all in `prototypes/arm-sim/workerbench/`)
| File | What it does |
|---|---|
| `kinds.py` | The 26 kinds, with gold severity, route and the level-1 alias table. `kind_score()`: 1 for an exact kind, 0.5 for the right route |
| `render.py` | `render(item, profile=full\|lite, track=D\|E) -> (text, idmap)`. Gold evidence is stored as row timestamps, so the profile never changes a label |
| `l1.py` | Level 1 replayed on a window: `armsim/level1.py` (loaded by file path, so no MuJoCo) plus `detect.py`. `l1_answer()` gives the same as a six-key answer |
| `gen_arm.py` | Fork of `factory.one_run`: the same draws, plus full-precision timestamps, the grasp state per row, skill results and incident times. Needs the `.venv` |
| `gen_pod.py` | 16 pod variants plus 4 pod hard-negative groups, as offline podd snapshots. Each variant asserts its expected level-1 result |
| `gen_real.py` | Windows of `firmware/ultrasonic-sample.json`, and windows rebuilt from a podd var dir's logs |
| `prompt.py` | `SYSTEM` (frozen, `wb-p1`), `ANSWER_SCHEMA`, `messages()`. The body follows the data report's §1.4, with a few healthy facts added *(inferred)*: what `pred=-` means, that no_echo is normal when the sensor points at nothing, that a deliberate reflash reboots once (`rst=pin new_build=1`), what the JS lines are, and that an INC line may name the wrong kind |
| `score.py` | `parse_answer`, `gold_ids`, `expand_ids`, `message_score` (M1–M5), `score_item`, `upper95`, `summarize` |
| `run.py` | Runs a model behind any OpenAI-compatible endpoint, or a baseline. Writes `answers.jsonl`, `report.json` and `report.md` |
| `manager_eval.py` | Stub. It documents the manager eval and lists the 30 items it would send; it needs `escalation.py` and the manager gateway |
| `tests/test_workerbench.py`, `tests/fake_openai.py` | Sanity checks, plus the model path against a stdlib fake server |
| `runpod_wb.py` | One Runpod GPU pod running stock `ollama/ollama`, driven over Runpod's REST API and the pod's Ollama HTTP API. `run.py` runs here against the pod's proxy URL. `verify-tags`, `launch`, `wait`, `pull`, `smoke`, `plan` (all phases), `full` (full test only), `status`, `terminate`, `pods`, `report-partial`. A detached watchdog deletes the pod at its cap; `plan`/`full` end in try/finally `terminate`. Dry-run against fake Runpod and Ollama servers with `RUNPOD_API_BASE`, `WB_POD_URL`, `WB_RUNS`, `WB_HARD_CAP_S` |

## The dataset (`data/workerbench/`, gitignored, regenerate with the commands below)
| File | Items | Source |
|---|---|---|
| `sim-factory-test.jsonl` | 828 | seed 99, 600 runs (the v3-heldout runs) |
| `synth-pod-test.jsonl` | 600 | 20 groups × 30, targets 600–2000 mm |
| `real-sample-test.jsonl` | 5 | 60-row windows of Olir's real sample |
| `sim-factory-dev.jsonl` | 903 | seed 1, 2000 runs (the v2 runs) |
| `synth-pod-dev.jsonl` | 510 | 17 groups × 30, targets 40–600 mm; images 08 and stuck-max held out |
| `vpod-live-live.jsonl` | 24 | split `live`: a live virtual pod plus podd on my own host; integration check only |

**The test split (1,433 items):**
- **Healthy:** 447 items, 323 of them hard negatives.
  - Arm groups: holding 37, home 81, wave end ("motion") 50, clean pre-onset 124.
  - Pod groups: covered, reflash, sparse no_echo, timestamp batching, image 08 dormant, 30 each.
  - Real: 5.
- **Faults:**
  - 848 visible: about 60 per arm kind, at least half of them missed by level 1 where there were enough, plus 30 per pod variant.
  - 138 latent: the fault was planted but nothing shows yet. These are reported, not scored.
- **Kinematics:** 705 items run with it off (all pod items, plus 100 arm items).
- **`--quick`:** 236 items (8 per variant, half of them level-1 misses where there are enough, plus 60 healthy).

**Prompt size**, measured on `--quick` in characters, median / max:
- arm items: full 5,616 / 6,548; lite 2,354 / 3,062;
- pod items: about 4,080 / 4,852;
- the system prompt: 3,485.

At about 3 characters per token *(inferred)*, a full arm prompt is about 3k tokens with the system prompt, and a lite one about 2k. `run.py` records the server's real `prompt_tokens`.

## Self-checks (all pass)
**The fork reproduces the fault factory exactly.** Output of `gen_arm`:
```
600 runs in 86 s, 2470 candidate windows
self-check: incident windows where level 1 replays the watchdog's incident: 249, mismatches: 0, level 1 firing before the incident: 0
vs data/sim-faults/v3-heldout/index.jsonl: 600 of 600 runs identical (fault kind and detected list); differing idx: []
2000 runs in 222 s, 8498 candidate windows
self-check: incident windows where level 1 replays the watchdog's incident: 797, mismatches: 0, level 1 firing before the incident: 0
vs data/sim-faults/v2/index.jsonl: 2000 of 2000 runs identical (fault kind and detected list); differing idx: []
```

**The run-level level-1 tally** (a run counts once level 1 alarmed during its plan):

| Planted fault | dev, seed 1 (= v2) | test, seed 99 (= v3-heldout) |
|---|---|---|
| none | 0/280 | 0/74 |
| joint_stuck | 230/230 | 65/65 |
| us_frozen | 132/254 | 40/68 |
| us_scale | 110/216 | 56/94 |
| us_offset | 95/264 | 30/78 |
| us_noisy | 76/254 | 14/77 |
| joint_offset | 95/265 | 21/66 |
| joint_reversed | 59/237 | 23/78 |

- The dev column equals the v2 tally in `docs/research/worker-benchmark-data.md` §2.6.
- The test column equals the "Level 1 caught" column in `docs/healer-data-levels.md:77-86`.
- **Window level:** the `l1` and `l1-arm` baselines replay the watchdog's incident on every incident window in the test file (167/167 with kinematics on).

**Pod variants against level 1.** Each variant asserts what it expects of level 1 (`gen_pod.py`, both splits). Measured counts on the test split:

| Variant | Gold kind | Level 1 fired | Data report said |
|---|---|---|---|
| 01, 02-real, 02-sim, 03, 04, 05, 06, 07, frozen-at-no_echo | crash_loop, link_garbled, clock_skew, acc_frozen, tx_overrun, acc_gravity, watchdog_loop, us_mismatch, us_frozen | 30/30 each | caught |
| 02-early | clock_skew | 0/30 | missed |
| 08-held | us_mismatch | 15/30 (all with ≥2.5 s since the hand left, none with ≤1.5 s) | missed to 1.5 s, caught from 2.5 s |
| frozen-early | us_frozen | **4/30** | missed by both |
| frozen-at-out_of_range, spikes, stuck-max | us_frozen, us_noisy, us_out_of_range | 0/30 | missed |
| all 5 pod hard-negative groups | none | 0/30 | – |

- **frozen-early differs from the prediction.** Level 1 still fires when the healthy rows inside its 4 s window happen to share the frozen echo time. Real echo times repeat for up to 1.8 s (data report §1.1). So this variant's expectation isn't asserted, and `run.py` recomputes level 1 on every item.
- **Two label choices of mine** *(inferred)*:
  - frozen-at-no_echo accepts both `us_frozen` and `us_no_echo`. Without kinematics a frozen no-echo looks exactly like a lost echo.
  - us_offset and us_scale windows accept both kinds when the arm moved less than 20 mm. At one height the two look the same (`level1.py:69-70`).

## Baselines, full test split (1,433 items, track E, profile full)
| Baseline | schema_ok | recall, macro | gap recall, macro | L1 / combined recall | healthy false alarms (95% upper) | real alarms | kind score | severity / escalate | evidence hit / precision | message 0–5 |
|---|---|---|---|---|---|---|---|---|---|---|
| `oracle` | 1.0 | 1.0 | 1.0 | 0.599 / 1.0 | 0/447 (0.0067) | 0/5 | 1.0 | 1.0 / 1.0 | 1.0 / 1.0 | 5.0 |
| `healthy` | 1.0 | 0.0 | 0.0 | 0.599 / 0.599 | 0/447 (0.0067) | 0/5 | 0.0 | 0.31 / 0.31 | – | – |
| `alarm` | 1.0 | 1.0 | 1.0 | 0.599 / 1.0 | 447/447 (1.0) | 5/5 | 0.0 | 0.29 / 0.69 | 0 / 0 | 2.07 |
| `l1` (watchdog + podd) | 1.0 | 0.693 | 0.0 | 0.599 / 0.599 | **50/447 (0.139)** | 0/5 | 0.512 | 0.64 / 0.68 | 0 / 0 | 3.69 |
| `l1-arm` (watchdog only) | 1.0 | 0.190 | 0.0 | 0.599 / 0.599 | 0/447 (0.0067) | 0/5 | 0.165 | 0.43 / 0.44 | 0 / 0 | 4.12 |
| `l1-pod` (detect.py only) | 1.0 | 0.597 | 0.0 | 0.599 / 0.599 | 50/447 (0.139) | 0/5 | 0.347 | 0.54 / 0.59 | 0 / 0 | 3.22 |

**Gap items:** 340 in the full test split, 64 in `--quick`. "L1 recall" always means the `l1` rule set, both watchdog and podd.

**Level 1 (`l1`) by kind**, on visible items:

| Kind | n | Level-1 recall | Gap items | Kind score |
|---|---|---|---|---|
| joint_offset | 60 | 0.50 | 30 | **0.10** |
| joint_reversed | 23 | 1.00 | 0 | **0.00** |
| joint_stuck | 70 | 0.50 | 35 | 0.27 |
| us_frozen | 155 | 0.46 | 83 | 0.46 |
| us_noisy | 91 | 0.36 | 58 | 0.26 |
| us_offset | 59 | 0.51 | 29 | 0.49 |
| us_scale | 60 | 0.50 | 30 | 0.50 |
| us_mismatch | 60 | 0.75 | 15 | 0.75 |
| us_out_of_range | 30 | 0.00 | 30 | 0.00 |
| clock_skew | 60 | 0.50 | 30 | 0.50 |
| acc_frozen, acc_gravity, crash_loop, watchdog_loop, link_garbled, tx_overrun | 30 each | 1.00 | 0 | 1.00 |

**Level 1 (`l1`) false alarms by healthy group:**
- **motion:** 50 of 50.
- **every other group:** 0. That is clean 124, home 81, holding 37, and covered, reflash, sparse_no_echo, ts_batch and 08_dormant at 30 each, plus real 5.

**What the baselines show:**
1. **podd raises `us_intermittent` on every wave-end window** (50/50). That is level 1's only false alarm on healthy data.
   - The beam sweeps open space, so 5–80% of pings return no_echo, which is the rule's band (`detect.py:230-234`).
   - **Seen live as well:** on my own host (`--pod sim`), podd opened `us_intermittent` "Ultrasonic drops out intermittently (74% no_echo)" during a wave (its `logs/incidents.jsonl`).
   - It is a warn, so it doesn't park the arm, which parks only on critical pod incidents (`host.py` `on_pod_incidents`).
   - It does put a misleading `INC` line in track E. Those 50 windows are now a hard negative for the worker *(inferred)*.
2. **Level 1 names the joint faults wrong.** Its kind score is 0.10 on joint_offset (it says us_offset, us_scale or us_intermittent) and 0.00 on joint_reversed (us_no_echo). That matches the data report's 0-of-95 and 0-of-59. Kind correction is where the worker can add value even when level 1 fires *(inferred)*.
3. **joint_reversed has no gap items.** Every visible window already trips a rule.
   - `l1` counts podd too, so its spurious `us_intermittent` also shrinks the gap set. For example, 13 joint_stuck windows count as "level 1 fired" for the wrong reason (`base-l1/report.json`, confusion `joint_stuck->us_intermittent`).
4. **The `l1-arm` message score of 4.12** is the reference bar for messages: level 1's own sentences, scored with the same rubric.

Raw reports: `prototypes/arm-sim/runs/wb/base-<name>/report.md` (gitignored).

## The model path, tested against a fake server
`tests/fake_openai.py` is a stdlib `http.server` whose reply mode is the request's `model`. `test_workerbench.py` runs `run.main()` against it (output from `python3 workerbench/tests/test_workerbench.py`):
```
ok    test_the_oracle_scores_100_percent  (0.2 s)
ok    test_always_healthy_has_no_recall_and_no_false_alarms  (0.2 s)
ok    test_always_alarm_has_100_percent_false_alarms  (0.1 s)
ok    test_l1_answer_matches_each_variants_expectation  (0.0 s)
ok    test_the_l1_baseline_replays_the_watchdog_on_incident_windows  (0.2 s)
ok    test_line_ids_are_unique_and_every_gold_row_renders  (0.3 s)
ok    test_the_answer_schema_accepts_the_oracles_answers  (0.1 s)
ok    test_parse_answer_strict_lenient_and_schema  (0.0 s)
ok    test_upper95_is_about_3_over_n_at_zero  (0.0 s)
ok    test_quick_subset_is_deterministic  (0.0 s)
ok    test_model_path_good_answers_and_headers  (0.1 s)
ok    test_model_path_invalid_json_counts_as_no_alarm  (0.1 s)
ok    test_model_path_fenced_and_prose_are_lenient_only  (0.2 s)
ok    test_model_path_json_only_in_reasoning_is_invalid  (0.1 s)
ok    test_model_path_cut_off_for_length_is_invalid  (0.0 s)
ok    test_model_path_slow_answers_time_out_and_the_run_goes_on  (3.6 s)
ok    test_model_path_http_errors_are_recorded_and_429_is_retried  (2.1 s)
ok    test_model_path_cut_transfer_is_recorded_and_the_run_goes_on  (0.1 s)
ok    test_model_path_refused_connection_is_recorded  (0.1 s)
ok    test_model_path_native_usage_fallback_and_streaming  (0.2 s)
ok    test_resume_skips_answers_already_written  (0.1 s)
all passed: 0 failing; items: 272 (with arm items)
```
**What these tests cover:**
- **Headers:** a plain `User-Agent: workerbench/0.1`. Runpod's Cloudflare blocks Python's default agent with error 1010 (`docs/healer-data-levels.md:60`).
- **Request body:** the `json_schema` `response_format` and `max_tokens`.
- **Usage:** read from `usage`, with a fallback to Ollama's native `prompt_eval_count`/`eval_count`.
- **Streaming:** SSE with `stream_options.include_usage`, plus time to first token.
- **Bad answers:**
  - invalid JSON is no alarm;
  - fenced JSON or JSON inside prose is lenient only;
  - JSON that appears only in `message.reasoning` is invalid;
  - `finish_reason: length` is invalid.
- **Slow answers:** a 2 s reply with `--timeout 0.5` is recorded as a failed call and the run goes on.
- **Errors and resume:**
  - HTTP 500, a refused connection, and a transfer cut mid-body (`IncompleteRead`) are recorded, and the run goes on;
  - 429 is retried;
  - `--resume` skips answers already written.

## Commands
From `prototypes/arm-sim`. Only `gen_arm` needs the `.venv` (MuJoCo); everything else is standard library.
```bash
# dataset (about 1.5 min for test, 5 min for dev, on 3 workers)
PYTHONPATH=. .venv/bin/python -m workerbench.gen_arm --seed 99 --n 600 --split test --workers 3
PYTHONPATH=. .venv/bin/python -m workerbench.gen_arm --seed 1 --n 2000 --split dev --workers 3
python3 -m workerbench.gen_pod --seed 99 --split test --per-variant 30
python3 -m workerbench.gen_pod --seed 1 --split dev --per-variant 30
python3 -m workerbench.gen_real                       # real sample windows

# baselines
for b in oracle healthy alarm l1 l1-arm l1-pod; do python3 -m workerbench.run --baseline $b --label base-$b; done
python3 -m workerbench.run --baseline oracle --quick --label base-oracle-quick

# tests
python3 workerbench/tests/test_workerbench.py

# the GPU run (what produced "Model results" above); RUNPOD_API_KEY=proxy-injected in a Claude cloud session
RUNPOD_API_KEY=proxy-injected python3 -m workerbench.runpod_wb plan            # all phases, then terminate
WB_HARD_CAP_S=4800 RUNPOD_API_KEY=proxy-injected python3 -m workerbench.runpod_wb full qwen3.5:9b lfm3:1.7b
RUNPOD_API_KEY=proxy-injected python3 -m workerbench.runpod_wb pods            # must print []

# a model, on a Runpod pod's Ollama through the proxy, or the laptop's Ollama
python3 -m workerbench.run --quick --track E --profile lite --json-mode schema \
  --endpoint https://<pod-id>-11434.proxy.runpod.net/v1 --model qwen3.5:4b --label qwen3.5-4b-nothink \
  --extra-body '{"reasoning_effort": "none"}' --timeout 120
python3 -m workerbench.run --quick --endpoint http://192.168.50.1:11434/v1 --model qwen3.5:4b --label pi-qwen4b
```
**Useful flags:**
- `--concurrency N` sends parallel calls; the default is one at a time, as on the bench (`docs/healer-benchmark-progress.md:92`).
- `--repeat 3` reports stability: how often the alarm or the kind flips between repeats.
- `--resume` continues a run that was cut off.
- `--api-key` (or the `WB_API_KEY` environment variable) sends a bearer token, for endpoints that need one.
- `--limit N` runs only the first N items, as a smoke test.

## Not done, or not verified
- **No laptop or Pi latency.** Every latency above is an L40S plus a Cloudflare round trip. The worker's real number (`run.py --endpoint http://192.168.50.1:11434/v1` from the Pi, design §3) is still owed. No 20-item CPU sample was run either: there is no Ollama in this container.
- **Ollama behaviour, now checked on the real server (0.40.15):** `response_format: json_schema` held the JSON shape for every model that answered. `reasoning_effort: "none"` turned thinking off for the qwen3.5 models and lfm3:1.7b (empty `reasoning`), but not for lfm2.5:24b (see phase 1). `server/pi_check.py` was not run (it needs the bench's Ollama).
- **HTTPS through this container's agent proxy works** with plain urllib and no extra CA setting: every call above went through it.
- **No lead-time metric yet.** Lead time over level 1 is not computed.
- **`--shots` is refused for now.** Few-shot examples from dev aren't built.
- **No L3 baseline.** It is deferred, as the design says (§4).
- **The quick subset can't pass the false-alarm gate.** With 60 healthy items, 0 alarms still gives an upper bound of 0.049. The gate is meant for the full test.
- **Real podd data:** `windows_from_podd_logs` is tested only on the virtual pod's podd logs (the `live` split).
  - No real podd logs are in `samples/pod/` yet.
  - On real logs, the labels come from podd's own open incidents, so a person must confirm them. In the live run, the false `us_intermittent` became a fault label.
- **No real-arm data.** There are no real `arm-run/v1` runs yet, so the kinematics-off real-arm slice is empty.
- **Category 2 is out.** `grasp_missed` isn't in tonight's bench (design §4).
- **The live host was shut down after its run.** It used my own paths under `/tmp/claude-0/wb-agent/host`.
