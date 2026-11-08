# bench: the healer benchmark

It measures a healer (a small local model in an agent harness) on planted faults, using the fake
hardware in `device/sim`, on a Mac. Each task copies the robot app, plants a fault, commits it as the
deployed version, lets the supervisor detect the incident, runs the healer, runs a hidden check, and
rolls back to the deployed version if the check fails.

Results so far and how we picked the model: [`docs/healer-benchmark-progress.md`](../docs/healer-benchmark-progress.md).

## Files

| Path | What it is |
|---|---|
| `tasks.py` | The 6 planted faults, the reference fix (`oracle`) and the hidden check (`verify`) |
| `run_bench.py` | The runner. It takes the app from `../device/robot`, `../device/sim` and `../device/supervisor`, and a harness bundle from `../harness/hN` |
| `agents.py` | Builds the agent's task from a bundle (the incident, SPEC.md, the matching guides, the code) and runs mini-swe-agent on a local model through Ollama |
| `analyze.py` | Tables per config: passes, failure categories, time and tokens |
| `runs/` | One folder per run (gitignored) |

## Run it

From the repo root:

```bash
uv venv bench/.venv --python 3.11
uv pip install --python bench/.venv/bin/python mini-swe-agent mcp   # litellm 1.108 imports mcp on this path
bench/.venv/bin/python bench/run_bench.py --agent oracle   # validates the tasks: 6/6 should pass
bench/.venv/bin/python bench/run_bench.py --agent none     # baseline: 0/6
```

To run a real agent, start Ollama with a context window big enough for an agent loop (its default is
smaller), then pick a model, a harness version and tasks:

```bash
OLLAMA_CONTEXT_LENGTH=16384 ollama serve
bench/.venv/bin/python bench/run_bench.py --agent mini --model qwen3.5:4b --tasks S1,D2
```

`--harness DIR` runs any bundle (default `harness/h3`). `--no-skills` leaves out its skills, to measure
what they add, and `--no-think` turns a thinking model's thinking off (Ollama `think=false`). Each task's
work area (`bench/runs/<run>/<task>/`) keeps a copy of the bundle, `incident.json`, `trajectory.json`
(every model message, with token counts), `agent.diff` and `outbox/report.json`. To summarize runs,
pooling runs of the same config:

```bash
python3 bench/analyze.py bench/runs/<run> [<run> ...]
```

## The work area a healer sees

The runner builds the same layout for every task:

```
device/      the robot app (from device/robot): the agent's working directory, a git repo whose HEAD is the deployed version
harness/     the bundle: harness.json, prompt.md, skills/, tools/
supervisor/  monitor.py, which the health tool uses
sim/         fake hardware; the health tool replays the recorded scenario on it
incident.json, scenario.json, outbox/ (the report for the cloud goes here)
```

## The tasks

| Id | Planted fault | Scenario | Detected as |
|---|---|---|---|
| D1 | A "safety" rule in `control.py` opens the gripper when something is close (demo iteration 1) | hand_close | tier 3 spec violation |
| D2 | The distance validity check is removed | blind_zone | tier 1 crash (None reaches the math) |
| D3 | Servo reads no longer go through `with_retries` | normal + 3 failed bus reads | tier 1 crash |
| S1 | Config key typo: `near_cn` | normal | tier 1 crash (KeyError) |
| S2 | `read_distance` waits forever for an echo | sensor goes dead after 30 readings | tier 2 hang |
| A5 | Gripper servo (ID 6) missing: hardware, so the right move is park and report, with no code change | normal | tier 1 crash |

The hidden check never trusts the healer's own tools. For a software task, it reruns the healed code on a
fresh copy of the canonical sim and requires every arm command to match the canonical app on the incident
scenario and on the normal and hand_close regressions. It also fails the task if the healer edited `sim/`.
For A5 it requires `sim/arm_state.json` to show parked with torque off, no change to the code, and an
unedited sim. `passed` also requires the right report (`cause` and `action` in `outbox/report.json`). A run
that fails is rolled back to the deployed commit.

These exact-match checks are for the benchmark only. `device/tests/` checks SPEC.md's properties instead,
so a legitimate patch still passes it.

## Harness versions (`harness/`)

A bundle is the thing the cloud ships and improves: `harness.json`, `prompt.md`, `skills/` (short guides,
one per fault class) and `tools/` (`health.py` replays the incident; `park.py`; `report.py`; from h2 on,
`edit.py`). Each version runs as is with `--harness`.

| Version | What it adds |
|---|---|
| h1 | The prompt lists the skills by path, and the model has to read them itself. Edits are free-form (sed); the run ends when the model echoes mini-swe-agent's done marker. The tools don't check anything. |
| h2 | `edit.py`: exact-text replace that refuses an edit that misses or would break the file. `report.py` becomes a gate: "fixed" is accepted only when health.py passes, "parked" only after park.py, and only an accepted report ends the run. SPEC.md and the guides that match the incident go into the prompt. |
| h3 | "Hardware" or "park" is accepted only when the incident shows a servo or bus error (`hardware_evidence` in harness.json). The deployed code (`show_files`) goes into the prompt. `edit.py` also takes a SEARCH/REPLACE block in a heredoc, so code with quotes doesn't fight the shell. |
