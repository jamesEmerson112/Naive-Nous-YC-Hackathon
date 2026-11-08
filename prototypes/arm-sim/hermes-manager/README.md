# hermes-manager: the healer's manager, as a Hermes profile on James's Mac

The worker next to the arm (`armsim/worker.py`) sends one `arm-escalation/v1` packet per incident to a Hermes
profile called `manager`. The profile answers with one `arm-fixplan/v1` JSON. Plain code checks the answer
and runs the one action. The design is `docs/worker-manager-healer.md` §1-2. `H:L<n>` below is a line in
`docs/raw/hermes-llms-full.txt`; *(inferred)* marks what the docs don't confirm.

| File | What it is |
|---|---|
| `setup.sh a\|b <mac-ip>` | Creates the profile (`--clone`), installs the skill, writes a fresh `API_SERVER_KEY`, and writes `manager.key.env` for the worker |
| `config.manager.yaml` | The keys to merge into `~/.hermes/profiles/manager/config.yaml` by hand |
| `skills/arm-incident-manager/SKILL.md` | The skill: a triage parent, 1-3 `delegate_task` analysts with `output_schema`, one critic, one `arm-fixplan/v1` answer |
| `check.sh` | The no-model checks: `/health`, a wrong key gets 401, `/v1/capabilities`, `/v1/toolsets` has no terminal, file or `so101_arm`, the skill is listed |
| `ask.sh <packet>` | One escalation by hand (the §2.3 curl), with its latency |
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
   It runs `hermes profile create manager --clone --description "..."`. `--clone` copies the default profile's config, `.env`, skills and plugins (H:L9028). OAuth logins aren't copied: the profile keeps reading the root `auth.json`, so the ChatGPT-plan login carries over (H:L9049-9050).
   - Because `.env` is copied, the script drops any `API_SERVER_*` lines the clone brought and writes a fresh key. A named prefix needs its own key, and the default's key is refused there (H:L39844-39860).
   - Branch (b) also writes `API_SERVER_ENABLED=true`, `API_SERVER_HOST=<mac-ip>`, `API_SERVER_PORT=8642` (H:L39877-39884).
3. **Merge `config.manager.yaml`** into `~/.hermes/profiles/manager/config.yaml`. Where the cloned file already has a key, change its value; don't add a second copy. Keep the cloned `model:` block.
   - **One change from the design's §2.1: `delegation.gateway_mode: inline`.** Under the default `auto`, "the API server ... keep[s] background delegation" (H:L19782-19786). A background delegation ends the parent's turn with a one-line status (H:L19771-19773), so the run's `output` would be that status, not the fix plan. `inline` makes "every gateway session" join its children inside the turn (H:L19787). *(inferred: that runs on the API server count as gateway sessions.)*
   - Also added: `web` in `agent.disabled_toolsets` *(inferred: the analysts only read the packet)*.
   - Check: `hermes -p manager config show | grep -E 'gateway_mode|disabled_toolsets|max_concurrent'`.
4. **Start or restart the gateway.**
   - (a) The default profile's `.env` must hold `API_SERVER_ENABLED=true`, `API_SERVER_HOST=<mac-ip>`, `API_SERVER_PORT=8642` *(inferred: the listener is shared)*. Then `hermes gateway restart`. URL: `http://<mac-ip>:8642/p/manager`.
   - (b) `hermes -p manager gateway run` in its own terminal (H:L995, H:L4211). URL: `http://<mac-ip>:8642`.
5. **Check it, no model call:** `./check.sh`. It wants `/health` ok (H:L39505), 401 for a wrong key, `run_submission` in `/v1/capabilities` (H:L39406), no terminal, file, browser, `so101_arm` or `mcp-naive_board` in `/v1/toolsets` (H:L39796-39809), and `arm-incident-manager` in `/v1/skills`.
   - With branch (a), `/health` is probed under the `/p/manager` prefix *(inferred: the docs show it unprefixed)*. If that 404s, `curl http://<mac-ip>:8642/health` checks the listener itself.
6. **One real run on a saved packet** (the ChatGPT plan, no API key):
   ```bash
   ./ask.sh samples/esc-us_stuck.json                     # the §2.3 curl; prints status, output, latency
   cd .. && set -a && . hermes-manager/manager.key.env && set +a
   PYTHONPATH=. .venv/bin/python -m armsim.manager ask hermes-manager/samples/esc-us_stuck.json
   ```
   The second command is the worker's own client. It prints the run's latency and whether `check_plan` accepts the answer (exit 0), refuses it (2) or got none (1). Write the latency down. The design guessed 20-60 s per incident *(inferred)*; if it goes past 90 s, drop the critic (the skill already says to skip it past ~60 s).
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
- Rehearse without Hermes first: `--manager fake` (and `--fake-mode off_list|bad_json|slow|unsafe|500|refused`).
- The worker's model on the laptop: add `--worker-lm ollama --ollama http://192.168.50.1:11434/v1 --worker-model qwen3.5:4b`.
- `--manager` works only with `--exec scripted`. `stories b --hermes` still runs the Hermes commander with its own healing rules and skips the manager (design §1).

## If the run answers "working on it"
That's background delegation (see step 3). The worker refuses the answer and falls back to the scripted heal, so the arm is safe. Fix it one of two ways:
- confirm `delegation.gateway_mode: inline` took (`hermes -p manager config show`), then restart the gateway;
- or send caller-supplied history: "Runs requests with `previous_response_id` or caller-supplied history instead execute delegation synchronously" (H:L39622-39627). That means adding `"conversation_history": []` to the POST body in `armsim/manager.py` *(inferred: that an empty list counts)*.

## Not done here
- Nothing above was run against a real Hermes: this container has no `hermes` and no ChatGPT login. `check.sh`, `ask.sh` and `setup.sh` were run against `tests/fake_manager.py` and a stand-in `hermes` script only.
- Path B (a Naïve `pi` team returning the same plan, design §2.4) is not built.
