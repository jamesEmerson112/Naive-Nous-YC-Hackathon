# Worker-model benchmark: dataset, baselines, how to run a model

_Built Sun Nov 8, ~4 AM PST, as step 10 of `docs/worker-manager-healer.md` §5, to the spec in §4 of that doc and in `docs/research/worker-benchmark-data.md`. Models were scored on one Runpod GPU from ~4:40 AM PST (next section); the tables after it are the baselines. *(inferred)* marks my own conclusions._

## Model results: one Runpod GPU (Sun Nov 8, from 4:40 AM PST)
_Run in progress; this section is rewritten after each model. Interim numbers, quick subset, thinking off:_

| model | n | schema_ok | gap recall macro (n=64) | healthy false alarms (95% upper) | real alarms | kind | message | p50 / p95 s |
|---|---|---|---|---|---|---|---|---|
| `lfm3:1.7b` | 236 | 0.881 | 0.426 | 11/60 (0.285) | 0/5 | 0.429 | 3.98 | 2.10 / 4.61 |
| `qwen3.5:4b` | 236 | 0.920 | 0.579 | 25/60 (0.531) | 4/5 | 0.418 | 3.74 | 3.20 / 5.27 |

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

# a model, on a Runpod pod's Ollama through the proxy (next step), or the laptop's Ollama
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
- **No model has been run.**
  - Nothing here measures a real model's validity, recall, tokens or latency.
  - There is no Ollama in this container, and I installed none, as instructed. So the 20-item CPU sample (design §3) wasn't run.
- **Server behavior is checked only against my fake server.** Ollama's real handling of `response_format: json_schema` and of `reasoning_effort` (or `"think": false`) needs one real call before the Runpod run.
  - `server/pi_check.py` is the existing check (design §3).
- **HTTPS through this container's agent proxy is untested.** urllib reads `HTTPS_PROXY`, but trusting the proxy's CA (`/root/.ccr/ca-bundle.crt`) may need `SSL_CERT_FILE` set. Run from the pod itself (`--endpoint http://127.0.0.1:11434/v1`), this doesn't arise.
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
