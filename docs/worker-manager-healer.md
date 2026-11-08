# Worker/manager healer: the design for tonight (Sun Nov 8, ~3 AM PST)

**How to read the citations:** repo citations are `path:line` at `e7b4062`. `H:L<n>` is a line in `docs/raw/hermes-llms-full.txt` and `N:<n>` a line in `docs/raw/naive-llms-full.txt`. Model facts link the vendor card. *(inferred)* marks my own conclusions. I edited no repo files.

## The decisions, on one screen
1. **Plain code watches every tick, not the language model.**
   - The level-1 rules do the per-tick checks (`armsim/level1.py`, `firmware/healer/healer/detect.py`). The L3a classifier adds a second opinion that can only raise a warning (`docs/healer-data-levels.md:70-93`).
   - The small model wakes only when one of them finds something.
   - **Why this departs from "constantly monitor the log":** on a Pi 5, qwen3.5:4b reads prompts at 3.6 tok/s ([bestllmfor](https://bestllmfor.com/guide/llm-local-raspberry-pi-edge/)), so even a 400-token check takes about 2 minutes *(inferred)*.
2. **The worker is plain code plus the small model, and plain code has the final say.**
   - The worker runs next to the arm host. The model runs on the laptop's Ollama.
   - The model writes the escalation. Plain code checks the plan and carries out the one checked action.
   - `--exec-via lm` puts the model back as the one who "initiates the fix" (step 8).
   - `verify()` decides whether a fix worked (`prototypes/arm-sim/armsim/host.py:340-380`).
3. **The manager is one Hermes profile, `manager`, on the Mac that runs Hermes** (v0.23.0, `docs/pi-vs-hermes.md:11`).
   - The worker reaches it at `POST /v1/runs`, which returns a status and an output (H:L39524-39560).
   - Inside one run, a triage parent spreads the work across 1–3 `delegate_task` analysts, then one critic. Each child must answer in a set `output_schema` (H:L19187, H:L19199-19201).
   - Every agent uses gpt-6-sol through the ChatGPT plan (`docs/pi-vs-hermes.md:12, 16`) and has **no tools**.
4. **Tonight Naïve keeps the record and supplies the model, but does not host the manager.**
   - Hosted Hermes reported `runnable: false` (`docs/naive/company-setup.md:36`). Even when it does run, Hermes on Naïve gets no typed output (N:21543).
   - **The record:** plain code writes one incident card per escalation through `armsim/board.py` (`NaiveBoard.add`/`comment`, `board.py:196, 218`).
   - **The model:** `openai/gpt-6-sol` is a valid Naïve model id (`docs/pi-vs-hermes.md:12`). That answers the Naïve report's open question about "ChatGPT 6 sol".
   - **Stretch:** a Naïve `pi` team that returns the same fix plan (§2.4).
5. **The worker does not go through Romulus `doctor/ask`.**
   - `DoctorAdvice` is `{ask_id, hypotheses:[{claim, how_to_check}], model}`. It has no field for an action (`packages/contracts/src/romulus_contracts/doctor.py:194-208`).
   - Romulus still gets the outcome in the incident report it already receives (`prototypes/arm-sim/README.md:35`).
   - Extending that contract is Kent's lane.
6. **The arm is parked before anyone is asked.**
   - A finding sets `halt_reason` as soon as the incident opens (`host.py:307-321`).
   - If the manager is unreachable, slow, or sends a plan that fails the check, today's scripted heal runs (`armsim/dispatch.py:241`).
   - If that fails too, the arm stays parked and the incident goes to a person.

## 1. The worker/manager architecture

```
 ARM HOST MACHINE (Pi 5 on the real arm; James's Mac in sim)        MAC WITH HERMES v0.23.0
 ┌──────────────────────────────────────────────────────────┐       ┌────────────────────────────────────┐
 │ arm host + Watchdog: level-1 rules ──► PARK (immediate)  │       │ Hermes gateway, profile `manager`  │
 │ podd + detect.py: pod level 1 ──────► PARK               │       │ API server :8642                   │
 │ L3a audit: warn-only 2nd opinion                         │       │  triage parent (gpt-6-sol, 0 tools)│
 │        │ incident                                        │ POST  │   ├ delegate_task diag-arm   ┐     │
 │        ▼                                                 │/v1/runs   ├ delegate_task diag-pod   ├ output_schema
 │ armsim/worker.py (plain code, NEW)                       │──────►│   ├ delegate_task diag-task  ┘     │
 │  1 build arm-escalation/v1 ───► laptop Ollama: LM writes │       │   └ critic                         │
 │    the six-key answer (≤30 s)                            │ GET   │  → ONE arm-fixplan/v1 JSON         │
 │  2 POST, poll every 2 s, ≤90 s ◄─────────────────────────│◄──────│                                    │
 │  3 check_plan(): schema, allowlist, args, budgets        │       └────────────────────────────────────┘
 │  4 run that one action (code; LM with --exec-via lm)     │
 │  5 verify() decides; two fails → hand off to a person    │
 │  fallback: dispatch.py _heal (scripted) ► park + handoff │
 └──────┬──────────────────────────────┬────────────────────┘
        │ incident report              │ incident card: PLAN:/RESULT:
        ▼                              ▼
  Romulus (laptop, server/cloud_up.sh)   Naïve board (api.vetta.sh)

 LAPTOP 192.168.50.1 (Ethernet to Pi 192.168.50.2): Ollama qwen3.5:4b via server/serve.py, Romulus
 RUNPOD: the benchmark only (never in the loop)
 POD WORKER (optional, Olir's lane): firmware/harness/agent.py watch + an ask_manager tool (risk notify)
```

### What runs where, and the scope
- **The worker runs on the arm host's machine.**
  - The host speaks a Unix socket (`prototypes/arm-sim/hermes-plugin/so101-arm/__init__.py:15, 34-36`), so the worker has to sit beside it.
  - That means the Pi on the real arm (`prototypes/arm-sim/README.md:45-48`) and the Mac in sim.
- **The laptop runs the model and Romulus.**
  - Ollama serves qwen3.5:4b through the Ethernet-only relay (`server/README.md:3-15`).
  - Measured there: median turn 3.4 s, p90 10.2 s (`firmware/harness/RESULTS.md:67-68`).
  - Romulus runs there through `server/cloud_up.sh` (team status in CLAUDE.md).
- **Why the manager is called over `/v1/runs`, not `chat --oneshot`:**
  - A one-shot run may spawn only 2 children in total, while "gateway sessions are never capped by this" (H:L7006).
  - `API_SERVER_HOST` defaults to 127.0.0.1 (H:L39870-39884), so it must be set to an address the Pi can reach (open question 2).
- **Nothing from Naïve sits in the loop.**
  - "A sandbox reaches out; nothing reaches in" (N:20674).
  - Naïve's webhooks go only to public HTTPS addresses (N:14548).
- **What tonight's hook covers:**
  - The worker plugs into `ScriptedExecutor._heal` only.
  - `stories b --hermes` (`--exec hermes`) still runs the Hermes commander with `so101_arm` and its own healing rules (`dispatch.py:68`). It skips the manager, so don't demo it expecting one.
  - An incident on an idle arm parks and waits for the next job's `_clear_between_steps`, as it does today (`dispatch.py:220-233`).

### The escalation: worker → manager (`arm-escalation/v1`)
```json
{"$id":"arm-escalation/v1","type":"object","additionalProperties":false,
 "required":["schema","incident_id","device_id","category","opened_at","findings","worker","window","state","allowed_actions","deadline_s"],
 "properties":{
  "schema":{"const":"arm-escalation/v1"},
  "incident_id":{"type":"string","pattern":"^inc-[A-Za-z0-9-]{4,64}$"},
  "device_id":{"type":"string"},
  "category":{"enum":["fault","wrong_action"]},
  "opened_at":{"type":"string","format":"date-time"},
  "findings":{"type":"array","minItems":1,"maxItems":8,"items":{"type":"object","additionalProperties":false,
    "required":["src","kind","severity","summary"],
    "properties":{"src":{"enum":["watchdog","podd","l3a","dispatcher"]},"kind":{"type":"string","maxLength":40},
                  "severity":{"enum":["info","warn","critical"]},"summary":{"type":"string","maxLength":300}}}},
  "worker":{"type":["object","null"]},
  "window":{"type":"string","maxLength":12000},
  "state":{"type":"object","required":["mode","kinematics","parked","tried"],
    "properties":{"mode":{"enum":["sim","real"]},"kinematics":{"type":"boolean"},"parked":{"type":"boolean"},
      "pod_image":{"type":["string","null"]},
      "job":{"type":["object","null"],"properties":{"card_id":{"type":"string"},
             "steps":{"type":"array","items":{"type":"string"}},"failed_step":{"type":"string"}}},
      "tried":{"type":"array","items":{"type":"object","required":["action","verified"],
               "properties":{"action":{"type":"string"},"verified":{"type":"boolean"}}}}}},
  "allowed_actions":{"type":"array","minItems":1,"uniqueItems":true,
    "items":{"enum":["restart_sensor","verify","pod_rollback","redo_step","park","handoff_human","no_action"]}},
  "deadline_s":{"type":"number","minimum":3,"maximum":120}}}
```
- **`worker`** holds the model's six-key answer from §4. It is `null` if the model ran past its 30 s cap.
- **`window`** holds the rendered lines (§4, lite profile, about 1k tokens *(inferred)*).
  - The healthy facts sit in the system prompt. That applies the level-2 lesson: a frozen trace is caught only beside a healthy one (`docs/healer-data-levels.md:44-53`).
- **`deadline_s`** uses `DoctorAsk`'s 3–120 s bounds (`doctor.py:123-141`).

### The fix plan: manager → worker (`arm-fixplan/v1`)
```json
{"$id":"arm-fixplan/v1","type":"object","additionalProperties":false,
 "required":["schema","incident_id","action","args","kind","confidence","touches_safety","why"],
 "properties":{
  "schema":{"const":"arm-fixplan/v1"},
  "incident_id":{"type":"string"},
  "action":{"enum":["restart_sensor","verify","pod_rollback","redo_step","park","handoff_human","no_action"]},
  "args":{"type":"object","additionalProperties":false,
          "properties":{"from_step":{"enum":["reach","approach","grip","lift","release"]}}},
  "kind":{"type":"string","maxLength":40},
  "confidence":{"type":"number","minimum":0,"maximum":1},
  "touches_safety":{"type":"boolean"},
  "why":{"type":"string","maxLength":400},
  "expect":{"type":"string","maxLength":200}}}
```
- The plan can carry no angles and no free-form commands. Only `redo_step` takes an argument, and that argument is a skill name (the skill names are at `so101-arm/__init__.py:17-27`).

### The allowlist (plain code computes it per incident and sends it in the packet)
| Incident | `allowed_actions` | What runs it |
|---|---|---|
| `us_*` sensor kinds | restart_sensor, verify, park, handoff_human | host op `{"op":"repair","action":"restart_sensor"}`: `us off`, then `us on` through podd, then verify (`host.py:10, 322-338`) |
| `pod_*` or a critical podd incident | pod_rollback, verify, park, handoff_human | on the bench, `heal fw_rollback` (`firmware/healer/tools.json:651`), which restored known-good in 19 s (`firmware/harness/HANDOFF.md`); in sim, the `armsim/podheal_sim.py` stand-in (`:1-7`) |
| `joint_stall` | verify, park, handoff_human | `repair verify`; a joint that still won't move goes to a person (`host.py:375`) |
| `wrong_action` (category 2) | redo_step, park, handoff_human | **new:** `skill release`, then `skill go_home`, then the card's steps from `from_step` on, once *(inferred)* |
| every row | also `no_action` | **run as `verify`:** nothing resumes without `verify()` *(inferred)* |

### How plain code checks a plan: `check_plan(plan, esc, state) -> (ok, reason)`
The checks run in this order, and the first failure stops the plan.
1. **Exactly one JSON object** in `output` (one code fence is tolerated), and it passes the schema.
   - Treat the run's text as unchecked. The gaps doc says `stream-json` has no schema-checked final answer (`docs/hermes-harness-gaps.md:49`); assume runs are the same *(inferred)*.
2. **The right incident:** `incident_id` matches the packet, and that incident is still open (host `incidents` op, `host.py:675`).
3. **An allowed action:** `action` is in `esc.allowed_actions`.
4. **Valid arguments:** only `redo_step` takes any. `from_step` must be in `state.job.steps` and at or before `failed_step`.
5. **No safety changes:** a plan with `touches_safety: true` is refused, and the worker parks the arm and hands off. Safety changes go through the Fleet Doctor's simulated canary (K17, Romulus rules in CLAUDE.md).
6. **Within budget:**
   - at most 2 heals per job (`MAX_HEALS = 2`, `dispatch.py:62`);
   - at most 1 `pod_rollback` per incident, inside the pod gate's `FLASH_CAP = 3` (`firmware/harness/agent.py:38`);
   - at most 1 `redo_step` per job, and never the same action again after a failed verify *(inferred)*.
7. **Still current:** the plan arrived before the deadline and the arm is still halted (`host.py:314`).

**If any check fails,** the reason goes into the incident and onto the card, and the fallback runs.

**How the action runs:**
- **Tonight's default:** plain code issues the checked action.
- **With `--exec-via lm` (step 8):** the model gets the plan, with the allowed actions as its only tools. The gate allows only the call that equals `(plan.action, plan.args)`, the way the pod `Gate` refuses anything not on its list (`agent.py:389-418`). If no exact call comes within 15 s, plain code issues it *(inferred)*.

### Timeouts, and what happens when the manager is unreachable
| Stage | Limit | When the limit is hit |
|---|---|---|
| Level-1 park | immediate (`host.py:307-321`) | – |
| The model writes the escalation | 30 s (p90 turn is 10.2 s, `RESULTS.md:67-68`) | send `worker: null` and the level-1 summaries only |
| The manager answers | 90 s in all:<br>• one POST with `Idempotency-Key` = the incident id; an identical retry gets the same `run_id` back (H:L39536)<br>• poll `GET /v1/runs/{id}` every 2 s<br>• on 429, back off (H:L39907) | the scripted `_heal` (`dispatch.py:241`): restart a stuck sensor, wait for the pod healer, or do a checked retry at home. If that fails, stay parked and hand off |
| The model issues the call (`--exec-via lm`) | 15 s | plain code issues it |
| `verify()` | the host's own: go home, then hold 3.5 s on fresh rows (`host.py:340-360`) | after 2 failures, hand off, and Romulus opens a work order (`host.py:375`, `README.md:31`) |

**Why this keeps both rules** *(inferred)*:
- No new network path reaches the arm, and the arm never moves on a missing answer.
- It keeps "no LLM in the real-time control loop" (CLAUDE.md).
- It keeps "Hypotheses only, never a patch: the healer stays in charge" (`doctor.py:201-208`).

## 2. The manager side (Hermes, with Naïve as the record)

**Decision:** one `manager` profile with its tools switched off. Its analysts are `delegate_task` children that return typed answers.

**Why no tools:** the escalation packet already carries the evidence. That sidesteps four problems:
- Children inherit the parent's toolsets, and the model can't narrow them (H:L19519).
- A plugin's toolset is on in every session by default (H:L16354-16357).
- It's unconfirmed whether a webhook route accepts a plugin toolset (H:L35528).
- The dispatcher's commander already holds `so101_arm` (`dispatch.py:68`), so a manager with arm tools could collide with it *(inferred)*.

### 2.1 Setup on the Mac with Hermes
```bash
hermes profile create manager --clone \
  --description "Arm-company incident manager: reads one arm-escalation/v1 packet, answers with one arm-fixplan/v1 JSON."
#   --clone copies the default profile's config (H:L9025). OAuth logins aren't copied; the profile keeps reading
#   root auth.json, so the ChatGPT-plan login carries over (H:L9050)
```
`~/.hermes/profiles/manager/config.yaml` (add these to the cloned file):
```yaml
delegation:
  max_concurrent_children: 3     # set explicitly: the docs say 10 (H:L19187) and 3 (H:L7001)
  max_spawn_depth: 1             # flat: children can't delegate (H:L7004)
  max_iterations: 8              # per child; default 250 (H:L19907, example at H:L19537)
  child_timeout_seconds: 60      # default 0 = none, floor 30 (H:L19552-19553)
  # no base_url/model: children use the parent's gpt-6-sol. This changes the Hermes report, which sent them
  # to the laptop's Ollama. Three parallel calls there would compete with the worker's LM, and bench Hermes
  # is "paid by" the ChatGPT plan (docs/pi-vs-hermes.md:16) (inferred: no per-token cost)
agent:
  disabled_toolsets: [terminal, code_execution, file, browser, so101_arm, mcp-naive_board]  # "remains authoritative" (H:L20395)
approvals: {unattended_mode: deny, single_query_mode: deny}   # the defaults (H:L10764-10783)
gateway:
  api_server: {max_concurrent_runs: 2}   # default 10, then 429 (H:L39899, H:L39907); one arm
kanban: {auto_decompose: false}          # otherwise an extra LLM call every tick (H:L20749)
```
**Starting the gateway.** Check `hermes gateway status` first and pick one branch.
- **(a) A default gateway is already running.** Multiplexing is on by default (H:L102402), and the shared listener serves `/p/manager/...` (H:L39846-39848).
  - Put only `API_SERVER_KEY=<random>` in `~/.hermes/profiles/manager/.env`. Named prefixes need their own key, and the default key now returns 401 (H:L39844-39860).
  - The default profile's `.env` holds `API_SERVER_ENABLED=true`, `API_SERVER_HOST=<Mac IP the Pi can reach>` and `API_SERVER_PORT=8642` (H:L39870-39884) *(inferred: the listener is shared)*.
  - Run `hermes gateway restart` (H:L1271), then use `http://$MAC:8642/p/manager/v1/runs`.
- **(b) No gateway is running, or the boot preflight declines to multiplex** (H:L102402-102406).
  - Put all four variables in the manager's `.env`.
  - Run `hermes -p manager gateway run` (H:L995, H:L4211), then use the unprefixed `/v1/runs`.
- Don't run both branches: both bind 8642 *(inferred)*. `GET /health` (H:L39505) on the chosen path confirms the branch.

### 2.2 The skill: `~/.hermes/profiles/manager/skills/arm-incident-manager/SKILL.md`
The skill tells the manager to do four things, in order:
1. **Pick 1–3 analysts by category and findings.**
   - `diag-arm` for `us_*`, `joint_*` and kinematics;
   - `diag-pod` when there are podd findings or a `pod_image`;
   - `diag-task` for `wrong_action`.
2. **Send them all in one `delegate_task(tasks=[…])` call.** Each task gets the packet and `output_schema {hypothesis, kind, evidence[], confidence, action ∈ allowed_actions}` (H:L19199-19201).
3. **Run one critic task.** It gets the packet plus the hypotheses and returns `{approve, action, reason, touches_safety}`.
4. **Answer with ONLY one `arm-fixplan/v1` object.**
   - The action must come from `allowed_actions`. If unsure, answer `park` or `handoff_human`.
   - Never send angles, code or firmware patches; those go to the Fleet Doctor (K17).
   - The worker re-checks the plan and discards anything not on the list.

**Latency:** the triage turn, the parallel children and the critic make about three model rounds in sequence. Hermes's model time for a "hello" on sol was 1.3 s (`docs/pi-vs-hermes.md:26`). So expect 20–60 s per incident *(inferred; measure it)*. If a run goes past 90 s, drop the critic first.

### 2.3 One escalation by hand (what `ManagerClient` does), branch (a) URL
```bash
INC=inc-$(uuidgen)
RUN=$(curl -s http://$MAC:8642/p/manager/v1/runs -H "Authorization: Bearer $MANAGER_KEY" \
  -H "Idempotency-Key: $INC" -H "Content-Type: application/json" \
  -d "{\"input\": $(jq -Rs . < esc.json), \"session_id\": \"$INC\",
       \"instructions\": \"Use skill arm-incident-manager. Reply with ONLY one arm-fixplan/v1 JSON object.\"}" | jq -r .run_id)
curl -s http://$MAC:8642/p/manager/v1/runs/$RUN -H "Authorization: Bearer $MANAGER_KEY" | jq '.status, .output'
```
- **Use one `session_id` per incident, not per device.** A `session_id` loads that session's transcript (H:L39545-39549), so a per-device id would carry every earlier incident along *(inferred)*.

### 2.4 Naïve's part
- **Tonight: one card per escalation.**
  - Plain code writes it through `board.py`: an `INC:` title, then `PLAN:` and `RESULT:` or `BLOCKED:` comments.
  - This is the pattern the dispatcher already uses on ARM JOB cards (`dispatch.py:15-35`).
- **Re-check hosted Hermes (read-only call):** `curl -s https://api.vetta.sh/v1/harnesses -H "authorization: Bearer $KEY" | jq '.data[] | {base, runnable}'`
  - Keep the manager on the bench even if this now says runnable. On the `hermes` harness, tools that need approval are dropped (N:21598), there is no typed output (N:21543), and there are no tool events (N:21661).
- **Stretch: a second manager backend on Naïve ("Path B", same fix-plan schema).**
  - **The team:** a `pi` coordinator, `incident-lead`, with three members: `diagnoser`, `fix-planner` and `safety-reviewer` (N:31668).
  - **Create it:**
    - run `naive agent create -f <role>.agent.yaml` four times;
    - then `naive team set agt_<incident-lead> --member diagnoser@1 --member fix-planner@1 --member safety-reviewer@1 --board` (N:31780-31782, N:19413).
  - **Call it:**
    - `POST /v1/sessions` with `output_schema` and `structured_output_required: true`, about $0.50 a task (N:10135-10200, N:13749);
    - poll `GET /v1/sessions/{id}` and branch on `stop_reason` (N:10133).
  - **Test it:** `naive session run --agent agt_<incident-lead> --output-schema fixplan.json --require-output --json` (N:18740).
  - **It needs:** internet from the arm host, and a scoped token from `naive mcp create` (N:7680).
- **Not tonight: Kanban and webhooks.**
  - Kanban is single-host (H:L21419), its dispatcher ticks every 60 s (H:L20083), and it returns nothing to the caller (H:L20048-20050).
  - Webhooks can't reply over HTTP (H:L35288-35307).
  - Later they fit a slow lane: `hermes kanban swarm` for postmortems and firmware patches, then the Fleet Doctor's canary (H:L21007-21015).

### 2.5 Extra components worth adding *(inferred unless cited)*
- **L3a as a second-opinion monitor.** `level3.py audit` (`armsim/level3.py:177`) opens warn-level incidents for faults the rules pass. This is the L3b design (`docs/healer-data-levels.md:92-93`).
- **A pod worker.** Olir's `agent.py watch` (`agent.py:774-797`) gets an `ask_manager` tool with risk `notify`, which the gate already allows (`agent.py:39`). The harness README already names this seam (`firmware/harness/README.md:24-29`).
- **An offline manager.** Run `lfm2.5:24b` in a local Hermes profile; it was RL-trained inside Hermes Agent ([card](https://huggingface.co/LiquidAI/LFM2.5-24B-A2B)).

## 3. Models to benchmark (5 core, 1 optional)

**Decision: keep qwen3.5:4b until something beats it on our own bench.**
- On the bench it made 0 invalid calls (`firmware/harness/HANDOFF.md:11-13`) and passed 10 of 12 on h3 (`docs/healer-benchmark-progress.md:61`).
- Every challenger has only vendor numbers.
- **Score every model on one GPU pod.** The same quantization should rank about the same on any hardware, though backends aren't bit-identical *(inferred)*.
- **Measure speed where the worker really runs:** the laptop, called from the Pi.

| Model (Ollama tag) | Q4 size | Why it's on the list | Numbers |
|---|---|---|---|
| **qwen3.5:4b** (baseline) | 3.3 GB | Proven on our bench. On a Pi 5 it generates 2.3 tok/s and reads prompts at 3.6 tok/s ([bestllmfor](https://bestllmfor.com/guide/llm-local-raspberry-pi-edge/)) | BFCL-V4 50.3, TAU2 79.9 ([card](https://huggingface.co/Qwen/Qwen3.5-4B)) |
| **lfm3:1.7b** | 1.1 GB | Post-trained for Hermes Agent harnesses. Risks: only 12 days old, and the license allows commercial use only under $10M a year ([LICENSE](https://huggingface.co/LiquidAI/LFM3-1.7B/blob/main/LICENSE)) | BFCL V4 71.39, IFEval 96.66 ([card](https://huggingface.co/LiquidAI/LFM3-1.7B)) |
| **qwen3.5:2b** | 1.9 GB | Same template as the baseline. Pi 5: 4.4 tok/s generation, 6.6 tok/s prompt. The card warns thinking mode can loop | BFCL-V4 43.6 ([card](https://huggingface.co/Qwen/Qwen3.5-2B)) |
| **qwen3.5:9b** | 6.6 GB | The strongest Qwen that fits a laptop GPU or a 16 GB Mac *(inferred)* | BFCL-V4 66.1, TAU2 79.1 ([card](https://huggingface.co/Qwen/Qwen3.5-9B)) |
| **lfm2.5:24b** (24B-A2B) | 15 GB | Only 2.45B parameters active per token. Trained with RL in Hermes Agent; the offline-manager candidate | BFCLv4 65.01; 184 tok/s on an M5 Max ([card](https://huggingface.co/LiquidAI/LFM2.5-24B-A2B)) |
| *d1:2b* (optional, monitor only) | 3.9 GB | Gives "healthy or which fault" as probabilities, with no generation. The models report gives its endpoint as both `/v1/systemone` and `/api/v1/systemone`; check [the page](https://ollama.com/library/d1) before hardcoding it | 18.6 ms per question on an M5 Pro ([card](https://huggingface.co/LiquidAI/d1-2B)) |

- **Don't re-propose** granite4.1:3b (1 of 6) or qwen3:4b-instruct-2507 (3 of 6). Both failed on our bench (`docs/healer-benchmark-progress.md:63-64`).
- **Skip time-series forecasters.**
  - Fed a frozen history, a forecaster predicts a flat line, so the residual is zero and nothing fires. That's why `us_frozen` is plain code (`armsim/level1.py:30`).
  - Offset and scale faults already have the kinematic residual (`level1.py:47`), and L3a's trees use it.
  - ChatTS-14B cost $0.11 and caught a frozen trace only when shown a healthy one beside it (`docs/healer-data-levels.md:40-53`).
- **CPU in this container** (checked: 4 cores, 15 GB RAM, no Ollama installed):
  - Run only a 20-item latency sample of the 2B-class models, and only if Ollama installs through the proxy.
  - At 1–2k-token prompts, 4 cores take tens of seconds per item *(inferred)*.
- **The real speed number:** run `run.py --endpoint http://192.168.50.1:11434/v1 --model <tag>` from the Pi (`firmware/harness/agent.py:10`).
- **GPU on Runpod:** one 48 GB card at ≤ $0.79/h, the same as L2 (`docs/healer-data-levels.md:40`).
  - Fork `level2/runpod_l2.py` (`launch | status | fetch | terminate`, `docs/healer-data-levels.md:38`) into `workerbench/runpod_wb.py`, with an Ollama image and a hard `timeout` of 3 h.
  - On the pod:
  ```bash
  OLLAMA_HOST=0.0.0.0 ollama serve &
  for m in qwen3.5:4b lfm3:1.7b-q4_K_M qwen3.5:2b-q4_K_M qwen3.5:9b lfm2.5:24b; do ollama pull $m; done
  python -m workerbench.run --items 'data/workerbench/*-test.jsonl' --quick --track E --profile lite \
    --endpoint http://127.0.0.1:11434/v1 --model $M --json-mode schema --label $M-nothink
  ```
  - Order: all five on `--quick` with thinking off; then thinking on for the top 3; then the full test for the top 2. Fetch `runs/wb/` and terminate the pod.
  - Before each model, run `server/pi_check.py --model <tag>`, which tests one tool call with thinking on (`server/README.md:22`). Also run `ollama --version`, since the new tags need a recent Ollama.
- **Cost** *(inferred)*:
  - At about 2 s an item, a 250-item `--quick` run takes about 8 minutes per model and mode.
  - That's about 50 min with thinking off, about 25 min with it on, and about 80 min for the two full tests.
  - Total: about 2.5 h, roughly **$2.00**. The 3 h cap is $2.37, inside the ~$5 budget (the doc allows up to $10, `docs/healer-data-levels.md:102`).
  - Time the first 20 items, then cut the plan if needed.

## 4. The benchmark: `prototypes/arm-sim/workerbench/` (standard library plus repo code)

### Decisions *(inferred)*
These settle the data report's open points.
- **The headline is track E:** the window plus the level-1 `INC` lines. Writing the escalation on a finding is the worker's production job in this design. Track D (blind) comes second; it would justify a periodic model sweep.
- **`escalate` is true for every warn or critical fault.** The packet's `state.tried` tells the manager what the host already did.
- **Gold severity is `warn`** for kinds level 1 never emits: joint_offset, joint_reversed and spikes.
- **Category 2 (`grasp_missed`) stays out of tonight's bench.**
- **The L3 baseline is deferred.** `level3.py` trains per run (`armsim/level3.py:101-120`), so a per-window baseline would mean retraining.

### Items
Stored in `data/workerbench/<source>-<split>.jsonl`:
```
{"id","split","source":"sim-factory|synth-pod|vpod-live|real-sample|real-podd|real-arm","kinematics",
 "snapshot":{"now","D","J","A","H","E","link","skills":[{name,t0,t1,result}],"incidents":[{kind,severity,summary,sim_t}]},
 "label":{"status","kind","accept","route","severity","escalate","observable","hard_negative",
          "evidence_rows":{"D":[ts…],"J":[ts…],"H":[…],"E":[…]},"facts":{…},"onset_s","params","label_source"},
 "meta":{"seed","task","variant","run_id"}}
```
- **Gold evidence is stored as row timestamps.** `render()` returns an id→row map, so changing the profile or track never changes a label.

### Where the items come from
- **`gen_arm.py`:** a fork of `factory.one_run` (`armsim/factory.py:38-80`).
  - **Why a fork:** the factory parks after the first finding and drops incident times (`factory.py:69-71, 78`). The fork also captures skill results and each incident's `sim_t` (`host.py:310-311`).
  - **Seeds:** dev uses seed 1 and test uses seed 99, the same runs as v2 and v3-heldout (`factory.py:92`).
  - **Windows** end at onset +1.5, +3 and +5 s, and at the first incident.
  - **Healthy runs** give three hard negatives: holding the block, at home (every row `out_of_range`), and at peak wave speed.
  - **"Latent" items** (a planted fault with nothing visible in the window) are scored separately. For example, 55 of the 122 v2 frozen runs that level 1 missed froze on a `no_echo` row *(computed in the data report)*.
- **`gen_pod.py`:** offline podd snapshots, the way `arm/tests/simpod.py:70-116` makes them.
  - **Variants:** 01, 02-real, 02-sim, 02-early, 03, 04, 05, 06, 07, 08-dormant, 08-held, frozen-early, frozen-at-no_echo, frozen-at-out_of_range, spikes and stuck-max.
  - **Self-check:** each variant asserts the level-1 result it expects.
  - **Image 04** can only be tested this way (`firmware/harness/evaluate.py:12, 213`).
- **Real data, test split only:**
  - windows of `firmware/ultrasonic-sample.json`;
  - Olir's overnight podd logs, once they're in `samples/pod/`;
  - the first `arm-run/v1` runs, with kinematics off.
- **Sizes:**
  - **Test split:** about 1,200 items, at least 300 of them healthy and at least 150 hard negatives. With 0 alarms out of N, the 95% upper bound on the false-alarm rate is about 3/N, so claiming "≤ 1%" needs N ≥ 300.
  - **`--quick`:** about 250 items.

### What the model sees, and what it answers
- **Lines with ids:** `D05 +0.5 mm=38 us=220 st=ok pred=37.7`, then `J01 …`, `A1 …`, `H01 …`, `E01 …`, `L …`, plus `INC1 …` on track E only.
- **Window lengths:**
  - D and A: 6 s; H: 30 s; E: 180 s. These are podd's own triage windows (`firmware/healer/healer/podd.py:268-270`).
  - J rows are cut to 5 Hz, keeping the row with the largest error in each bin.
- **Prompt size:** the `full` profile is about 1.5–2.2k tokens *(computed in the data report)*; `lite` is about 1k.
- **The system prompt is frozen** for the comparison and carries the healthy facts. For example, the echo time jitters by about 0.4 µs, `mm` can stay constant, and `out_of_range` is normal at home.
- **The answer has exactly six keys:**
  `{"status":"fault","kind":"joint_offset","severity":"warn","evidence":["D17-D30"],"escalate":true,"message":"…≤40 words, names the part, the symptom and one number…"}`
- **`kind`** comes from a closed list of about 25. Each kind has a manager route: sensor_restart, pod_fw, wiring, calibration, arm_hardware, task_redo or manager.
- **Several kinds can count as right for one item,** via `label.accept`. For example, image 02 in sim accepts both clock_skew and us_scale.

### Metrics, gates and ranking
- **Validity:** strict, lenient and schema_ok. An invalid answer counts as no alarm.
- **Detection:**
  - The headline is **gap recall**: recall on observable items where level 1 fires nothing on the same window.
  - Also macro recall, combined recall of level 1 plus the model, and lead time over level 1.
- **False alarms:** the rate for each hard-negative group and for real data, each with its exact 95% upper bound.
- **Kind:**
  - 1 for an exact kind, 0.5 for the right route; plus a confusion matrix.
  - Also count kind corrections. In v2, level 1 named 0 of 95 caught joint_offset runs and 0 of 59 joint_reversed runs correctly *(computed in the data report)*.
- **Also scored:**
  - severity and escalation;
  - evidence hit rate and precision;
  - message score M1–M5, judged by code;
  - latency p50/p95/max, tokens;
  - stability over `--repeat 3`.
- **Gates:**
  - schema_ok ≥ 98%;
  - false-alarm upper bound ≤ 1% on healthy items;
  - 0 alarms on real data;
  - p95 latency ≤ 30 s, the worker's budget.
- **Ranking:** gap macro recall first, then kind score, then message score, then output tokens.
- **Manager eval (new, small)** *(inferred)*:
  - **Input:** 30 track E items wrapped as escalation packets, sent to `manager`.
  - **Score:**
    - the `check_plan` pass rate;
    - whether `plan.action` matches the gold route's action: sensor_restart→restart_sensor, pod_fw→pod_rollback, arm_hardware, calibration and wiring→handoff_human, task_redo→redo_step, none→no_action;
    - latency p50 and p90.
  - **Cost:** it runs on the ChatGPT plan, so no API key is needed.

### Files and functions
- **`kinds.py`:** `KINDS`, `GOLD_SEVERITY`, `ROUTE`, `L1_ALIAS`, `LEXICON`, `kind_score()`.
- **`render.py`:** `render(item, *, profile, track) -> (text, idmap)`.
- **`l1.py`:** `l1_findings()` and `l1_answer()`.
  - Load `armsim/level1.py` by file path, because `armsim/__init__.py:2` imports MuJoCo.
  - Add `firmware/healer` to `sys.path`, as `armsim/podsource.py:21-27` does.
- **`gen_arm.py`:** `run_capture(seed)`, `window_ends()`, `observable()`, `facts()`, `make_items()`. This is the only file that needs the `.venv`.
- **`gen_pod.py`:** `VARIANTS`, `healthy_d()`, `synth(variant, rng, *, target_mm, now)`.
- **`gen_real.py`:** `windows_from_podd_logs()`, `windows_from_sample()`.
- **`prompt.py`:** `SYSTEM`, `ANSWER_SCHEMA`, `messages()`.
- **`score.py`:** `parse_answer`, `gold_ids`, `expand_ids`, `message_score`, `score_item`, `upper95(k, n)`, `summarize`.
- **`run.py`:**
  - flags: `--items --split [--quick] (--endpoint --model | --baseline l1|l1-pod|l1-arm|oracle|healthy|alarm) --profile --track --repeat --json-mode none|object|schema --max-tokens 512 --stream --timeout --label --out runs/wb/<label>/`;
  - `call()` is a urllib POST to `{endpoint}/chat/completions`. It reads `message.reasoning` or `reasoning_content`, and falls back to the native token counts as `agent.py:626` does.
- **`manager_eval.py`:** the manager eval above.
- **`tests/test_workerbench.py`:**
  - the oracle scores 100%;
  - always-healthy gets recall 0 and no false alarms;
  - always-alarm gets 100% false alarms;
  - `l1_answer` matches each variant's `expect_l1`;
  - line ids are unique and every gold row renders.

## 5. Build order for tonight (**[no key]** = needs no API key and no paid model)

| # | Step (time) | How to test it |
|---|---|---|
| 0 | `hermes plugins validate prototypes/arm-sim/hermes-plugin/so101-arm` (2 min) **[no key]** | It should fail today: `plugin.yaml:6-16` leaves out `arm_check`, which `__init__.py:88-95` registers (`docs/hermes/terminology.md:42`). The one-line fix is the lead's call |
| 1 | `armsim/escalation.py`: both schemas, `ACTIONS`, `allowed_actions(inc, state)`, a standard-library `validate()`, `check_plan()` (20 min) **[no key]** | `tests/test_escalation.py`, about 15 table cases: valid; wrong incident; action not on the list; an angle in the args; `touches_safety`; redo with no job; a third heal; a late plan; a code fence; two JSON objects |
| 2 | `build_escalation(inc, host, job)` from the host's `status`, `incidents` and `distance` ops (`host.py:8`), plus a lite render of the last 6 s (15 min) **[no key]** | Run `demo/up.sh`, send `{"op":"inject","fault":"us_frozen"}` (`host.py:9, 683`), print the packet and validate it |
| 3 | `ManagerClient.ask(esc) -> (plan or None, why)`, plus `tests/fake_manager.py` (`http.server` on `/v1/runs`) with these modes: good, bad JSON, action not on the list, slow, 429, 500, connection refused (20 min) **[no key]** | One unit test per mode |
| 4 | `execute(plan)`: plain code issues the checked action. Hook it into `ScriptedExecutor._heal` (`dispatch.py:241`) behind `--manager off\|fake\|hermes`. The default `off` is today's behavior, and the fallback is the existing code (25 min) **[no key]** | `stories b --manager fake` gives the same `RESULT:` as today. `fake-bad` is refused, falls back, and still gets `RESULT:`. `fake-slow` falls back at the deadline (set to 10 s in tests). `tests/test_dispatch.py` still passes unchanged |
| 5 | The model writes the escalation: `worker_answer()` via `{ollama}/v1/chat/completions` with the six-key `response_format`, a 30 s cap, and `--worker-lm off\|ollama` (15 min) **[no key; needs Ollama]** | From the Pi: `--worker-lm ollama --ollama http://192.168.50.1:11434/v1` |
| 6 | The `manager` profile (§2.1 branch a or b, then §2.2) (20 min) | **[no key]:** `GET /health` (H:L39505), `/v1/capabilities` (H:L39406), and `/v1/toolsets` showing no terminal or `so101_arm` (H:L39796); a wrong key returns 401. Then one real run on a saved packet (ChatGPT-plan login), recording its latency |
| 7 | End to end in sim: `stories b`, `stories a --image 07` and `stories stall`, each with `--manager hermes` (scripted executor; not `--hermes`) (15 min) | The dispatcher's event log shows incident → packet → plan → check → action → verify; the Romulus incident report arrives; the Naïve card has `PLAN:` and `RESULT:` |
| 8 | Optional: `--exec-via lm`. The model issues the call, the gate allows only an exact match, and plain code takes over after 15 s (15 min) **[no key; Ollama]** | With the fake manager plus Ollama, a call that doesn't match the plan is refused and logged |
| 9 | Only if open question 1 says yes: category 2. Add an `inject` that shifts the block (sim only). The existing missed-grasp check (`dispatch.py:215-216`) opens `wrong_action`, then the `redo_step` path runs (30 min) **[no key with `--manager fake`]** | A new story: the grasp is missed, the action reversed and redone, and the card gets `RESULT:` |
| 10 | In parallel (another person or session): workerbench `kinds`/`render`/`l1` → `gen_pod` → `score`/`run` with the baselines → `gen_arm`; then the CPU sample, then one GPU pod (§3) (about 1 h) **[no key]** | `--baseline oracle` scores 100% and `--baseline l1` reproduces the v2 tally; then the models |
| 11 | 7 AM, the real arm: start with `--manager off`, and switch to `--manager hermes` only if steps 4 and 7 passed. Kinematics are off, so only the frozen-sensor, joint-stall and podd checks run (`prototypes/arm-sim/README.md:41-44`) | Plant one frozen sensor by hand |

- **At the 10 AM code freeze,** anything that hasn't passed ships with `--manager off`.
- **Needs no key at all:** steps 0–5, 8 and 10; step 6's health, toolset and auth checks; and Kanban `dispatch --dry-run`.
- **Needs a paid model or a login:** one real manager run (ChatGPT plan), Path B (a Naïve key) and the GPU pod (a Runpod key).

## 6. Open questions for the user (James)
1. **Is category 2 in scope tonight?** It is almost certainly the "wrong action" you recorded: "the arm did the wrong thing (missed the grasp, wrong place, bumped something). The system reverses the action, then the LLM corrects the command and re-runs it" (`docs/healer-data-levels.md:21-23`).
   - The dispatcher already detects a missed grasp (`dispatch.py:215-216`, `arm.py:267-271`), but today it only blocks the card.
   - Build `redo_step` tonight (step 9, with a sim-only planted fault), or after the freeze?
2. **Which machine hosts the manager gateway, and can the Pi reach it?**
   - The Pi–laptop link is a direct cable on 192.168.50.x (`server/README.md:8-10`).
   - Hermes v0.23.0 is on your Mac (`docs/pi-vs-hermes.md:11`).
   - If those are different machines: install the `manager` profile on the laptop, or put the Mac on the same router.
3. **Does this count as using the Naïve SDK?** The rule is "Every team must use the Naïve SDK" (CLAUDE.md).
   - What we'd have tonight: Hermes on the bench, a Naïve card as the record, and gpt-6-sol.
   - Is that enough, or must Path B (a Naïve `pi` team returning the fix plan through sessions) be built too?
   - This is still open in `docs/cloud-platform-planning/cloud-platform-naive-integration.md:192` (Q1).