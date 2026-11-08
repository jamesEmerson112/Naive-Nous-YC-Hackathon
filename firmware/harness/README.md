# Pod healer harness: agent loop and blind evaluation

The agent loop and scenario evaluation that [`../docs/HARNESS.md`](../docs/HARNESS.md) asks for (sections 4–9),
on top of Olir's `heal` tools. Nothing under `healer/`, `pod/` or `demo/` is changed.

| File | What it does |
|---|---|
| `agent.py` | The loop: system prompt from `system_info`, tools from `healer/tools.json` in file order, risk gate, one tool call at a time, transcripts. `run` handles the open incidents once; `watch` wakes on new ones (10 s debounce). |
| `evaluate.py` | Blind runs of scenarios 01, 02, 03, 05 and 06: operator steps, agent run, scoring from the bench. Holds the answers, so never show it to the agent. |
| `tests/test_harness.py` | Offline tests: fake bench, scripted model, fake Ollama server, scorer cases. |

## Drivers
- **`claude`**: the online fallback, `claude-opus-5-5` through the Anthropic API, effort `high`, summarized
  thinking, server-side refusal fallback (`fallbacks="default"`), automatic prompt caching, append-only
  history. Needs `ANTHROPIC_API_KEY`.
- **`ollama`**: a local model, `qwen3.5:4b` with thinking on by default, 32k context. `--tools core` offers 17 of
  the 38 tools (about 1.8k tokens of schemas instead of 3.4k; the briefing adds 2.6k).

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
- The Ollama model runs wherever `--ollama-url` points (default: the machine running the harness), not on the Pi.
