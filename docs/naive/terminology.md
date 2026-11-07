# Naïve (Naive Managed Agents) — terminology

- **Sources:** https://usenaive.ai/docs (index: `llms.txt`, full text: `llms-full.txt`, both in `docs/raw/`), plus the `usenaive` GitHub blueprint repos where marked **[GitHub]**.
- **Snapshot:** 2026-11-07. Refresh the dumps with `bash docs/raw/fetch.sh`.
- **Rules:** every definition comes from the linked page. Link labels are paths under `https://usenaive.ai/docs/`. *(inferred)* marks anything I worked out myself rather than read.
- **Naming:** the product is "Naive Managed Agents"; the hackathon page spells it "Naïve". The API, headers and older env vars use the name "Vetta" (see [Quick reference](#10-quick-reference-api--sdk--cli--mcp)).

Contents:
1. [Hackathon basics: blueprints, templates, companies](#1-hackathon-basics-blueprints-templates-companies)
2. [Agents, sessions, runtime](#2-agents-sessions-runtime)
3. [Harnesses](#3-harnesses)
4. [Inference, cost and policy](#4-inference-cost-and-policy)
5. [Team](#5-team)
6. [Computer](#6-computer)
7. [Identity](#7-identity)
8. [Capabilities and built-in tools](#8-capabilities-and-built-in-tools)
9. [Platform and billing](#9-platform-and-billing)
10. [Quick reference: API, SDK, CLI, MCP](#10-quick-reference-api--sdk--cli--mcp)

---

## 1. Hackathon basics: blueprints, templates, companies

The hackathon asks for "a company run by agents". In the docs, a company comes from a **blueprint** plus a **template**, applied with `naive up`.

- **Naive Managed Agents**: "a managed agent for long-horizon tasks, optimised for cost at every layer". You configure the agent: model, system prompt, tools, skills, budget, completion window and harness. Naive runs the three layers underneath (harness, tools, runtime). ([how-managed-agents-is-built](https://usenaive.ai/docs/how-managed-agents-is-built))
- **Blueprint**: "the machine: the repository, the apps it hosts and the routes they serve." ([concepts/templates](https://usenaive.ai/docs/concepts/templates))
- **Template**: "a crew with a first day and a memory of the business it runs — chosen from a blueprint, provisioned by an apply." A template is data inside a blueprint: which agents make up the crew, what each is told on day one, what it has been taught, what it may reach, and when it runs. To switch templates within one blueprint, edit the config and apply again; you never need a new app. ([concepts/templates](https://usenaive.ai/docs/concepts/templates))
- **Shipped blueprints and templates**:
  - From the docs: `web` (template `publisher`); `media` (templates `faceless`, `longform`, `clipping`); `support` (template `inbox`, no app). ([concepts/templates](https://usenaive.ai/docs/concepts/templates))
  - From GitHub: the `agency` blueprint, with templates `blank`, `seo-geo` and `product-design`. **[GitHub]** [agency-blueprint](https://github.com/usenaive/agency-blueprint)
  - To list everything published: `naive catalog`. ([cli/naive](https://usenaive.ai/docs/cli/naive))
- **Crew**: the agents a template staffs. In the agency blueprint, each one has a role, a private system prompt, a tool allow-list that denies by default, skills from the platform catalogue, a daily budget, and a first message that runs on day one. **[GitHub]** [agency-blueprint](https://github.com/usenaive/agency-blueprint)
- **Per-client crew** (`crew_per_client`): a crew the app provisions when you onboard a client. Its agent names carry the client's slug, so it can't be declared in `naive.config.ts`. **[GitHub]** [agency-blueprint](https://github.com/usenaive/agency-blueprint)
- **`naive.config.*`** (`naive.config.ts` / `naive.config.json`): the project declaration that `naive up` reads.
  - In the blueprint repos it is `defineProject({ name, blueprint, template, questions, identities, apps, … })` from the `@usenaive-sdk/blueprints` engine. **[GitHub]**
  - `naive init` scaffolds a starter one. ([cli/naive](https://usenaive.ai/docs/cli/naive))
- **Apply / install**:
  - `naive up` "reconcile[s] `naive.config.*` against the platform, record[s] the apply, and report[s] what changed".
  - Re-running it is idempotent, because resources are keyed by name.
  - Each apply is recorded as an install; `naive installs` lists them and `naive installs remove bpi_…` removes one.
  - Use `naive up --dry-run` to preview. ([cli/naive](https://usenaive.ai/docs/cli/naive))
- **`naive claim`**: binds a cloned blueprint repo to your organization by storing a verified API key. Pipe the key in with `naive claim < key.txt` to keep it out of your shell history. ([cli/naive](https://usenaive.ai/docs/cli/naive))
- **`naive template <blueprint> [--template <name>]`**: clones a published blueprint into its own directory. ([cli/naive](https://usenaive.ai/docs/cli/naive))
- **`{ from_env }`**: a config value read from your shell when you apply. If the variable is unset, the apply is refused by name, so no secret sits in the file. **[GitHub]** [agency-blueprint `naive.config.ts`](https://github.com/usenaive/agency-blueprint/blob/main/naive.config.ts)
- **Setup questions**: a template asks at most **four** before provisioning anything.
  - Field types are `text` and `choice`.
  - `optional: true` lets a question be left blank; its key is then absent from the answers.
  - `help` is the sentence shown under the label. ([concepts/templates](https://usenaive.ai/docs/concepts/templates))
- **Project context** (`project_context` tool): the setup answers together with their questions, the apps and their URLs, and the agents and their roles.
  - Every agent a template creates reads it through the read-only `project_context` tool.
  - Edit it with `naive installs context --set key=value`; the crew reads the new value the next time it looks, with no re-apply. ([concepts/templates](https://usenaive.ai/docs/concepts/templates))
- **Tasks (day one)**: a template's first day is a set of cards on the organization's shared board.
  - Each card has a title, a body, an `assignee` and the cards it is `blocked_by`.
  - The apply creates the board's owner (as `CEO`) if there isn't one.
  - A card with an assignee and no open blockers is picked up on the platform's next tick.
  - Re-applying finds the same cards and reports them as `unchanged`. ([concepts/templates](https://usenaive.ai/docs/concepts/templates))
- **Intake**: the old way to run day one: a private session for each agent instead of a shared card. It still works, but templates written today declare `tasks`. The first day is priced as "day one": the sum of the intake budgets. ([concepts/templates](https://usenaive.ai/docs/concepts/templates))
- **Selection** (`selection: { agents, apps }`): provisions only part of the crew.
  - From the CLI: `naive up --only editor,site` or `naive up --without analyst`.
  - Resources marked `required` can't be left out.
  - An existing agent left out of a selection keeps running and is reported as `deselected`. ([concepts/templates](https://usenaive.ai/docs/concepts/templates))
- **Skill catalogue**: skills the platform ships, versioned and immutable.
  - Reference one as `naive/slug` (the newest version, resolved when a session starts) or `naive/slug@N` (frozen).
  - A bare `slug` means one of your own organization's skills.
  - List the catalogue with `naive skill catalog`. ([concepts/templates](https://usenaive.ai/docs/concepts/templates))
- **Approvals (operator screen)**: in the agency blueprint, tools held at `ask` show up on **Approvals** before they run. On the platform, an `ask` emits `tool.confirm` and pauses the tool until someone approves or rejects it. **[GitHub]** + ([concepts/policies](https://usenaive.ai/docs/concepts/policies))
- **`ask_operator` / `request_tools`**: the agent's two ways to reach you. `ask_operator` asks a question and stops until you answer. `request_tools` proposes adding tools to the agent's own toolset and stops until approved. ([capabilities/tools](https://usenaive.ai/docs/capabilities/tools))
- **Mini apps**: the pages a company's template declares, read with live numbers. ([api/mini-apps](https://usenaive.ai/docs/api/mini-apps))
- **Digest**: the company's sessions and agents in one read: what is waiting on you, what failed, recent work, and each channel. ([api/digest](https://usenaive.ai/docs/api/digest))
- **CRM and Calendar**: a company's clients, their timelines, and its meetings; agents use `crm.read` / `crm.write`. ([api/crm](https://usenaive.ai/docs/api/crm))
- **Marketplace listing**: `list_as_template` lists the running company as a template other companies can start from. `pay_out_earnings` pays your share of the earnings to the company's Stripe account. Both "always ask". ([capabilities/tools](https://usenaive.ai/docs/capabilities/tools))
- **Studio**: the hackathon page says "Our studio lets you deploy and operate these systems"; you sign up at https://app.usenaive.ai/register. *(from the event page and site, not the docs)*

## 2. Agents, sessions, runtime

- **Agent**: "a reusable, versioned *configuration*" of model, system prompt, toolset, skills, a required budget, a default completion window, and the `harness` field. Every edit creates a new, unchangeable version. You define it once and run it as many sessions. ([capabilities/agent](https://usenaive.ai/docs/capabilities/agent))
- **Agent fields**:

  | Field | What it sets |
  |---|---|
  | `name`, `system` | Name and system prompt |
  | `model` | The model for each turn |
  | `harness` | The agent loop (default `pi`) |
  | `tools`, `mcp_servers` | What it may use, each `allow` / `ask` / `deny` |
  | `skills` | Expertise loaded on demand |
  | `budget` | **Required** spend cap |
  | `window` | Default completion window |
  | `output_schema` | JSON Schema the final result must match |

  ([capabilities/agent](https://usenaive.ai/docs/capabilities/agent))
- **Session**: "a single, directly-controlled run of an agent". It is a durable run that can pause for hours at storage cost and pick up where it left off. ([concepts/sessions](https://usenaive.ai/docs/concepts/sessions))
- **Session statuses**:
  - `queued` → `running` → `idle`. An idle session can be resumed and always carries a `stop_reason`.
  - `completed`: the agent declared the task done.
  - `failed` and `cancelled` are terminal. ([concepts/sessions](https://usenaive.ai/docs/concepts/sessions))
- **Stop reasons** (`stop_reason`):
  - `end_turn`: finished normally.
  - `awaiting_input`: waiting for your next message; nothing specific is owed.
  - `awaiting_approval`: waiting on a tool you set to `ask`.
  - `awaiting_answer`: the agent asked a question (`ask_operator`).
  - `awaiting_delegation`: a coordinator waiting on teammates it delegated to.
  - `budget_paused`: hit a budget cap, or the prepaid balance ran out.
  - `interrupted`: you interrupted the turn.
  - `max_iterations`: hit the step cap for one slice; send another message to continue.
  - `context_exhausted`: the turn produced neither a message nor a tool call.
  - `error`: an unrecoverable error.

  ([concepts/sessions](https://usenaive.ai/docs/concepts/sessions))
- **Runtime**: Naive's metered infrastructure, "not a setting". It covers the durable loop and scheduler, sandboxed computers and the managed browser, model routing, and the ledger. ([concepts/runtime](https://usenaive.ai/docs/concepts/runtime))
- **Wake → turn → commit → sleep**: the durable loop "is an **alarm**, not a call stack".
  1. **Wake** on a trigger: a message, a schedule, a tool result, or an inbound email or SMS.
  2. Take **one** bounded turn.
  3. **Commit** the transcript and cursor.
  4. **Sleep.**

  A crash loses at most one turn. ([concepts/runtime](https://usenaive.ai/docs/concepts/runtime))
- **Bounded slice**: each turn runs within a step budget, a wall-clock budget and the spend budget. When it hits one, the runtime saves a checkpoint and schedules the next slice. ([concepts/runtime](https://usenaive.ai/docs/concepts/runtime))
- **Idle costs storage, not compute**: between turns there is no process running. A paused micro-VM is charged for its stored disk and no vCPU. ([concepts/runtime](https://usenaive.ai/docs/concepts/runtime))
- **Events**:
  - Every session has an ordered stream named `{domain}.{action}`.
  - Each event has a `seq` number, so you can resume with `after_seq`; it is delivered as Server-Sent Events.
  - Common types: `session.running`, `session.idle`, `session.usage`, `message.delta`, `message.completed`, `tool.started`, `tool.confirm`, `tool.completed`, `budget.exceeded`. ([concepts/events-and-streaming](https://usenaive.ai/docs/concepts/events-and-streaming))
- **Session operations**: send input, interrupt, confirm a tool (`naive session confirm --allow|--deny`), cancel, archive and delete. ([concepts/session-operations](https://usenaive.ai/docs/concepts/session-operations))
- **Awaiting-approval loop / awaiting-answer loop**: how you drive a session that is waiting on a tool approval (`tool.confirm`) or on an answer to an `ask_operator` question (a `pending_actions` entry with `kind: "question"`). ([concepts/session-operations](https://usenaive.ai/docs/concepts/session-operations))
- **Plan mode**: a chat writes a Markdown plan with `write_plan`, then asks the operator with `propose_plan` ("Build it" or "Keep planning"). ([capabilities/tools](https://usenaive.ai/docs/capabilities/tools))
- **Deployment (cron)**: "Run an agent on a schedule, then collect the results of every fire." ([capabilities/deployments](https://usenaive.ai/docs/capabilities/deployments))
- **Webhooks**: subscribe to session and message events, and receive inbound email and SMS. ([capabilities/webhooks](https://usenaive.ai/docs/capabilities/webhooks))
- **Structured outputs**: "Constrain a session's final result to a JSON Schema and read it back as typed data", using `output_schema` / `structured_output_required`. ([capabilities/structured-outputs](https://usenaive.ai/docs/capabilities/structured-outputs))
- **Outcome / grader** (`ocm_…`): "Score a session against a rubric with a model-driven grader loop." ([capabilities/outcomes](https://usenaive.ai/docs/capabilities/outcomes))
- **Observability**: cost and token usage for each session, a usage event before every idle, and a full trace of every turn, tool call and model request. ([capabilities/observability](https://usenaive.ai/docs/capabilities/observability))

## 3. Harnesses

- **Harness**: "the agent loop itself: assemble a turn, call the model, parse the tool calls, decide what carries into the next turn". It is the one layer you choose, as a single field on the agent, and it **can only be set at creation** (a `PATCH` that includes it is ignored). To move to a different loop, create a new agent. ([concepts/harnesses](https://usenaive.ai/docs/concepts/harnesses))
- **The four loops**:

  | `harness` | Status | Execution | What an idle session holds | Approvals | Typed output | Streaming |
  |---|---|---|---|---|---|---|
  | `pi` (default) | `ga` | `sandbox` | a micro-VM | yes | yes | yes |
  | `vetta` | `preview` | `isolate` | nothing | yes | declared but not served | no |
  | `claude_code` | `preview` | `sandbox` | a micro-VM | no | no | no |
  | `hermes` | `preview` | `sandbox` | a micro-VM | no | no | no |

  Typed output is `structured_output` and streaming is `streaming_deltas`. All four can be a team member or a coordinator. Only `pi` has the `mobile` tool. ([concepts/harness-capabilities](https://usenaive.ai/docs/concepts/harness-capabilities))
- **Execution**:
  - `isolate`: the loop runs inside the session itself, so an idle session costs storage only.
  - `sandbox`: the agent runs as a process on a micro-VM, and the session holds that machine between turns.
  - `local`: listed, but not available to a hosted session. ([concepts/harnesses](https://usenaive.ai/docs/concepts/harnesses))
- **Runnable**: whether a harness can start on this deployment. `claude_code` and `hermes` "need an image"; `GET /v1/harnesses` (`client.harnesses.list()`) is the authoritative answer. ([concepts/harness-capabilities](https://usenaive.ai/docs/concepts/harness-capabilities))
- **Capabilities**:
  - `approvals`: the loop can hold a tool call open while it waits for a person.
  - `structured_output`: the loop can enforce typed results.
  - `streaming_deltas`: the loop emits `message.delta` events.
  - `injected_tools`: the platform's tools reach the loop's model.

  If a session asks for a capability its harness lacks, it is **refused at start** with a `400` naming `harness`, never quietly downgraded. ([concepts/harnesses](https://usenaive.ai/docs/concepts/harnesses))
- **Session-scoped tool endpoint ("the second road")**: `claude_code` and `hermes` run as CLI processes with **their own** toolsets. At every turn, Naive gives them an endpoint, scoped to that session, for calling Naive's team, MCP, connection, social and app tools. **Naive's built-ins don't travel this road**: web search and fetch, image and video generation, skill disclosure, `publish_file`, and the managed browser. ([concepts/harness-capabilities](https://usenaive.ai/docs/concepts/harness-capabilities))
- **Limits for `hermes` specifically**: this is the link to the Nous hackathon; see [../cross-reference.md](../cross-reference.md). ([concepts/harness-capabilities](https://usenaive.ai/docs/concepts/harness-capabilities))
  - Tools set to `ask` are left out entirely, and a session whose toolset contains an `ask` is refused at start.
  - `social.post` and `social.cancel` default to `ask`, so they are only offered if you set them to `allow` by name.
  - `wait_for_agents` doesn't pause the turn.
  - Its tool calls don't appear in the event stream (no `tool.started` / `tool.completed`), though cost is still metered.

## 4. Inference, cost and policy

- **Model router**: how each model call is routed to inference, what the completion window changes, and how tokens are metered. The model catalogue is read live (`GET /v1/models`), "never a fixed list". ([concepts/model-router](https://usenaive.ai/docs/concepts/model-router), [api/models](https://usenaive.ai/docs/api/models))
- **Model auto-selection**: "Let an evaluation model choose which of your models answers a conversation." ([concepts/model-selection](https://usenaive.ai/docs/concepts/model-selection))
- **Completion window** (`window`): three prices for the same model, chosen per request.
  - `immediate` (the default): answers now, highest price.
  - `priority`: answers soon, lower price.
  - `loose`: answers eventually, lowest price.
  - Precedence: `session.window`, then `agent.window`, then the org default.
  - Only a few models serve `priority` and `loose`. Asking for them on any other model gives `window_unavailable` (400). ([concepts/completion-window](https://usenaive.ai/docs/concepts/completion-window))
- **Budget**: required on every agent.
  - `cap_micro_usd` is the cap per period, `max_task_micro_usd` the ceiling per task, and `period` is `day` / `week` / `month`.
  - Every call is priced in advance (a **quote**) and refused if it would break a cap. The checks run in this order: org balance, agent cap, task ceiling, session budget.
  - A refusal emits `budget.exceeded`, and the session goes to `budget_paused`; raising the cap resumes it. ([concepts/budgets](https://usenaive.ai/docs/concepts/budgets))
- **Micro-USD**: every amount is an integer number of micro-USD (`1 USD = 1,000,000`), in fields ending `_micro_usd`. The CLI's `--budget-usd` converts for you. ([concepts/budgets](https://usenaive.ai/docs/concepts/budgets))
- **Five-tier token ledger**: model spend is split into `input`, `cache_write`, `cache_read`, `output` and `reasoning`. Other cost components are `computer`, `search` and `media`. ([concepts/budgets](https://usenaive.ai/docs/concepts/budgets))
- **`session_spend`**: a built-in tool that reads the calling session's own ledger. It is read-only and free. ([capabilities/tools](https://usenaive.ai/docs/capabilities/tools))
- **Policy**: governance at the tool-call boundary. Each tool resolves to:
  - `allow`: runs without asking.
  - `ask`: emits `tool.confirm` and pauses the tool until someone decides.
  - `deny`: never offered to the model.

  Policy lives on the agent's `tools` toolset, as a `default_config` plus per-tool `configs`. There is no organization-wide policy layer. ([concepts/policies](https://usenaive.ai/docs/concepts/policies))
- **Tool naming in policies**: an MCP tool is `<server>.<tool>`, a connection tool is `<connector>.<tool>` (for example `crm.create_contact`), and a built-in uses its plain name. ([concepts/policies](https://usenaive.ai/docs/concepts/policies))
- **Ask-by-default tools**: tools that publish, pay, file, change company settings, run paid data jobs, make videos or train characters resolve to `ask` unless you name them in `configs`. ([concepts/policies](https://usenaive.ai/docs/concepts/policies))
- **Pricing**: "What you pay, and how the completion window changes it." ([platform/pricing](https://usenaive.ai/docs/platform/pricing))

## 5. Team

- **Team**: an agent whose config carries a `multiagent` object; there is no separate "team" object. That agent is the **coordinator**, and it works with a **roster** of member agents, each running in its own session thread. ([team/overview](https://usenaive.ai/docs/team/overview))
- **Coordinator** (`multiagent.type = "coordinator"`): owns the top-level task and context. Any published harness can be one. ([team/coordinator](https://usenaive.ai/docs/team/coordinator), [team/overview](https://usenaive.ai/docs/team/overview))
- **Roster / version pinning**: members are pinned by version (`{ type: "agent", id: "agt_…", version: 3 }`; the CLI accepts `name@version`), so team behaviour can be reproduced. Teams may mix harnesses. ([team/rosters-and-versioning](https://usenaive.ai/docs/team/rosters-and-versioning))
- **Delegation**: point-to-point and one-off.
  - `send_to_agent` with `wait: true`: the member works in its own session, and its answer comes back into the coordinator's transcript as a tool result.
  - Delegation is **one level deep**: a member's `send_to_agent` refuses `wait: true`.
  - `wait_for_agents` and `list_agents` complete the set. ([team/delegation](https://usenaive.ai/docs/team/delegation))
- **Handoff**: `send_to_agent` with `wait: false` starts a new top-level session of the target agent, on the target's own budget. Who an agent may name is set by its `handoffs`. ([capabilities/tools](https://usenaive.ai/docs/capabilities/tools))
- **Company board**: the long-lived, many-to-many way a team coordinates.
  - One board per coordinator, shared by the roster, switched on with `board: true`.
  - Tools: `board_read` and `board_write`, with ops `create`, `update`, `assign` and `comment`.
  - Card statuses: `todo`, `doing`, `blocked`, `done`.
  - Cards carry title, notes, status, assignee and `blocked_by`.
  - **Assigning a card starts the assignee** in a session of its own. The coordinator gets one digest per board listing cards that reached `done` or `blocked`. ([team/board](https://usenaive.ai/docs/team/board), [team/overview](https://usenaive.ai/docs/team/overview))
- **The pattern the docs recommend**: "the coordinator puts the plan on the board and assigns each card; the board starts the crew." ([team/overview](https://usenaive.ai/docs/team/overview))
- **Context & budgets**: what threads share and keep private, how spend flows across a team, and the events that cross threads. ([team/context-and-budgets](https://usenaive.ai/docs/team/context-and-budgets))
- **`advisor` roster entry**: `{ type: "advisor", model }` is accepted by the parser but refused at save ("not yet served"). ([concepts/harness-capabilities](https://usenaive.ai/docs/concepts/harness-capabilities))
- CLI: `naive team set <agent> --member name@ver --board`, and `naive board` to read and move cards. ([cli/team](https://usenaive.ai/docs/cli/team), [cli/board](https://usenaive.ai/docs/cli/board))

## 6. Computer

- **Computer** (`cmp_…`): Naive's managed sandbox, "a disposable Linux micro-VM with a filesystem, a shell, and an optional browser, paused when the agent sleeps". An agent config has no computer: a session takes one the first time a turn uses the shell or the filesystem. ([capabilities/computer](https://usenaive.ai/docs/capabilities/computer), [concepts/sessions](https://usenaive.ai/docs/concepts/sessions))
- **Computer lifecycle**:
  - `creating`, then `running`, which is billed.
  - `paused`: disk kept. The sandbox page says it "bills nothing"; the runtime page says it is charged for stored disk.
  - `failed` and `destroyed` are terminal. ([computer/sandbox](https://usenaive.ai/docs/computer/sandbox))
- **Filesystem**: read, write, list and manage files through five governed operations; `read` and `write` are limited to 4 MiB per call. ([computer/filesystem](https://usenaive.ai/docs/computer/filesystem))
- **Shell** (`bash` tool): runs a command and returns the buffered stdout, stderr and exit code. ([computer/shell](https://usenaive.ai/docs/computer/shell))
- **Browser** (`browser` tool): an optional managed browser, limited to set domains, with write guards. Actions: `goto`, `click`, `type`, `act`, `extract`, `observe`, `links`, `screenshot`, `login`, `signup`, `save_context`. ([computer/browser](https://usenaive.ai/docs/computer/browser), [capabilities/tools](https://usenaive.ai/docs/capabilities/tools))
- **Mobile device** (`mobile` tool): a cloud Android phone or iPhone. Actions: `screenshot`, `element_tree`, `tap`, `type`, `press_key`, `scroll`, `open_url`, `launch_app`, `install_app`, `end`. One phone per session; it **asks by default** and works on `pi` only. ([computer/mobile](https://usenaive.ai/docs/computer/mobile), [capabilities/tools](https://usenaive.ai/docs/capabilities/tools))
- **Snapshots**: save a checkpoint of a running VM and start new computers from it. "Persistent volumes are not available." ([computer/snapshots-and-volumes](https://usenaive.ai/docs/computer/snapshots-and-volumes))
- **Networking**: outbound access from the sandbox; locking down egress is "coming". ([computer/networking](https://usenaive.ai/docs/computer/networking))

## 7. Identity

- **Identity / persona** (`idn_…`): "An addressable persona an agent acts as". It owns endpoints (email, phone, social accounts, web domains, third-party connections) and a credential vault, and it attaches to agents ("the agent grant"). ([capabilities/identity](https://usenaive.ai/docs/capabilities/identity), [identity/personas](https://usenaive.ai/docs/identity/personas))
- **Why the persona matters**: identity tools resolve `session → agent → identity → {inboxes, connected accounts}`. An agent with no persona is offered nothing from a mailbox or a connected account. **[GitHub]** [agency-blueprint](https://github.com/usenaive/agency-blueprint)
- **Credential vault**: "Secrets injected at the network boundary — the agent never sees raw values." The API only lets you write them. ([identity/vault](https://usenaive.ai/docs/identity/vault), [api/vaults](https://usenaive.ai/docs/api/vaults))
- **Identity policies**: identity tools use the same per-tool `allow` / `ask` / `deny` as every other tool. ([identity/policies](https://usenaive.ai/docs/identity/policies))
- **Web domains**: "dual-track": email and app verification are independent, there are three kinds of registrar, and email has readiness checks. There is also an organization "system domain": `naive identity domain connect --kind system --receive`. ([identity/domains](https://usenaive.ai/docs/identity/domains), [concepts/templates](https://usenaive.ai/docs/concepts/templates))
- **Email**: inboxes on a verified domain. Sending mail is checked against credits, and inbound mail wakes the agent. ([identity/email](https://usenaive.ai/docs/identity/email))
- **Phone**: numbers on carrier-registered messaging, for sending and receiving SMS. ([identity/phone](https://usenaive.ai/docs/identity/phone))
- **Inbound events**: inbound email or SMS wakes the agent that owns the inbox or number, behind guards, and is treated as untrusted. ([identity/inbound](https://usenaive.ai/docs/identity/inbound))
- **Connections**: let an agent act through third-party apps (OAuth or API key) "without ever touching the token". The setup flow is the **Connect Link**. ([identity/connections](https://usenaive.ai/docs/identity/connections), [api/connections](https://usenaive.ai/docs/api/connections))
- **Social accounts**: connect them to an identity and publish under policy (`social.post`, `social.cancel`). ([identity/social](https://usenaive.ai/docs/identity/social))
- **Legal / KYC**: verify the people behind an identity, then form a company it can operate as. KYC is "the gate before a company can be formed". ([identity/legal](https://usenaive.ai/docs/identity/legal), [identity/kyc](https://usenaive.ai/docs/identity/kyc))
- **Wallet**: "A custodied, spend-limited wallet" in USDC. It can pay for paywalled resources via `x402`. ([identity/wallet](https://usenaive.ai/docs/identity/wallet), [api/wallet](https://usenaive.ai/docs/api/wallet))
- **Cards**: prepaid virtual cards bought with organization credits, with "a single audited credential reveal". ([identity/cards](https://usenaive.ai/docs/identity/cards))
- **Trading**: connect an identity's own brokerage account (paper or live), "with a person approving each order". ([identity/trading](https://usenaive.ai/docs/identity/trading))

## 8. Capabilities and built-in tools

- **Skills**: "Persisted, versioned expertise loaded with progressive disclosure". The agent pulls a skill body into context with `read_skill`. ([capabilities/skills](https://usenaive.ai/docs/capabilities/skills))
- **Toolset**: what the agent may use (built-ins, connection tools and MCP servers), each gated by a permission. ([capabilities/tools](https://usenaive.ai/docs/capabilities/tools))
- **Built-in tools you'll reach for**:
  - Files and shell: `bash`, `read`, `write`, `edit`, `ls`, `find`.
  - Web: `web_search`, `web_fetch` (priced per call).
  - Generation: `generate_image`, `generate_video`, `clip_video`.
  - Files library: `publish_file`, `fetch_file`, `find_files`, `view_image`.
  - Data catalogue: `data_search`, `data_describe`, `data_run`.
  - In-chat views: `compose`, `show`, `open_panel`.
  - Hosted apps: `apps`.
  - Operator: `ask_operator`, `request_tools`.
  - Team: `send_to_agent`, `board_write`.

  ([capabilities/tools](https://usenaive.ai/docs/capabilities/tools))
- **Data**: read data across 18 domains (people, companies, funding, SEO, news, law, jobs and more), priced per run. ([capabilities/data](https://usenaive.ai/docs/capabilities/data))
- **Social data**: public posts, profiles and trends on about 21 networks, with no connected account needed. ([capabilities/social-data](https://usenaive.ai/docs/capabilities/social-data))
- **Files** (`fil_…`): "Durable, organization-scoped storage for the artifacts a session produces"; the asset library. ([capabilities/files](https://usenaive.ai/docs/capabilities/files))
- **Media**:
  - Images, video, clips, audio (speech-to-text, text-to-speech, speech-to-speech) and stock photos.
  - **Characters** (`chr_…`) are trained, reusable on-screen personas.
  - **Media skills** turn 1–3 public short videos into a skill. ([api/media](https://usenaive.ai/docs/api/media), [api/characters](https://usenaive.ai/docs/api/characters), [api/media-skills](https://usenaive.ai/docs/api/media-skills))
- **3D assets**: generate a textured 3D model from a description or photos, or build a dimensioned part in code. ([capabilities/3d](https://usenaive.ai/docs/capabilities/3d))
- **Apps**: hosted web apps, covering provisioning, deployments, secrets, custom domains, the app database, and the app's own MCP endpoint. **Fullstack** apps add a managed **database** and object **storage**. ([api/apps](https://usenaive.ai/docs/api/apps), [api/database](https://usenaive.ai/docs/api/database), [api/storage](https://usenaive.ai/docs/api/storage))
- **Paid advertising**: create and run paid campaigns through a connected advertiser account. It is in early access and turned on per organization, and campaigns start paused. ([api/ads](https://usenaive.ai/docs/api/ads), [sdk/ads](https://usenaive.ai/docs/sdk/ads))
- **Reviews**: "Record a person's approve, reject or edit of agent work given outside the approval box." ([api/reviews](https://usenaive.ai/docs/api/reviews))

## 9. Platform and billing

- **Organization**: "The organization is the billing entity; users are members." ([platform/organizations](https://usenaive.ai/docs/platform/organizations))
- **Credits**: "A real US-dollar balance, topped up through the payments provider, metered against vendor cost." ([platform/billing](https://usenaive.ai/docs/platform/billing))
- **API keys**: scoped to the organization, sent with the Bearer scheme; create, rotate and revoke them with `naive keys`. ([api/authentication](https://usenaive.ai/docs/api/authentication), [cli/keys](https://usenaive.ai/docs/cli/keys))
- **Audit logs**: "A queryable, principal-attributed record of control-plane actions." ([api/audit-logs](https://usenaive.ai/docs/api/audit-logs))

## 10. Quick reference: API · SDK · CLI · MCP

**REST API** ([api/overview](https://usenaive.ai/docs/api/overview))
- Base URL: `https://api.vetta.sh`, path version `/v1`.
- Date version: the `Vetta-Version: YYYY-MM-DD` header. If you leave it out you get the current version, `2026-09-30`.
- Auth: an `authorization: Bearer $NAIVE_API_KEY` header.
- Lists use cursor pagination ([api/pagination](https://usenaive.ai/docs/api/pagination)). Errors come back in a typed envelope ([api/errors](https://usenaive.ai/docs/api/errors)).

**TypeScript SDK** ([sdk/typescript](https://usenaive.ai/docs/sdk/typescript))
- Install with `npm i @usenaive-sdk/managed-agents`. It is ESM-only, and its one runtime dependency is `zod`.
- Create a client: `createClient({ baseUrl: "https://api.vetta.sh", apiKey })`.
- It sends `Vetta-Version` and an `Idempotency-Key` on every write. It has no retry policy and no auto-paging iterator. It throws `ApiError`.
- Stream events with `client.stream(sessionId, { afterSeq })`. ([sdk/streaming](https://usenaive.ai/docs/sdk/streaming))
- Groups: `client.agents`, `.sessions`, `.harnesses`, `.models`, `.board`, `.proxy`, `.computers`, `.browserSessions`, `.mobileDevices`, `.skills`, `.outcomes`, `.crm`, `.files`, `.apps`, `.storage`, `.database`, `.identities`, `.connections`, `.vaults`, `.domains`, `.emails`, `.phones`, `.ads`, `.trading`, `.deployments`, `.webhooks`, `.auth`, `.orgs`, `.miniApps`, `.digest`, `.keys`, `.credits`, `.auditLogs`, `.media`, `.characters`, `.data`, `.socialData`. (Section `sdk/*` of `docs/raw/naive-llms.txt`.)

**CLI: `naive`** ([cli/overview](https://usenaive.ai/docs/cli/overview))
- Install with `npm i -g @usenaive-sdk/cli`. The agency blueprint README's badge names the package `@usenaive-sdk/vetta-cli` **[GitHub]**.
- Auth: `naive login` (pipe the key on stdin) and `naive whoami`.
- Env var: `NAIVE_API_KEY`. The older `VETTA_API_KEY` is read only if `NAIVE_API_KEY` is unset.
- Output is `--json` by default.
- Useful commands:
  - `naive run --agent … --message … --window priority`: one-off run.
  - `naive agent apply -f ./agents/`: GitOps, `*.agent.yaml`.
  - `naive session create|stream|events|confirm|budget`.
  - `naive agent create|spend`, `naive team set`, `naive skill catalog`.
  - Blueprints: `naive claim|up|init|template|catalog|installs`.
- Command groups: `agent`, `team`, `board`, `models`, `media`, `character`, `data`, `social-data`, `session`, `computer`, `browser`, `mobile`, `skill`, `outcome`, `file`, `deploy`, `webhook`, `app`, `storage`, `db`, `org`, `keys`, `plan`, `credits`, `audit`, `identity`, `trading`, `vault`. (Section `cli/*` of `docs/raw/naive-llms.txt`.)

**MCP and proxy**
- **MCP server**: "The Naive Managed Agents API published to a model as an MCP tool catalog, generated from the route table." ([api/mcp](https://usenaive.ai/docs/api/mcp))
- **MCP sessions**: "short-lived, revocable, least-privilege credentials for the Naive MCP server". ([api/mcp-sessions](https://usenaive.ai/docs/api/mcp-sessions))
- **Model proxy**: point an Anthropic- or OpenAI-compatible SDK at Naive, using your API key and your organization's balance. ([api/proxy](https://usenaive.ai/docs/api/proxy))

**ID prefixes seen in the docs:** `agt_` agent · `ses_` session · `evt_` event · `cmp_` computer · `idn_` identity · `fil_` file · `crd_` board card · `chr_` character · `ocm_` outcome · `bpi_` install · `app_` app · `sk_live_` API key.

**"Vetta"** *(inferred)*: the API host (`api.vetta.sh`), the headers (`Vetta-Version`, `Vetta-Deprecation`), the legacy env var (`VETTA_API_KEY`), the blueprint app token (`VETTA_MCP_TOKEN`) and the `vetta` harness all use this name, and the docs are also served at `vetta.sh/docs`. It looks like the platform's earlier or internal name.
