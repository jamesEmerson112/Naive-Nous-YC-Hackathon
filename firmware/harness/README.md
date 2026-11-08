# Pod healer harness: agent loop and blind evaluation

The agent loop and scenario evaluation that [`../docs/HARNESS.md`](../docs/HARNESS.md) asks for (sections 4–9),
on top of Olir's `heal` tools. Nothing under `healer/`, `pod/` or `demo/` is changed.

**Running it on the bench or benchmarking it:** start with [`HANDOFF.md`](HANDOFF.md), the runbook and current state.

| File | What it does |
|---|---|
| `agent.py` | The loop: system prompt from `system_info`, tools from `healer/tools.json` in file order, risk gate, one tool call at a time, transcripts. `run` handles the open incidents once; `watch` wakes on new ones (10 s debounce). |
| `evaluate.py` | Blind runs of scenarios 01, 02, 03, 05 and 06: operator steps, agent run, scoring from the bench. Holds the answers, so never show it to the agent. |
| `tests/test_harness.py` | Offline tests: fake bench, scripted model, fake Ollama server, scorer cases. |
| `console.py` | The live view: streams the model's thinking and text as it's generated, each tool call with the gate's decision, and a readable summary of each result. |
| `view.py` | Replays a saved run through the same view (`python3 view.py` for the newest; `--thinking brief`, `--delay 0.4` for an audience). |

## Drivers
- **`ollama`** (the default): a local model, `qwen3.5:4b` with thinking on by default, 32k context, stdlib
  only. `--tools core` offers 23 of the 38 tools: every tool the briefing names, plus the source, build and
  flash tools (about 2.3k tokens of schemas instead of 3.4k; the briefing adds 2.5k).
- **`claude`**: the online fallback, `claude-opus-5-5` through the Anthropic API, effort `high`, summarized
  thinking, server-side refusal fallback (`fallbacks="default"`), automatic prompt caching, append-only
  history. Needs the `anthropic` package and `ANTHROPIC_API_KEY`.

**Online fallback through Romulus (demo).** On the demo the Pi only calls out to Kent's server (`CLOUD_URL`).
`--base-url` points the `claude` driver at any Messages-compatible endpoint, so a passthrough on that server
would keep the API key off the Pi. Kent's Fleet Doctor L1 (`POST {CLOUD_URL}/v1/devices/{id}/doctor/ask`, off by
default and still being built) returns hypotheses, not tool calls, so it would plug in as advice for the
local model (for example an `ask_doctor` tool) rather than as a driver. Not built yet; the driver interface
(`start`, `step`, `add_results`) is the seam.

**Not this harness's job:** reporting to the cloud. `tools/edge_bridge` already reports podd incidents and
the agent's actions (from what `heal` records: audit log, deployments, notes) through its outbox. The
harness only writes local transcripts and reaches the pod only through `heal`, so podd stays the only
process on the serial port.

## Setup and use
```bash
cd firmware/harness
uv venv .venv && uv pip install --python .venv/bin/python anthropic pytest   # anthropic only for the claude driver
.venv/bin/python -m pytest tests -q

# the tools run on the Pi with the Nucleo attached; from another machine use SSH with key auth
.venv/bin/python evaluate.py --target pi@HOST:/path/to/firmware --driver claude --scenarios 05
.venv/bin/python evaluate.py --target pi@HOST:/path/to/firmware --driver ollama --tools core --scenarios 05,01,02,03,06
.venv/bin/python agent.py watch --target pi@HOST:/path/to/firmware --driver ollama        # live healer
```
The bench needs `demo/scenario setup` and `build-images` done once, and `healer/bin/podd` running. Each
scenario run starts and ends with `demo/scenario reset`. Runs land in `runs/<time>-<label>/`
(`transcript.jsonl`, `report.md`, `score.json`) and `runs/results.jsonl`.

## Watching a run
Every `agent.py run` and `watch` streams to the terminal (`--view full`, the default; `brief` keeps the first
lines of each thinking block, `off` hides thinking, `none` prints only the JSON summary). For each turn it
shows the thinking (`┊`) and text (`▌`) as Ollama generates them, then each call (`▶ name args [risk]`, or
`REFUSED by the harness: why`) and its result (`◀ seconds summary`): triage findings by severity, diffs in
colour, flash verification, `arm_test` verdicts. The transcript holds everything, so `view.py` can replay
any run the same way.

## The arm task (`--tools arm`)
The robot arm depends on the pod's ultrasonic distance. `--tools arm` offers 17 `heal` tools (observe, read
the source and history, flash, notify; no edits or builds) plus `arm_test`, a harness tool that runs
`arm/safe_replay.py` on the bench: the start gate only (`--check-only`), or the real motion with
`--arm-port`. It's capped at `--arm-cap` runs (default 4). The system prompt gains the arm rules
(`ARM_RULES`): a real obstruction goes to a person, a firmware fault gets `fw_rollback` to known-good, and
the run is done only when `arm_test` passes. `arm/heal_test.py` is the scenario test (bad release v1.7 →
`us_mismatch` → rollback → arm runs) and scores from the bench; see `arm/README.md`.

All runs also get one turn-budget notice, appended to a tool result and marked `[harness: ...]`, when a
quarter of the turns are left (in `agent.py`, `budget_notice`).

## What the loop enforces
- **Only the tools.** No shell. The model sees `system_info` (plus a few operating rules), the tool list
  and a neutral kickoff built from `heal incidents`.
- **Gate:** `read`, `runtime`, `code` and `notify` are allowed; `flash` (`fw_flash`, `fw_rollback`) is capped
  at 3 per run. Unknown tools are refused as invalid. Refused as forbidden: `pod_cmd inject ...`, paths
  outside the workspace, image paths instead of crcs (they would let `fw_*` read `demo/`), and revisions
  starting with `-` (git would read them as options). Refusals go back to the model as `is_error` results.
- **Calls:** one at a time in the model's order, 180 s timeout, results capped at 30k characters keeping
  the end (`triage_after`, rollback details). Claude gets all of a turn's results in one message; Ollama
  takes one `tool` message per result.
- **Transcripts:** every model turn (text, thinking, usage) and every call (arguments, gate decision,
  result, duration) in `transcript.jsonl`.
- **Blind mode (evaluation only):** records older than the scenario start are hidden, so earlier runs and
  bench tests can't leak fixes: lookback windows (`events`, `log_search`, `incidents`, `telemetry_*`) are
  clamped to the time since the start, and `fw_images` builds and deployments, image notes and old
  serial-log lines are filtered.

## Scoring (HARNESS.md 8)
Everything numeric comes from the bench after the run: `var/deployments.jsonl`, `var/notifications.jsonl`,
`chip_status`, a fresh `triage`, plus one end-state check per scenario: |a| within 900–1150 mg at rest (05),
radio frames decoding (01), `chip_clock` ratio 1.0 (02), a fresh `trigger 03` healed by the firmware
itself (03), and a `notify_human` call (06; the simulated replug is recorded but not scored).
`passed` = root cause named, and a durable fix verified by `fw_flash` (a forward fix, or a rollback where
HARNESS.md accepts one: 01, 02), and the end state holds. Also recorded: unnecessary flashes, forbidden
and invalid calls, tool calls by risk, tokens, time to the verified fix, and the agent's diff against the
release.

## Limits
- "Root cause named" is a keyword check on what the agent stated (text, notes, commits, notifications), not
  its thinking. `score.json` lists the matched words; read the report before trusting a borderline case.
- Scenarios 03 and 06 use operator fault injection, which leaves traces the agent can see (the `inject`
  command's response line, the `inject=` field in boot lines and `health`). A physical trigger avoids that.
- Scenario 04 is left out: it wedges the probe's USB serial bridge (HARNESS.md 9, issue 1).
- The Ollama model runs wherever `--ollama-url` points (default: the machine running the harness).
