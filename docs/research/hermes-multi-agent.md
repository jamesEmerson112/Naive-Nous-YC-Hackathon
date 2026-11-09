# How Hermes can run several "manager" agents for the arm's local worker

**Recommendation:** run one `manager` Hermes profile on the laptop. The worker on the Pi sends it an escalation over the API server (`POST /v1/runs`). The manager runs one subagent per subsystem and replies with one typed action. The worker's plain code checks that action and carries it out. I edited nothing in the repo.

**How to read citations:** `H:L<n>` is a line in `docs/raw/hermes-llms-full.txt`, a copy of https://hermes-agent.nousresearch.com/docs/llms-full.txt taken 2026-11-07 (`docs/hermes/terminology.md:4`). Repo citations are `path:line`. *(inferred)* marks my own conclusions.

## 0. One question decides the design: does the worker get the answer back in the same request?

Only two Hermes entry points send a reply back to the caller:
- **API server Runs:** `POST /v1/runs` returns a `run_id`. `GET /v1/runs/{id}` returns `status` and `output` (H:L39524-39560). It also has an `Idempotency-Key` header (H:L39536) and `POST /v1/runs/{id}/approval` (H:L39705).
- **A2A:** an inbound task goes into a live gateway session and "the final reply is returned to the caller as the task result" (H:L96563).

The other two are one-way:
- **Inbound webhooks (:8644):** there is no "reply over HTTP" option. Every `deliver` target is a chat platform, `log` or `github_comment` (H:L35288-35307).
- **Kanban:** you create a card and the caller gets nothing back (H:L20048-20050).

So with webhooks or Kanban, the fix can only reach the device through a tool call Hermes makes itself.

## 1. Ways to run several agents, and their limits

**`delegate_task`: parallel child agents inside one run**
- Each child has its own context, and only its final summary comes back (H:L19151).
- **Concurrency:** up to 10 at once by default (H:L19187, H:L19353).
- **Typed answers:** each task can carry an `output_schema`; a wrong answer gets one correction turn (H:L19199-19201).
- **Tools:** children get the parent's toolsets, and the model cannot narrow them (H:L19519).
- **Blocked tools:** leaf children lose `delegate_task`, `clarify`, `memory`, `send_message` and `cronjob` (H:L19521-19526).
- **Budget:** 250 iterations per child by default (H:L19532-19538).
- **One-shot cap:** `chat -q` / `--oneshot` runs may spawn only **2 children in total** (`delegation.oneshot_max_children`, H:L19740, H:L7006).
- **Not durable:** after a restart a running child is recorded as `unknown` (H:L19826-19840).
- **Shared folder:** children share the parent's working directory unless `worktree_isolation` is on (H:L19854).
- **Cost:**
  - Children use the parent's keys (H:L19850) and burn most of a run's tokens (H:L19423).
  - Setting `delegation.base_url`, `model` and `provider` sends children to a cheaper model, e.g. the laptop's Ollama (H:L19421-19436, H:L19919-19923).
  - By default that model choice is global, not per task (H:L19436). Per-task routing (`model_map` / `profile_routes`) needs the `advanced-delegation` plugin (H:L19438-19470).
  - A profile route hands that profile's keys and terminal access to the child (H:L19478-19480).

**Subagent Lifecycle API (from a plugin)**
- A plugin can launch children with a narrower `allowed_toolsets`, then wait for, steer, cancel and read them (H:L76311-76346).
- It refuses toolsets wider than the parent's; plugin toolsets count as known (H:L76376-76381).
- Results are kept in memory for 1 hour, and handles do not survive a restart (H:L76370-76374).
- This is the only documented way to give diagnostician children read-only tools while the parent keeps more *(inferred)*.

**Kanban: durable work queue across profiles**
- **What it gives:**
  - Each worker is a named profile running as its own OS process (H:L19956).
  - Cards support retries, human comments and unblocking, and per-task skills and model (H:L20577-20628).
  - `hermes kanban swarm` builds parallel workers, then a verifier, then a synthesizer, in one command (H:L21007-21015).
  - It fires `kanban_task_*` hooks (H:L20650-20659).
- **Limits:**
  - **Single host only** (H:L21419).
  - The dispatcher checks every 60 s by default; you can nudge it (H:L20083, H:L20837).
  - It needs a running gateway (H:L20285-20287).
  - `auto_decompose: true` spends an extra LLM call every tick, so set it to false (H:L20749, H:L20775).
  - `max_in_progress` and `max_in_progress_per_profile` throttle slow local models (H:L20958-20959).

**Profiles: one isolated Hermes home per role**
- Each has its own config, `.env`, `SOUL.md`, skills, plugins and sessions (H:L8956).
- Never point two processes at one profile (H:L8958-8960).
- `--description` is what Kanban uses to route work (H:L9014-9018).
- **Catch:** a plugin's toolset is on by default in every session (H:L16354-16357), and even an empty platform list still adds enabled plugin toolsets (H:L38901).
- So keep roles apart with separate profiles or `agent.disabled_toolsets` ("remains authoritative", H:L20395), not with `-t` *(inferred)*.

**The gateway: one process that hosts it all**
- It runs webhooks, the API server, A2A, the cron ticker and the Kanban dispatcher.
- It serves several profiles behind `/p/<profile>/`, on by default (H:L102402), each with its own `API_SERVER_KEY` (H:L39844-39860).
- **Caps:**
  - API runs: `gateway.api_server.max_concurrent_runs`, default 10, then HTTP 429 (H:L39907).
  - Webhooks: 30 requests per minute per route (H:L35563).

**Cron:** scheduled agent runs, plus `no_agent` script-only jobs that use no LLM at all (H:L17680, H:L18638). Good for an overnight sweep, not for live healing *(inferred)*.

**Approvals**
- `cron_mode`, `single_query_mode` and `unattended_mode` all default to `deny` (H:L10764-10783).
- They only gate dangerous terminal commands and `execute_code` scripts (H:L10939-10974, H:L10982), not plugin tools.
- A `pre_tool_call` hook that returns `approve` sends the call to a human, and "denial, timeout, or gate error fails closed" (H:L22943). Only `/v1/runs` clients can answer that prompt (H:L10783).
- So the plain-code check should stay a `block` in `pre_tool_call`, as `so101-arm/__init__.py:97-114` does today *(inferred)*.

**Two contradictions in the docs; test before relying on either:**
- Leaf children keep `execute_code` (H:L19528, H:L19847) vs. it is blocked (H:L44968).
- `max_concurrent_children` defaults to 10 with no ceiling (H:L19187) vs. the config example's 3, and `max_spawn_depth` "1-3, clamped" (H:L7001, H:L7004).

## 2. How an escalation gets in, and how the fix gets back

**Ways in:**
1. **`POST /v1/runs` (recommended).**
   - The caller gets a status and output back.
   - The idempotency key is kept 24 h (H:L39536), so the incident id works as the key.
   - The client can answer approval prompts (H:L39705-39720).
   - Setup: `API_SERVER_ENABLED`, `API_SERVER_KEY` and `API_SERVER_HOST` set to the laptop's Ethernet IP; it binds to 127.0.0.1 by default (H:L39879-39884).
   - Warning: it gives "full access... including terminal" (H:L39870), so narrow the profile's tools.
2. **A2A `SendMessage`.**
   - Port 9900 (H:L96614); localhost-only unless a token and `A2A_HOST` are set (H:L96600).
   - One `contextId` per conversation, and a 5-turn anti-loop cap (H:L96620).
   - Inbound tasks share a live session (H:L96563), so isolation is weaker than with Runs *(inferred)*.
3. **Webhook at :8644.**
   - HMAC is required (H:L35550). Use the V2 headers: signature plus a timestamp within ±300 s (H:L35545).
   - `coalesce` keyed by device turns a burst of incidents into one run (H:L35115-35149).
   - A `script` filter can drop noise by printing `[SILENT]` (H:L35151-35176).
   - The default webhook toolset is only `web_search`, `web_extract`, `vision_analyze` and `clarify` (H:L35495).
   - A per-route `toolsets` list is a manual edit, and **it is unconfirmed whether a plugin toolset name passes its check** (H:L35528).
   - Nothing comes back to the caller.
4. **`hermes chat --oneshot` spawned by a dispatcher.**
   - This is how arm jobs run today (`prototypes/arm-sim/armsim/dispatch.py:28-30, 68`; `armsim/stories.py:125-126`).
   - `--format stream-json` emits a final `result` record (H:L51706-51727), and the exit code reports the outcome (H:L51729-51740).
   - It is capped at 2 children and approvals default to deny.
5. **A Naïve board card** (`stories.py b --hermes`, read through the `naive_board` MCP filter, `prototypes/arm-sim/README.md:36`). Good for jobs and records, too slow for a heal *(inferred)*.

**Ways out:**
- (a) A reply over Runs or A2A that the device's plain code checks and carries out.
- (b) A Hermes tool call into the arm host or podd.
- (c) A Romulus proposal the device pulls at check-in: `ProposalPointer` (`packages/contracts/src/romulus_contracts/doctor.py:212-224`).
- (d) A card comment (`RESULT:` / `BLOCKED:`).
- (e) `hooks.outbound` (H:L24556-24571), which is notify-only and cannot steer (H:L24622).

**Romulus already has this seam:**
- `POST /v1/devices/{id}/doctor/ask` (`cloud/src/romulus_cloud/api/doctor.py:60`).
- `DoctorAsk.budget_s` is clamped to 3–120 s, default 20, and any non-200 means "no answer" (`romulus_contracts/doctor.py:123-141`).
- `DoctorAdvice` is "Hypotheses only, never a patch: the healer stays in charge" (`doctor.py:201-208`).
- A Hermes manager could sit behind this contract unchanged *(inferred)*.

## 3. Proposed design (all *(inferred)* unless cited)

**What decides it:** the plugin talks to the arm host over a Unix socket (`hermes-plugin/so101-arm/__init__.py:15, 34-36`). The pod tools run locally or over SSH (`firmware/harness/agent.py:114-152`). A manager on the laptop cannot reach the Pi's arm host through that socket. So "a tool call into the arm" needs either Hermes on the Pi or a new TCP/SSH transport.

**Recommendation: the manager advises, the worker acts.**
- **What the manager returns:** one action from a fixed list.
- **Who checks it:** the device's plain code, against:
  - the arm host's `REPAIRS = ("restart_sensor","verify")` (`prototypes/arm-sim/armsim/host.py:249`);
  - park;
  - pod actions under the pod `Gate`, which caps flashing (`firmware/harness/agent.py:386-418`; allowed risks at `:39`).
- **Who decides success:** `verify()` (go home, fresh readings, podd agreeing; `host.py:340-380`), not the model.
- **Why:**
  - No new network path can move the arm.
  - The check sits next to the hardware.
  - If Hermes is down, the arm is already parked (`host.py:307-321`).
  - It keeps the team's "no LLM in the control loop" rule and the Fleet Doctor's "hypotheses only" rule.

**Roles:**
- **Worker (Pi).**
  - **When it escalates:** the level-1 plain-code rules trigger (`armsim/level1.py`, `firmware/healer/healer/detect.py`). qwen3.5:4b first runs its known playbook (`firmware/harness/agent.py:774-797`), then escalates if:
    - the fault kind isn't in its playbook;
    - it ends a run without a verified fix;
    - two verifies have failed (the host's own threshold, `host.py:375`).
  - **How:** a new `ask_manager` tool with risk `notify`, which the Gate already allows (`agent.py:39`).
  - **Arm side:** escalations can hook in at `Watchdog.on_open` (`host.py:256, 318`).
- **Manager / triage:** profile `manager` on the laptop gateway. It answers `/v1/runs`, picks which diagnosticians to run and decides. Tools: a new read-only `so101_diag` plugin; no terminal.
- **Diagnosticians `diag-arm`, `diag-pod`, `diag-sensor`:** one `delegate_task` child each, with `output_schema {hypothesis, evidence[], confidence, action}`. They inherit the manager's tools, which is why the manager gets read-only tools only.
- **Critic:** one more child that returns `{approve, reason, risk}`. Its verdict is advisory; the real verifier is plain code.
- **Reporter:** not an LLM. The worker puts the advice and the outcome in its existing Romulus incident report (`prototypes/arm-sim/README.md:35`).
- **Slow lane:** Kanban profiles `fleet-ops` and `fw-patcher` for hardware handoffs and firmware patches, then the Fleet Doctor's replay and canary.

**Collisions:**
- While the dispatcher's commander (`dispatch.py:68`) holds a job, the manager only advises. The manager profile must not enable `so101-arm`.
- There is one arm, so serialize escalations per device: idempotency key on Runs, `coalesce.key` on webhooks.

**Config.** Create the profile:
```bash
hermes profile create manager --description "Arm-company incident manager: diagnoses arm, pod firmware and sensor faults from an evidence packet; answers with one typed action."
```

`~/.hermes/profiles/manager/config.yaml`:
```yaml
delegation:
  base_url: "http://127.0.0.1:11434/v1"   # children on the laptop's Ollama (H:L19919-19923)
  model: "qwen3.5:4b"
  max_concurrent_children: 3
  max_iterations: 20
  oneshot_max_children: 0                  # only matters on the --oneshot path (H:L7006)
  child_timeout_seconds: 120               # inactivity cap, minimum 30 (H:L19548-19556)
approvals: {mode: smart, unattended_mode: deny, single_query_mode: deny}
agent:
  disabled_toolsets: [terminal, code_execution, file, browser]   # (H:L20395)
plugins: {enabled: [so101-diag]}           # new read-only plugin (inferred)
kanban: {auto_decompose: false, max_in_progress: 2}
hooks:
  outbound:
    - name: romulus
      url: "http://<laptop-ip>:8000/v1/hermes-events"   # new Romulus route (inferred)
      events: [on_session_end, subagent_stop]
      secret_env: HERMES_OUTBOUND_WEBHOOK_SECRET          # (H:L24556-24571)
```

`.env` for the manager profile:
```
API_SERVER_ENABLED=true
API_SERVER_KEY=<per-profile key>
API_SERVER_HOST=<laptop-ip>
```

Pi → laptop escalation (H:L39850-39856):
```bash
curl -s http://<laptop>:8642/p/manager/v1/runs \
  -H "Authorization: Bearer $KEY" -H "Idempotency-Key: inc-<uuid>" \
  -H "Content-Type: application/json" \
  -d '{"input":"<packet JSON>","instructions":"Use skill arm-incident-manager. Reply ONLY {\"action\":...,\"why\":...}","session_id":"arm-twin-1"}'
```
Then poll `GET /v1/runs/<id>` and parse `output` as JSON. Top-level runs have no schema-checked output (`docs/hermes-harness-gaps.md:49`).

**Skill** `~/.hermes/profiles/manager/skills/arm-incident-manager/SKILL.md`. It tells the manager to:
- run one `delegate_task` per subsystem, each with an `output_schema`;
- then run one critic task;
- then answer with one action from: `restart_sensor`, `verify`, `park`, `pod_rollback`, `handoff_human`, `none`.

It also says the worker carries out the answer and discards anything outside that list.

**Webhook alternative** (no reply, so the fix would have to leave by a tool call):
```yaml
platforms:
  webhook:
    enabled: true
    extra:
      port: 8644
      routes:
        arm-escalation:
          secret: "<secret>"                   # sender signs with V2 (H:L35545)
          profile: manager                     # (H:L35030)
          coalesce: {key: "{device_id}", window_seconds: 10, max_wait_seconds: 60}
          prompt: "Incident {incident.kind} on {device_id}: {incident.summary}. Evidence: {evidence}"
          skills: ["arm-incident-manager"]
          toolsets: ["so101_diag"]             # plugin toolset name: unconfirmed (H:L35528)
          deliver: log
```

**Kanban slow lane** (needs the gateway running):
```bash
hermes kanban init
hermes kanban swarm "Postmortem inc-42" \
  --workers diag-arm,diag-pod,diag-sensor --verifier critic --synthesizer planner
hermes kanban create "Replace elbow servo on arm-1" --assignee fleet-ops \
  --idempotency-key inc-42 --json
```

## 4. What can be tested without an LLM key

- **Plugin checks:** `hermes plugins doctor <path> --ci` and `hermes plugins validate <path>` (`docs/hermes/terminology.md:92-93`).
  - These should **fail today**: `plugin.yaml:6-16` `provides_tools` leaves out `arm_check`, which `__init__.py:88-95` registers, and validate fails on that mismatch (`terminology.md:42`).
  - The `pre_tool_call` guard can be unit-tested with a fake `ctx` and a stub socket.
- **Webhook route:**
  - `curl :8644/health` (H:L35009).
  - A route whose `script` prints `[SILENT]` proves HMAC V2 and routing: it returns 200 `ignored`/`script` and no agent runs (H:L35176).
  - A bad signature returns 401 (H:L35378).
  - `hermes webhook test` does start an agent run, so it needs a key.
- **API server and A2A:**
  - `GET /health` (H:L39505), `/v1/capabilities` (H:L39406).
  - `/v1/toolsets` and `/v1/skills`, which confirm the manager has no terminal (H:L39796).
  - The A2A agent card at `/.well-known/agent-card.json` (H:L96558).
- **Kanban:** `init`, `create`, `swarm`, `list`, `context`, `dispatch --dry-run`, `complete --summary`, with `auto_decompose: false` and no gateway, so no workers spawn.
- **Still an LLM, but no paid key:**
  - qwen3.5:4b on the laptop's Ollama as both the provider and `delegation.base_url`.
  - `dispatch.py --exec scripted` (`dispatch.py:21-27`, `prototypes/arm-sim/tests/test_dispatch.py`) is the deterministic stand-in for Hermes.
  - The fault factory (`python -m armsim.factory`) plus the pod `evaluate.py` blind eval can score the manager's answers.

## 5. To settle before building

- **Version:** check which Hermes version is installed against these docs. They describe features through mid-2026, e.g. the July 2026 change at H:L39862.
- **Plugin toolset in a webhook route:** whether a webhook route accepts a plugin toolset name (H:L35528).
- **Parallel children on Ollama:** whether Ollama serves the three diagnosticians at the same time or one after another *(inferred risk)*.
- **Where Hermes runs:** laptop only (recommended), or also on the Pi, which return path (b) would need.