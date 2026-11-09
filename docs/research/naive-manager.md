# How Naive can run the manager side (Hermes and several agents) for the arm's local worker

**Short answer:** yes, Naive can run a team of manager agents, and a Hermes harness exists, but two things limit it today:
- **Hosted Hermes wasn't runnable on our org** at the last check (Sat 3 PM).
- **Naive can't reach the arm or the Pi.** The worker has to fetch the fix itself, by polling, rather than the manager pushing it.

A full copy of this report is at `/tmp/claude-0/-home-user-Naive-Nous-YC-Hackathon/40da8899-cc0b-54bd-b44e-30bb039d8e71/scratchpad/naive-manager-report.md`. No repo files were changed.

**How sources are cited:** `N:<line>` is `docs/raw/naive-llms-full.txt`, `H:<line>` is `docs/raw/hermes-llms-full.txt`, and repo files are `path:line`. *(inferred)* marks my own conclusions. I made no live API calls: this container has no `naive` or `hermes` CLI and no Naive key.

## 0. The two facts that shape everything

1. **Naive has a Hermes harness, but it was not runnable on our org at the last check.**
   - **The docs:** `harness: "hermes"` is "an external coding agent, run as a process" on a micro-VM (N:21713). It is in preview and "needs an image" (N:21541).
   - **Kent's live reading:** on Sat at 3 PM, `GET /v1/harnesses` returned `hermes: runnable: false` (`docs/naive/company-setup.md:36`).
   - **What that means:** a session on a harness with no image fails at start with `501` naming `harness` (N:21633).
   - **Re-check it** (read only): `curl -s https://api.vetta.sh/v1/harnesses -H "authorization: Bearer $KEY" | jq '.data[] | {base, runnable, capabilities}'`
   - **Everything below about hosted Hermes depends on that value now being `true`.**
2. **The answer has to be pulled; Naive can't push into our network.**
   - Webhook URLs must be public HTTPS (N:14548), and so must agents' MCP server URLs (N:14415).
   - Sandboxes can't open a port: "A sandbox reaches out; nothing reaches in" (N:20674).
   - Tools that run on our side are listed as "Coming soon" (N:14497).
   - So "the manager responds back" means the worker reads the answer. It polls a card or session, or holds an outbound SSE stream *(inferred)*.

## 1. Bottom line

- **What Naive gives the manager side:** a **team**, meaning one coordinator agent and a roster of 1–20 member agents, each pinned to a version (N:31668, N:31877, N:31962). The agents coordinate two ways:
  - **Delegation:** `send_to_agent` with `wait: true`. Results can be typed, and delegation goes only one level deep (N:31650, N:31747).
  - **A shared, durable board:** assigning a card starts a session for the assigned agent (N:30999).
- **Spend is checked before every call** (N:21262). Each member spends against its own agent cap (N:31232).
- **What works tonight, with no Hermes image:**
  - A Naive team on the `pi` harness, which has typed output, approvals and full tool events (N:21542–21543).
  - Hermes on the bench as a client of Naive, reading the board over MCP. The dispatcher already does this (`prototypes/arm-sim/armsim/dispatch.py:68`, `prototypes/arm-sim/README.md:36`).
- **What waits on the image:** a hosted `hermes` coordinator or member.
  - Teams may mix harnesses, and any harness can coordinate (N:13807, N:21548).
  - On `hermes` you lose tools that need approval, typed output, streaming and tool events (N:21598, N:21543, N:21661).
- **"Several agents in the Hermes harness"** can mean either of two things:
  - **Naive roster members:** hosted, budgeted, and visible on the board.
  - **Hermes's own `delegate_task`:** up to 10 subagents at once by default, each optionally held to an `output_schema` (H:19187, H:19199). This is the option when Hermes runs on the bench.
- **The setup I recommend** *(inferred)*:
  - The worker opens one session (or card) per incident on an `incident-lead` coordinator.
  - The coordinator runs on `pi` now. It can move to `hermes` once that's runnable, if we accept untyped output.
  - It hands the work to typed `pi` members (diagnose, plan the fix, review safety) and returns one typed **FixPlan**.
  - Plain code on the worker checks the plan against its allowlist before anything moves.
  - This fits alongside Kent's recorded decision N3: Romulus as a Naive app exposing `romulus.*` tools (Path D, `docs/naive/company-setup.md:58`).

## 2. What Naive gives the manager side

### 2.1 Agents and harnesses
- **An agent is a versioned config:** model, prompt, tools, skills, a required budget, a window and `harness` (`docs/naive/terminology.md` §2).
- **`harness` can only be set when the agent is created.** A PATCH that sets it is ignored, so switching loops means creating a new agent (N:21805).

| | `pi` | `vetta` | `claude_code` | `hermes` |
|---|---|---|---|---|
| Status | ga | preview | preview | preview (N:21541) |
| Runnable | always | only orgs it's been opened to | needs an image | needs an image (N:21541) |
| Approvals (`ask` tools, `ask_operator`) | yes | yes | no | no (N:21542) |
| Typed output (`structured_output`) | yes | declared, not served | no | no (N:21543, N:31628) |
| Coordinator / member | yes / yes | yes / yes | yes / yes | yes / yes (N:21548) |

**What changes on `hermes`:**
- **Tools that need approval are dropped,** and a session whose toolset contains one is refused at start (N:21598).
- **None of Naive's own built-in tools:** no web search or fetch, `publish_file`, managed browser or skill loading. Hermes uses its own CLI tools instead (N:21652).
- **Naive's team, MCP, connection and app tools still reach it,** through a per-session tool endpoint (N:21588).
- **`wait_for_agents` doesn't pause the turn.** The coordinator only parks once the turn goes idle (N:21608).
- **No `tool.started` or `tool.completed` events for any tool** (N:21661). That weakens the "root cause on the timeline" demo step *(inferred)*.

### 2.2 Teams: coordinator, roster, members, versions
- **A team is not a separate object.** It is an agent whose config contains `multiagent: {type: "coordinator", agents: [...], board: true}` (N:31668, N:30956).
- **Members are pinned to a version.** If you leave the version out, saving pins the current one, and later edits to that member don't carry over (N:31877).
- **Saving a roster checks each member:** that it exists, that the version exists, that it isn't itself a coordinator, that its harness can run here, and that the harness fits the member's config. A failure refuses the save (N:31891).
  - So a `hermes` member can't even be saved while the image is missing *(inferred)*.
- **Two requirements on a member's own config:** a typed result needs a harness with typed output (N:31907), and any `ask` tool needs a harness with approvals (N:31908).
- **Roster entry types:**
  - `agent` works.
  - `self` (copies of the coordinator) works (N:31394).
  - `advisor` is refused at save as "not yet served" (N:31416).
- **Limits:**
  - 1–20 members, at most one `self`, and one level of delegation (N:31962, N:31650).
  - The number of members running at once is capped, but the number isn't published. `list_agents` and the `at_capacity` refusal report it at runtime (N:31649, N:31556).
- **CLI:** `naive agent create …`, then `naive team set <agt> --member name@ver … --board`. There are also `team add`, `team remove` and `team show` (N:31780–31782, N:19413).

### 2.3 Delegation (point to point, typed)
- **`send_to_agent({agent, message, wait: true, output_schema?})` returns at once.** The member runs in its own thread, on the coordinator's session budget, and its answer comes back after `wait_for_agents` (N:31478).
- **`output_schema` needs `wait: true`** and a member that reports itself as typed. Otherwise the call is refused with `no_structured_output` (N:31519).
- **Who an agent may name is set by `handoffs`:** `true`, `"team"`, a list of names, or `false`. Every agent can always reach the CEO (N:13945–13950).
- **`wait: false` is a handoff,** which starts a separate top-level session.
  - Chains stop at depth 4, and one session can start at most 8 handoffs (N:13954).
  - The API route for people is `POST /v1/sessions/{id}/handoffs`, to 1–6 agents at once (N:10239).
- **What the coordinator sees:**
  - Its event stream shows `thread.created`, `thread.idle` (with a 160-character summary) and `thread.terminated` (N:31255).
  - A run's total cost is the root session's plus each thread's, listed at `GET /v1/sessions/{id}/threads` (N:31242).

### 2.4 The board (durable; assigning a card starts a session)
- **Card states:** `todo`, `doing`, `blocked` and `done`, and the set is fixed. Moving a card to `done` needs a note, and cards are never deleted (N:31155–31165).
- **Assigning a card starts the assigned agent:**
  - **Through the API:** the session starts in the same request, and the response includes `woken_session_id`.
  - **Through an agent's `board_write`:** the session starts as soon as the write lands.
  - **Anything else:** the session starts on the board's next once-a-minute sweep.
  - **The started session** is an ordinary top-level session on the assignee's own budget, tagged `metadata.board = {card_id, board_id}` (N:30999).
- **A card created with `key` waits for the sweep,** so it can start up to a minute late (N:2371). The `Idempotency-Key` header avoids that delay (`docs/cloud-platform-planning/cloud-platform-naive-integration.md:44-48`).
- **Limits per card:**
  - Each agent works one card per board at a time (N:31014).
  - A card starts at most 3 sessions, then it is moved to `blocked` for the owner (N:2245).
- **The rule you described** ("blocked if the session ends without done") **is confirmed.**
  - If the session ends (finished, or idle after its turn) with the card still `todo` or `doing`, the card moves to `blocked`. A system comment names the session and why it stopped.
  - The card is never restarted in a loop (N:31036).
- **The board's owner gets one summary session per board** for cards that reached `done` or `blocked` (N:31051, N:31360).
- **Which board an agent sits on:**
  - Its own board if it has one turned on; otherwise the board of the coordinator whose roster names it; otherwise the company board.
  - A roster always wins over the company board (N:30966–30968).
  - So an incident card's assignee must be on that board's roster *(inferred)*.
- **REST routes** (N:31155–31165):
  - `POST /v1/boards`
  - `GET|POST /v1/boards/{brd_|agt_}/cards`
  - `GET|PATCH …/cards/{cid}`
  - `GET|POST …/comments`
  - Scopes are `agents:read` and `agents:write` (N:31164). A key that can write cards can also manage agents, so scope the device's credential (`cloud-platform-naive-integration.md:108`).

### 2.5 Sessions (the typed request-and-answer path)
- **`POST /v1/sessions` creates and starts a session.**
  - Required fields: `agent_id` and `message`.
  - Optional fields: `budget_micro_usd`, `output_schema`, `structured_output_required`, `metadata` and `window` (N:10135–10200).
- **`GET /v1/sessions/{id}`** returns `status`, `stop_reason`, `consumed_micro_usd` and `structured_output` (N:10225).
  - Always branch on `stop_reason`: an `idle` session hasn't necessarily succeeded (N:10133).
- **How the typed answer comes back:**
  - Setting `output_schema` gives the agent a `submit_output` tool. A non-matching answer is handed back to the agent to fix (N:13759).
  - With `structured_output_required`, a session that can't produce a matching answer ends idle with `stop_reason: error` (N:13749).
  - An `output_schema` on the agent is the default for all its sessions (N:13671).
- **Steering and events:**
  - Send to a running session with `POST /v1/sessions/{id}/messages`, using `interrupt` or `queue` (N:10275).
  - List events with `GET …/events?after_seq=`; they can be replayed for at least 72 h (N:6099).
  - Stream them over SSE with `GET …/stream` (N:6130).
- **CLI:** `naive session run … --output-schema f.json --require-output --json` waits for the session to go idle. Its exit code reflects the stop reason: 0 for a normal finish, 5 for out of budget, 7 for an error (N:18740, N:18904).

### 2.6 Triggers: schedules, webhooks, inbox
- **Scheduled runs (deployments):**
  - Cron can fire at most every 5 minutes (N:13008). Each run needs a `budget_micro_usd`, and `on_idle` can set up a webhook (N:12854, N:12942).
  - A run waiting on a person makes the next one skip (N:12889).
  - Each schedule gets one standing "Recurring:" card on the board (N:12893).
  - Good for a nightly summary, not for incidents *(inferred)*.
- **Outgoing webhooks:**
  - For sessions, the only events are `session.idle`, `message.completed` and `budget.exceeded`, and there are **no board events** (N:14556).
  - They are HMAC-signed and must be answered within 10 s (N:14567, N:12481).
  - They go to a public HTTPS address only, so the Pi can't receive them.
- **Incoming email and SMS wake the agent that owns the inbox,** at most 5 times an hour per sender and 20 an hour per inbox. That rules it out for incidents (N:23872).

### 2.7 Budgets
- **Every agent needs a budget:** `cap_micro_usd` per day, week or month, plus `max_task_micro_usd` (N:21314).
- **Check order:** org balance, then agent cap, then task ceiling, then session budget (N:21262).
- **A refused call** emits `budget.exceeded`, and the session waits as `budget_paused`. Raising the cap resumes sessions paused in the last 7 days (N:21269).
- **In a team:** delegated threads share the root session's ceiling (N:31231), each member is also held to its own agent cap (N:31232), and board reads and writes are billed too (N:31179).

### 2.8 Model proxy (gpt-oss-120b)
- **Two endpoints:** `…/v1/proxy/openai/v1/chat/completions` and `…/v1/proxy/anthropic/v1/messages` (N:9674). The key needs `proxy:write` or `sessions:write` (N:9706).
- **What we've confirmed:**
  - Our team key works, and `openai/gpt-oss-120b` answers in about 1.8 s (`cloud-platform-naive-integration.md:172`).
  - The proxy has no model list; use `GET /v1/models` (same file, line 171).
  - The docs' own example shows the cheaper `priority` window refused for gpt-oss-120b (N:9889).
- **Fields the proxy refuses:**
  - The OpenAI endpoint refuses `top_p`, `seed`, `logprobs`, `response_format`, `tool_choice` and the penalty settings with `400`. It accepts `temperature` but ignores it (N:9821).
  - The Anthropic endpoint accepts `thinking` only as `disabled` (N:9750).
- **How proxy calls are capped:**
  - Calls made with an org key carry no session or agent, so only the org balance limits them, not any agent cap (N:9900).
  - A hosted Hermes's own model calls go through the proxy under its session, so they are held to the session and agent budgets (N:9902).

### 2.9 MCP server and scoped tokens
- **`POST /v1/mcp` exposes the whole Naive API as MCP tools,** with names like `agents_get` (N:7444). Tool results are cut at 8,000 characters (N:7596).
- **An admin can mint a scoped token:** `POST /v1/mcp_sessions`, or `naive mcp create --scopes … --tools … --expires-in …` (N:7680, N:7695, N:16918).
  - The token also works on the REST route behind each allowed tool, but never on a route the MCP catalog leaves out (N:7727–7729).
  - The SSE stream route `GET /v1/sessions/{id}/stream` is left out (N:7609).
  - So a device with a scoped token has to poll; streaming needs a full `sk_live_` key *(inferred)*.

### 2.10 Vault, the waiting list, and settings for unattended runs
- **Vault:** credentials for MCP servers never sit on the agent.
  - A `static_bearer` or `mcp_oauth` credential is matched to a server by URL and added on Naive's side when the session starts. One bound to an `agent_id` reaches only that agent (N:14418).
  - **CLI:** `naive vault create --name … --identity …`, then `naive vault set --vault vlt_… --mcp-bearer https://…/mcp`, with the secret read from stdin (N:24555–24565).
  - `env_var` credentials return `501`.
  - The Mobilerun key is already planned this way (`cloud-platform-naive-integration.md:106`).
- **`naive session inbox`** lists every session waiting on a person (N:18743).
- **Turn off `ask_operator` and `request_tools` on every manager agent** *(inferred)*. Either one makes a session wait for a person, which stalls an unattended overnight loop. On `hermes`, an enabled `ask_operator` is refused at start anyway (N:21633 table).

## 3. How the worker opens work and reads the answer back

There's no Python SDK, only TypeScript (`@usenaive-sdk/managed-agents`). From the Pi, call the REST API with `httpx` (`cloud-platform-naive-integration.md:30, 150`), or call the `naive` CLI the way `prototypes/arm-sim/armsim/board.py` does.

**Open question:** the hackathon rule says "Every team must use the Naïve SDK" (CLAUDE.md). Whether REST from Python counts is still unanswered (`cloud-platform-naive-integration.md:192`, Q1). The SDK calls are listed below, and a small Node shim on the laptop would meet the literal rule *(inferred)*.

**The device's credential (an admin mints it once):**
```bash
naive mcp create --name arm-worker \
  --scopes agents:read,agents:write,sessions:read,sessions:write \
  --tools boards_create_card,boards_get_card,boards_list_cards,boards_create_comment,sessions_create,sessions_get,sessions_list_events \
  --expires-in 172800
```
- The board tool names come from `company-setup.md:41`.
- `sessions_create` and `sessions_list_events` are in the docs (N:7684, N:7444–7596).
- `sessions_get` is my guess from the naming pattern *(inferred; check with `tools/list`)*.

### Path B (recommended): one typed session per incident
```bash
curl -fsSL https://api.vetta.sh/v1/sessions \
  -H "authorization: Bearer $MCP_OR_KEY" -H "content-type: application/json" \
  -H "Idempotency-Key: inc_$INCIDENT_ID" \
  -d '{"agent_id":"agt_<incident-lead>","message":"<incident brief: device, finding, last log window summary, allowed actions>",
       "output_schema": <FixPlan schema>, "structured_output_required": true,
       "budget_micro_usd": 500000, "metadata": {"incident_id":"'$INCIDENT_ID'"}}'
# then poll (every few seconds) and branch on stop_reason, NOT on status:
curl -fsSL https://api.vetta.sh/v1/sessions/$SES -H "authorization: Bearer $MCP_OR_KEY"     # status, stop_reason, structured_output
curl -fsSL "https://api.vetta.sh/v1/sessions/$SES/events?after_seq=$N&limit=50" -H "…"   # progress, thread.* events
```
- **When to stop polling:**
  - **Done:** `structured_output` is set, or `stop_reason` is `end_turn`, `error`, `budget_paused`, `context_exhausted` or `max_iterations`.
  - **Still working:** `queued` or `running`, or `idle` with `awaiting_delegation`. A coordinator that has handed work to its members waits idle on that reason (N:10128).
  - **Waiting on a person:** `awaiting_approval` or `awaiting_answer`. Escalate and keep the arm parked.
- **The same thing through the SDK** (N:13671 onward, N:21459):
  - `naive.sessions.create({ agent_id, message, output_schema, structured_output_required: true, budget_micro_usd, metadata })`
  - then `naive.sessions.get(id)`, or follow it live with `for await (const e of client.stream(id, { afterSeq }))`.
- **The FixPlan shape is only an example** *(inferred)*: `{incident_id, action: enum[<exactly the worker's allowlist, e.g. restart_sensor, park, verify, rollback_pod_image, escalate_hardware, no_action>], args, rationale, confidence, touches_safety}`.
  - The worker checks it again in plain code whatever comes back, so no LLM sits in the control loop (CLAUDE.md, Romulus rules; `docs/healer-data-levels.md:19`).
- **This path needs a `pi` coordinator.** On `hermes`, requiring typed output is refused at start with a `400` (N:21543, N:21633 table).

### Path A: an incident card on the team's board
```bash
curl -fsSL https://api.vetta.sh/v1/boards/agt_<incident-lead>/cards \
  -H "authorization: Bearer $MCP_OR_KEY" -H "content-type: application/json" \
  -H "Idempotency-Key: inc_$INCIDENT_ID" \
  -d '{"title":"INC-'$INCIDENT_ID' arm-1 us_frozen","body":"<brief ≤20k chars; no raw logs>","assignee":"<a rostered name>"}'
# → 201 with woken_session_id (started in-request, N:30999)
curl -fsSL https://api.vetta.sh/v1/boards/agt_<incident-lead>/cards/$CRD -H "…"            # status, note
curl -fsSL https://api.vetta.sh/v1/boards/agt_<incident-lead>/cards/$CRD/comments -H "…"
```
- **The answer is the card's note,** for example a first line `FIX: {json}` or `STOPPED: <reason>`. It's plain text, so the worker parses and checks it. The integration doc already uses this pattern for work orders (`cloud-platform-naive-integration.md:56-62, 79-84`).
- **Read the card by its id, not from the list,** because finished cards can fall off the first page (same file, line 53).
- **A version with no coordinator turn** *(inferred)*:
  1. The worker creates three cards: "diagnose" for `diagnoser`, "plan-fix" blocked by it for `fix-planner`, and "review" blocked by that for `safety-reviewer`.
  2. Each card's agent starts as soon as the previous card is finished (N:30999).
  3. The worker reads the last card's note.
  4. The coordinator only gets the summary. Each summary is its own billed session, one per board per minute in which cards changed (N:31051).
- **Choosing:** Path A leaves the more durable, visible record; Path B gives a typed answer. A hybrid also works: Path B, with the coordinator writing a card for the record.

### Path D (Kent's recorded decision N3): Romulus as a Naive app with an MCP endpoint
- **The decision** (`docs/naive/company-setup.md:58`, steps at lines 64–67):
  - The Romulus control plane runs as a Naive app.
  - Every agent that can reach it gets `romulus.<tool>` tools (`list_incidents`, `rollback`, `set_mode`, `record_heal`), under the usual allow / ask / deny rules.
  - The bench pushes heartbeats and incidents to it over HTTPS.
- **The docs confirm how it works** (N:14467 onward):
  - Setting `mcp` mints a `VETTA_MCP_TOKEN`, and the platform registers the app's tools as `<app-name>.<tool>`.
  - The token never reaches the sandbox, and an app that's down only removes its tools for that turn.
- **The return path:** the manager writes the fix through a typed tool such as `romulus.record_heal(...)`, and the device reads it from the app it already talks to *(inferred)*.
- **It works alongside Path B,** so which one is the record is your call.
- **Limit:** this needs Romulus hosted on Naive. The laptop Romulus on Ethernet (Olir's `server/cloud_up.sh`) can't be reached from Naive.

### Path C (works tonight): Hermes on the bench as the manager
- **Already in place:**
  - The dispatcher runs `hermes chat --oneshot -t so101_arm,mcp-naive_board -q …` (`dispatch.py:68`).
  - A `naive_board` filter exposes only the five card tools (`README.md:36`).
- **Several agents locally:** `delegate_task(tasks=[…])` runs up to 10 subagents in parallel, each with an optional `output_schema`. The parent checks each answer and allows one correction (H:19187, H:19199).
- **Running Hermes's model through Naive** *(inferred, untested)*:
  - Hermes accepts any OpenAI-compatible endpoint (H:440). Point it at `https://api.vetta.sh/v1/proxy/openai/v1` with `openai/gpt-oss-120b`.
  - **Risk 1:** if Hermes sends `tool_choice`, `response_format`, `top_p` or `seed`, the proxy returns `400` (N:9821).
  - **Risk 2:** calls made this way are limited only by the org balance (N:9900).
  - Today bench Hermes uses an OpenAI key (`features.txt:97`).
- **Hermes has its own Kanban board** (H:19950). Keep Naive's board as the single record (`docs/cross-reference.md`) *(inferred)*.

## 4. Limits

| Limit | Value | Source |
|---|---|---|
| Sessions running at once, per org | 50; extra ones queue | N:9475 |
| Request rate | 6,000/min per org, 3,000/min per key; 429 with Retry-After | N:9474 |
| Roster size / delegation depth | 1–20 members / 1 level | N:31962, N:31650 |
| Members delegated at once | capped; the number isn't published | N:31649 |
| Handoffs | 4 deep, 8 per session | N:13954 |
| Cards | body up to 20,000 characters; one in progress per agent per board; at most 3 starts | N:2363, N:31014, N:2245 |
| Board sweep | once a minute | N:30999 |
| Cron | at most every 5 minutes | N:13008 |
| Incoming email wake-ups | 5 an hour per sender, 20 an hour per inbox | N:23872 |
| MCP tool result | 8,000 characters | N:7596 |
| Webhooks and agents' MCP URLs | public HTTPS only | N:14548, N:14415 |
| Reaching into a sandbox | not possible | N:20674 |
| Hermes harness | no approval tools, no typed output, no tool events; not runnable on our org | N:21598, N:21543, N:21661, `company-setup.md:36` |
| Org state (Sat 3 PM, stale) | $249.92 balance; Pro plan set to cancel Dec 1; only agent is the CEO | `company-setup.md:27-29` |

## 5. Proposed manager roles on a Naive team *(inferred unless cited)*

**Which board:**
- ARM JOB cards stay on the company board (`brd_v2zc…`, `prototypes/arm-sim/armsim/board.py:34`).
- Incidents go on `incident-lead`'s own board, where its roster members sit (N:30966).
- `fleet-ops` stays off every roster, so it sits on the company board (`cloud-platform-naive-integration.md:124`).

| Role | Naive agent | Harness | What it returns | Tools / policy | Budget idea |
|---|---|---|---|---|---|
| Manager / triage | `incident-lead` (coordinator, board on, `handoffs` set to the member list, or `team` once the roster is saved) | `pi` now; `hermes` once runnable, at the cost of Path B's typed answer | FixPlan (`output_schema` on the session or agent) | team tools; everything else denied by default | $5/day, $0.50/task |
| Diagnose | `diagnoser` | `pi` | `{root_cause, fault_class, confidence, evidence}` | none, or a read-only Romulus tool | $3/day, $0.30/task |
| Plan the fix | `fix-planner` | `pi` | `{action from the allowlist, args, touches_safety, verify}` | none | $3/day, $0.30/task |
| Safety review | `safety-reviewer` | `pi` | `{approve, reasons}`; flags `touches_safety`, and Romulus's gate runs the simulated canary (K17; no Naive agent can run it) | none | $2/day |
| Code or firmware patch (optional) | `patcher` | `hermes` once runnable | untyped; a diff in the note | its own CLI tools; no approval tools allowed | $5/day |
| Parts / work order | `fleet-ops` (top-level, not on the roster) | `pi` (`cloud-platform-naive-integration.md:122`) | `RESULT:` / `STOPPED:` note | Mobilerun MCP | existing plan |
| The worker's hands | process on the Pi or laptop; Hermes `so101-arm` plugin or a script | outside Naive | runs one allowed action, then `verify()` | plain-code gate | n/a |

**Commands** (syntax from N:31780–31782, N:19413, N:21020–21060):
```bash
naive agent create -f diagnoser.agent.yaml        # name, model, harness: pi, system, tools, budget{cap_usd,max_task_usd,period}, handoffs
naive agent create -f fix-planner.agent.yaml
naive agent create -f safety-reviewer.agent.yaml
naive agent create -f incident-lead.agent.yaml    # handoffs: [diagnoser, fix-planner, safety-reviewer]  (NOT `team` yet: refused on an agent with no roster, N:13946)
naive team set agt_<incident-lead> --member diagnoser@1 --member fix-planner@1 --member safety-reviewer@1 --board
# optional afterwards: switch handoffs to `team` with an agent update, now that a roster exists
naive board create incident-lead                  # ensure its board (board commands accept the owner's name)
naive session run --agent agt_<incident-lead> --message "<test incident>" --output-schema fixplan.json --require-output --json | jq .structured_output
```

**What the coordinator's prompt should tell it:**
1. Ask `diagnoser`, then `fix-planner`, then `safety-reviewer`, each with `wait: true` and its own `output_schema`.
2. Write one card for the record.
3. Return the FixPlan with `submit_output`.
4. Never propose an action that isn't on the allowlist in the brief.

**Model:**
- `openai/gpt-oss-120b` is a valid model id (N:9722) and already works with our key.
- "ChatGPT 6 sol" (`docs/healer-data-levels.md:20`) isn't in the Naive docs, so check `GET /v1/models` first.
- Kent's earlier plan was `anthropic/claude-sonnet-5.5` at $10/day (`company-setup.md:57`).

## 6. Not confirmed, and the call that settles each

1. **Is Hermes runnable now?** `GET /v1/harnesses`.
2. **Does a session started by a card pick up the agent's `output_schema`?** I'm inferring it by joining N:13671 and N:30999. Test it with `GET /v1/sessions/{woken_session_id}`.
3. **Can a card on the coordinator's own board be assigned to the coordinator?** Try `naive board add incident-lead --assignee incident-lead`.
4. **How fast does an API-created card start its session?** The docs say within the request, but Kent's spike for this was never run (`cloud-platform-naive-integration.md:173`).
5. **Do the MCP tools `sessions_get` and `boards_*` exist under those names?** Run `tools/list` with the minted token.
6. **Does Hermes work against the Naive proxy?** Run one `hermes chat -q` and watch for a `400` naming `tool_choice` or `response_format`.
7. **Is "ChatGPT 6 sol" in the model list, and with which windows?** `GET /v1/models`.
8. **What are the org's balance and plan now?** `naive credits`.