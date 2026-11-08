# Handoff: running and benchmarking the pod healer agent

For Olir's Claude session, from Ishaan's (Sat Nov 7, ~10 PM PST). The loop was written and tested without
the bench. Olir's session now owns the bench runs, the benchmark and any fixes. [`README.md`](README.md) is the
reference for flags, the gate, blind mode and scoring. This file holds the runbook and the current state.

## State
- **Verified:**
  - 13 offline tests pass on Python 3.11 and 3.14. The 2 Claude-driver tests skip when the `anthropic` package
    is missing.
  - A live run on a Mac with no board: qwen3.5:4b through Ollama with `--tools core`. It made 8 turns and 11
    calls through the real `healer/bin/heal`, with no refused or invalid calls. It found podd, OpenOCD and the
    workspace missing, and filed `notify_human`.
  - Every field the scorer reads was checked against `healer/healer/*.py` and `demo/scenario`.
- **Not verified:** anything on the bench (podd, SWD, flashing, the scenarios, scores on real data), the SSH
  `--target` path, Ollama on the Pi (speed unknown) and the `claude` driver against the live API.

- **Verified on the bench (Sat Nov 7, ~10:30–11 PM PST, Olir's session, branch `olir/arm-ultrasonic-heal`):**
  - Steps 1–3: podd up (link at 115200), `server/pi_check.py` 3 of 3 through the laptop relay at
    `192.168.50.1:11434`, and a smoke run through the real `heal` tools (10 turns, 10 calls, 0 invalid, 0 tool
    errors, stop `max_turns`). It put the bench's real `us_no_echo` on v1.0 down to hardware and didn't flash.
  - Turn speed, qwen3.5:4b on the laptop: median 3.4 s per model turn, p90 10.2 s (166 turns; `RESULTS.md`).
  - The new arm scenario on the real chip: `demo/scenario start 07` flashes v1.7 (`A12774C3`, runs), and
    `heal fw_rollback` restores known-good, verified, in 19 s.
  - `arm/heal_test.py` on the bench: preflight passes, then it stops at stage 1 because the HC-SR04 gets no
    echo at all (`pod_cmd "us check"`: no pulse, 0 mV at D3, since about 19:20). That's a wiring or 5 V problem
    on the sensor, not software.
- **Still not verified:** the full arm scenario on the bench, and steps 4–5. `evaluate.py` refuses to start
  while `us_no_echo` is open ("bench not clean"), so both wait on the HC-SR04 wiring. Also unverified: the
  real arm motion (`--arm-port`, no arm on USB), the SSH `--target` path and the `claude` driver.
- **Changed for the arm scenario (see `README.md`, "The arm task"):** the live view (`console.py`,
  `view.py`), Ollama streaming, the `arm_test` harness tool, `--tools arm`, the arm rules, the turn-budget
  notice, the `us_mismatch` triage rule (restart podd after pulling, so it loads), and bad release v1.7.

## Steps (on the Pi, in `firmware/harness`)
1. **Bench up.** `git pull` on main, then HARNESS.md §3. Done when `healer/bin/heal triage` (from
   `firmware/`) shows `"findings": []` with podd running.
2. **Model reachable.** Choose where Ollama runs (next section). Done when `curl <ollama-url>/api/tags` lists
   `qwen3.5:4b`.
3. **Smoke run:** `python3 agent.py run --tools core --ollama-url <url> --max-turns 6`. Done when the printed
   summary shows `tool_calls` above 0, `invalid` 0, and a `stop` other than `model_error`. The last line is
   the transcript path.
4. **One scenario:** `python3 evaluate.py --tools core --ollama-url <url> --scenarios 05`. Done when
   `runs/results.jsonl` has a new row for 05 without an `error` key. `passed: false` is a result. `error`
   means the harness or the bench broke.
5. **Baseline:** the same with `--scenarios 05,01,02,03,06`, repeated as time allows. Scores vary from run to
   run, so several runs per scenario are worth more than one clean sweep.
6. **Report** (below).

Live healer for the demo: `python3 agent.py watch --tools core --ollama-url <url>` wakes on new warn or
critical incidents, after a 10 s debounce. It prints `podd unreachable` when podd is down.

## Where Ollama runs
- **On the Pi.** This backs the offline claim and measures Pi speed. Leave `--ollama-url` at its default
  (`localhost`). Pulling `qwen3.5:4b` is a 3.3 GB download. If memory runs short at the default 32k context,
  pass `--num-ctx 16384`. Runs then stop with `context_full` sooner.
- **On a laptop over the LAN.** This is the fastest benchmark, and the harness stays on the Pi. Run
  `OLLAMA_HOST=0.0.0.0 ollama serve` on the laptop, and on the Pi pass
  `--ollama-url http://<laptop-ip>:11434`.
- **Everything on a laptop**, with tools over SSH: `--target pi@<host>:/path/to/firmware`. This path is
  unverified and needs SSH key auth. It runs with BatchMode, so it never prompts for a password.

## Report
Commit `firmware/harness/RESULTS.md` on main. `runs/` stays local.
- **Header:** the model, the tool set, where Ollama ran and the harness commit.
- **One row per run:** scenario, `passed`, `root_cause_named`, `fix.kind`, agent flashes, tool calls,
  turns, tokens, seconds and `stop`.
- **Turn speed:** each transcript records seconds per model turn (`model_turn` → `raw.seconds`). Report the
  median, so the Pi's speed finally gets measured.
- **Borderline passes:** `root_cause_named` is a keyword check. Read `report.md` before you count a borderline
  pass.

## Improving the agent
Improve the agent through the harness only: the core tool set (`CORE_TOOLS`), `OPERATING_RULES`,
`--num-ctx` and the limits. Change one thing at a time, rerun the same scenarios, and log each variant
against the baseline in RESULTS.md. Before you offer a tool outside the core set, check whether its output
shows records from before the scenario start. `agent.Blind` filters only the tools it knows (README, Blind
mode).

## Gotchas
- **Blind runs:** only `evaluate.py` runs blind. `agent.py run` and `watch` can see earlier runs' fixes, so
  every reported number comes from `evaluate.py`.
- **Scenarios:** 04 is refused because it wedges the probe's serial bridge. 03 and 06 use operator
  injection, and the agent can see its traces (README, Limits).
- **Dirty bench:** `bench not clean after reset ... link_silent` means replug the board's USB (HARNESS.md §3).
