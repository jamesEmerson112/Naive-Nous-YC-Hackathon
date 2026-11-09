# hermes-manager: the healer's manager, as a Hermes profile on James's Mac

The worker next to the arm (`armsim/worker.py`) sends one `arm-escalation/v1` packet per incident to a Hermes
profile called `manager`. The profile answers with one `arm-fixplan/v1` JSON. Plain code checks the answer
and runs the one action. The design is `docs/worker-manager-healer.md` §1-2. `H:L<n>` below is a line in
`docs/raw/hermes-llms-full.txt`; *(inferred)* marks what the docs don't confirm.

| File | What it is |
|---|---|
| `setup.sh a\|b <mac-ip>` | Creates the profile (`--clone`), gives a fresh clone a blank memory and its own `SOUL.md`, installs the skill, writes a fresh `API_SERVER_KEY` and `manager.key.env` for the worker, and applies `config.manager.yaml` with `hermes -p manager config set` |
| `config.manager.yaml` | The manager's config keys; `setup.sh` applies them (`config_pairs.py` reads the file) |
| `skills/arm-incident-manager/SKILL.md` | The skill: a triage parent, 1-3 `delegate_task` analysts with `output_schema`, one critic, one `arm-fixplan/v1` answer |
| `check.sh` | The no-model checks: `/health` (unprefixed if the `/p/manager` one doesn't answer), a wrong key gets 401, `/v1/capabilities`, `/v1/toolsets` passes the worker's own rule (it loads `../armsim/manager.py`, so run it from the arm-sim checkout), the skill is listed. Needs `curl` and `python3` |
| `ask.sh <packet>` | One escalation by hand (the §2.3 curl, with the worker's own POST body), with its latency and whether the output is one JSON object. Needs `curl` and `python3`, not `jq` |
| `samples/esc-*.json` | Real packets from the sim (story b's `us_stuck` and `us_frozen`, image 07's `us_offset`, the stall's `joint_stall`, story redo's `grasp_missed`) |
| `arm-escalation.v1.schema.json`, `arm-fixplan.v1.schema.json` | The two schemas (`python -m armsim.escalation schemas hermes-manager` regenerates them) |

## Set it up (about 20 min)
1. **Pick one branch.** Run `hermes gateway status`.
   - **(a)** A default gateway is already running. Multiplexing is on by default (H:L102402), so the shared listener serves `/p/manager/...` (H:L39846-39848).
   - **(b)** No gateway is running, or the boot preflight declines to multiplex (H:L102402-102406).
   - Never run both: both bind 8642 *(inferred)*.
2. **Run the script** from this folder. `<mac-ip>` is the address the arm host reaches: `127.0.0.1` for sim on this Mac, the Mac's LAN address for the Pi.
   ```bash
   ./setup.sh a 127.0.0.1        # or: ./setup.sh b 127.0.0.1
   ```
   It runs `hermes profile create manager --clone --description "..."`. `--clone` copies the default profile's config, `.env`, `SOUL.md`, skills, plugins, and the memory files `memories/MEMORY.md` and `memories/USER.md` (H:L9028). OAuth logins aren't copied: the profile keeps reading the root `auth.json`, so the ChatGPT-plan login carries over (H:L9049-9050).
   - **Inherited memory and personality.** The default profile is the arm commander's, so its `SOUL.md` and memories would ride into every manager run. For a profile it creates, the script moves `~/.hermes/profiles/manager/memories/MEMORY.md` and `memories/USER.md` aside (to `*.cloned-from-default`) and writes the manager's own `SOUL.md` (the cloned one becomes `SOUL.md.cloned-from-default`). "For a blank memory ... delete the two files afterwards; the agent never falls back to another profile's memory when they are absent" (H:L9028). A profile that already existed is left as it is: clear it by hand with `cd ~/.hermes/profiles/manager && mv memories/MEMORY.md memories/MEMORY.md.old && mv memories/USER.md memories/USER.md.old`, and edit `SOUL.md` (H:L9028).
   - Because `.env` is copied, the script drops any `API_SERVER_*` lines the clone brought and writes a fresh key. A named prefix needs its own key, and the default's key is refused there (H:L39844-39860).
   - Branch (b) also writes `API_SERVER_ENABLED=true`, `API_SERVER_HOST=<mac-ip>`, `API_SERVER_PORT=8642` (H:L39877-39884).
3. **The config.** The script applies `config.manager.yaml` one key at a time with `hermes -p manager config set <dotted.key> <value>` (`-p` selects the profile, H:L54590-54598; dotted settings go to `config.yaml`, H:L3858-3872; the toolset list goes as an inline JSON literal, H:L7581-7585). An existing key's value is replaced, and the cloned `model:` block is kept. If a `config set` fails (an older `hermes`), the script stops: merge the file into `~/.hermes/profiles/manager/config.yaml` by hand, changing a key's value where the cloned file already has it, not adding a second copy.
   - **The toolsets.** The API server's default keeps every `hermes-cli` toolset but clarify, tts, computer_use and kanban (H:L56075-56077), so `agent.disabled_toolsets` names every other default one (vision, image_gen, todo, cronjob and session_search too, not only terminal, file, browser, web and code_execution), plus `so101_arm` and `mcp-naive_board`: enabled plugin toolsets are added even to an explicit list (H:L38901-38903). `platform_toolsets.api_server: [delegation, memory, skills, no_mcp]` writes the API server's list out, the way Blank Slate does for the CLI so `hermes update` can't add to it (H:L396), and `no_mcp` keeps the default profile's MCP servers out *(inferred: that the api_server list reads it as ACP's does, H:L38630-38632)*. Never put a composite (`coding`, `safe`, `debugging`) in `disabled_toolsets`: removal is per tool and would strip `delegate_task`, `memory` or the skill tools (H:L4844-4845).
   - **Why `skills` is on.** The manager reads its own skill with `skill_view` (H:L12660-12664), and the `skills` toolset brings `skill_manage` with it (H:L56060). So `skills.write_approval: true` stages every skill write for a person (H:L13153-13170; *inferred: that this covers API-server runs*), and the script locks the skill (`hermes -p manager curator lock arm-incident-manager`, H:L13186-13190; a warning, not a stop, if that fails).
   - **The worker checks it in code.** `--manager hermes` reads the manager's `GET /v1/toolsets` (H:L39794-39809) before its first ask, and again before every ask, with one allowlist (`armsim/manager.py` `toolset_problems`): `delegation` must be on, `memory` and `skills` may be, each with only its own tools (`delegate_task`; `memory`; `skill_view`, `skills_list`, `skill_manage`), and a toolset counts as on unless `enabled` is `false`. Anything else on, or a `/v1/toolsets` it can't read (a wrong key, an old server), and it won't start (or, later, won't ask: the scripted heal runs). So a skipped or failed config step can't give the manager the arm: the API server "gives full access to hermes-agent's toolset, including terminal commands" (H:L39870), and the cloned `so101_arm` plugin's socket reaches the arm host in sim. The real names on your install may differ from the docs' table (the docs' own example lists a `core` toolset, H:L39805): `./check.sh` prints what is on, and is the first real confirmation.
   - **One change from the design's §2.1: `delegation.gateway_mode: inline`.** Under the default `auto`, "the API server ... keep[s] background delegation" (H:L19782-19786). A background delegation ends the parent's turn with a one-line status (H:L19771-19773), so the run's `output` would be that status, not the fix plan. `inline` makes "every gateway session" join its children inside the turn (H:L19787). *(inferred: that runs on the API server count as gateway sessions.)*
   - Also added: `web` in `agent.disabled_toolsets` *(inferred: the analysts only read the packet)*.
   - Check: `hermes -p manager config show | grep -E 'gateway_mode|disabled_toolsets|max_concurrent'`.
4. **Start or restart the gateway.**
   - (a) The default profile's `.env` must hold `API_SERVER_ENABLED=true`, `API_SERVER_HOST=<mac-ip>`, `API_SERVER_PORT=8642` *(inferred: the listener is shared)*. Then `hermes gateway restart`. URL: `http://<mac-ip>:8642/p/manager`.
   - (b) `hermes -p manager gateway run` in its own terminal (H:L995, H:L4211). URL: `http://<mac-ip>:8642`.
5. **Check it, no model call:** `./check.sh` (from this folder of the arm-sim checkout). It wants `/health` ok (H:L39505), 401 for a wrong key, `run_submission` in `/v1/capabilities` (H:L39406), `/v1/toolsets` (H:L39796-39809) passing the worker's own rule (step 3: it loads `../armsim/manager.py` by path and prints what is on and anything not allowed, so check.sh and `--manager hermes` never disagree), and `arm-incident-manager` in `/v1/skills`. If it names a toolset that isn't allowed, add it to `agent.disabled_toolsets` (`hermes -p manager config set agent.disabled_toolsets '[...]'`, the whole list), restart the gateway, and run it again.
   - With branch (a), `/health` is probed under the `/p/manager` prefix first *(inferred: the docs show it unprefixed)*; if that doesn't answer 200, the script probes the listener's own `http://<mac-ip>:8642/health`.
6. **One real run on a saved packet** (the ChatGPT plan, no API key). **Confirm its output is one JSON object**, not a status line:
   ```bash
   ./ask.sh samples/esc-us_stuck.json                     # the §2.3 curl; prints status, output, latency, and
                                                          # "output is one JSON object" (or NOT)
   cd .. && set -a && . hermes-manager/manager.key.env && set +a
   PYTHONPATH=. .venv/bin/python -m armsim.manager ask hermes-manager/samples/esc-us_stuck.json
   ```
   The second command is the worker's own client. It prints the run's latency and whether `check_plan` accepts the answer (exit 0), refuses it (2) or got none (1). Exit 0 means the output was one JSON plan. Write the latency down. The design guessed 20-60 s per incident *(inferred)*; if it goes past 90 s, drop the critic (the skill already says to skip it past ~60 s).
   - Both send `"conversation_history": []` with the run, as the worker does: caller-supplied history makes delegation run synchronously inside the turn (H:L39622-39628) *(inferred: that an empty list counts)*. If the output is still a status line, see below.
   - Try all four samples. The answers to expect: `restart_sensor` for `us_stuck` / `us_frozen`, `pod_rollback` for `us_offset` (podd flagged `us_mismatch`), `verify` or `handoff_human` for `joint_stall`.

## Then the loop, in sim on this Mac
```bash
cd prototypes/arm-sim
set -a && . hermes-manager/manager.key.env && set +a     # ARMSIM_MANAGER_URL, ARMSIM_MANAGER_KEY
demo/up.sh                                               # the arm host with the virtual pod
PYTHONPATH=. .venv/bin/python -c "from armsim.client import call; call('inject', fault='us_frozen')"
PYTHONPATH=. .venv/bin/python -m armsim.orders add pick
PYTHONPATH=. .venv/bin/python -m armsim.dispatch --once --manager hermes        # the scripted commander
```
- The event log (`runs/events.jsonl`, and Mission Control's feed) shows `incident`, then `escalation` (src worker), `fix_plan` (src manager), `plan_check`, `plan_action`, and `heal`.
- The board gets one `INC ...` card per escalation with `ESCALATION:`, `PLAN:`, then `RESULT:` or `BLOCKED:`; the job card gets a `PLAN:` line.
- Rehearse without Hermes first: `--manager fake` (and `--fake-mode off_list|bad_json|slow|unsafe|500|refused|park|trickle|errstr`).
- A manager `park` is sticky: the incident is handed to a person first, then the arm parks, so the dispatcher waits for them before the next order. The host never moves the arm on its own for a handed-off incident (not even when the pod's healer fixes the pod), and an incident that closed first is held for the person anyway. A person's check at home (Mission Control's "a person fixed it") gives it back.
- A person who takes the incident while the manager thinks keeps it: nothing runs, not the plan and not the scripted heal.
- A `pod_rollback` plan is carried out as a wait for the pod's own healer (plain code can't flash the pod), and the events and cards say `wait_for_pod`.
- The worker's model on the laptop: add `--worker-lm ollama --ollama http://192.168.50.1:11434/v1 --worker-model qwen3.5:4b`.
- `--manager` works only with `--exec scripted`. `stories b --hermes` still runs the Hermes commander with its own healing rules and skips the manager (design §1).

## If the run answers "working on it"
That's background delegation (see step 3). The worker refuses the answer and falls back to the scripted heal, so the arm is safe. Two things keep it inline, and the second is already done:
- `delegation.gateway_mode: inline` (setup.sh applies it): confirm it took (`hermes -p manager config show`), then restart the gateway;
- caller-supplied history: "Runs requests with `previous_response_id` or caller-supplied history instead execute delegation synchronously" (H:L39622-39628). `armsim/manager.py` and `ask.sh` send `"conversation_history": []` with every run *(inferred: that an empty list counts; if a real run shows it doesn't, send the packet as one user turn in the history instead)*.

## The worker's client, for the record
- Each HTTP call is bounded by what is left of the deadline on the wall clock (a thread joined for that long), so a server that trickles bytes can't hold the arm parked past `--manager-deadline` (3-120 s, checked at startup).
- A run it gives up on while it may still be going is stopped, best effort: `POST /v1/runs/{run_id}/stop` (H:L39698-39703) with a 2 s budget, so it doesn't keep one of the two `max_concurrent_runs` slots and 429 the next incident.

## Not done here
- Nothing above was run against a real Hermes: this container has no `hermes` and no ChatGPT login. `check.sh`, `ask.sh` and `setup.sh` were run against `tests/fake_manager.py` and a stand-in `hermes` script only, so `hermes -p manager config set` with these keys (`gateway.api_server.max_concurrent_runs` especially) is untested on a real install *(inferred: H:L3872 says unknown paths under a known section are still written, with a notice)*.
- Path B (a Naïve `pi` team returning the same plan, design §2.4) is not built.
