# Naïve ↔ Hermes — cross-reference

The same words mean different things in each product. Each cell is a short version of the matching entry in [naive/terminology.md](naive/terminology.md) or [hermes/terminology.md](hermes/terminology.md), where the source links are. *(inferred)* marks something I worked out myself rather than read in the docs.

Naïve can run Hermes as one of its harnesses (`harness: "hermes"`); see [Hermes inside Naïve](#hermes-inside-naïve-harness-hermes) below.

## Shared words

| Word | In Naïve | In Hermes | Watch out |
|---|---|---|---|
| **Agent** | A versioned *configuration* (model, harness, prompt, tools, skills, budget) that runs as sessions | The `AIAgent` program itself; a **profile** is one independent agent instance | Naïve agent ≈ a config record; Hermes agent ≈ a running program *(inferred)* |
| **Harness / loop** | A field you choose: `pi`, `vetta`, `claude_code`, `hermes`. It can only be set at creation | Hermes *is* a loop (`agent/conversation_loop.py`) | On Naïve, Hermes is one harness option |
| **Session** | One durable run: `queued`/`running`/`idle`/…, plus a `seq`-ordered event stream | A conversation saved in SQLite with FTS5 search, linked across compressions, tracked per platform | Different storage and lifecycle |
| **Skill** | Versioned expertise loaded by progressive disclosure (`read_skill`, `naive/slug`) | `SKILL.md` documents loaded by progressive disclosure (agentskills.io); the agent can create and edit them | On the `hermes` harness, Naïve's skill disclosure isn't offered |
| **Tools** | A toolset on the agent: `default_config` + `configs`, each `allow`/`ask`/`deny` | Toolsets are named bundles (core, composite, platform, dynamic); enabling one enables all its tools | Naïve's policy still filters Hermes's access to *Naïve* tools by name |
| **Approval** | `ask` → `tool.confirm` → the session idles at `awaiting_approval` | Dangerous-command approval; YOLO mode auto-approves | The `hermes` harness has `approvals: false`, so Naïve leaves `ask` tools out |
| **Memory** | No "Memory" page in the docs index. The nearest things are **project context**, the durable transcript and Files *(inferred)* | `MEMORY.md` / `USER.md` through the `memory` tool, plus memory-provider plugins | A memory-provider plugin is a valid Hermes hackathon entry |
| **Plugin** | No plugin system in the docs index. You extend Naïve through MCP servers, connections, apps and skills *(inferred)* | The extension system: `plugin.yaml` + `register(ctx)`, hooks, middleware, backend-provider plugins | The Hermes hackathon entry is a plugin |
| **MCP** | `mcp_servers` on the agent (`<server>.<tool>`). The Naïve API is itself available as an MCP server | An MCP client (`mcp__<server>__<tool>`); plugins can ship MCP servers | Both can talk to the same MCP server |
| **Multi-agent** | A **team**: coordinator + version-pinned roster. **Delegation** (`send_to_agent`, one level deep) plus the **board** (cards `todo`/`doing`/`blocked`/`done`) | `delegate_task` subagents; **Kanban** board across profiles; **Bot Mode** | Two different board systems: Naïve `board_write` vs Hermes Kanban |
| **Scheduling** | **Deployment** (cron) of an agent | **Cron jobs**, written in natural language and able to attach skills | |
| **Sandbox** | **Computer**: a Linux micro-VM with filesystem, shell, optional browser and mobile device; paused while idle | **Terminal backends**: local, Docker, SSH, Daytona, Modal, Vercel Sandbox, plugins | On Naïve, the Hermes process runs *on* the micro-VM |
| **Identity** | A **persona** that owns email, phone, domains, connections, vault, wallet and cards | Personality via `SOUL.md` and profiles; nothing like an inbox or phone in what I read *(inferred)* | Naïve's persona gives the agent real-world endpoints |
| **Secrets** | Credential vault, injected at the network boundary; the agent never sees the values | Secret sources (Bitwarden, 1Password, command helpers); `iron-proxy` injects credentials at egress; `agent.secret_scope.get_secret` for plugins | Same idea: inject at the boundary |
| **Webhooks** | Signed outbound deliveries of session and message events; inbound email and SMS wake the agent | A webhook messaging platform on the gateway | |
| **Cost** | A **budget** is required; every call is priced in micro-USD before it runs | Credential pools and provider routing manage keys and cost; no budget gate in what I read *(inferred)* | Naïve's cost metering still covers a `hermes` session |
| **Events / telemetry** | A `{domain}.{action}` SSE stream (`tool.started`, `message.completed`, …) | Hooks and observer hooks | On the `hermes` harness, Naïve has no `tool.*` events |

## Hermes inside Naïve (`harness: "hermes"`)

Everything in this section comes from Naïve's [concepts/harnesses](https://usenaive.ai/docs/concepts/harnesses) and [concepts/harness-capabilities](https://usenaive.ai/docs/concepts/harness-capabilities) pages unless it is marked *(inferred)*.

**What Hermes owns:** the turn loop and its **own** toolset (file, shell and search tools) inside the machine. The docs describe it as "an external coding agent, run as a process".

**What Naïve owns:** everything "beneath the harness":
- the durable loop (wake → turn → commit → sleep), the budget gate, the tool policy, the event log and the ledger;
- the micro-VM (`sandbox` execution; an idle session holds the machine);
- personas, connections, MCP servers, apps and team tools, which reach Hermes through a **session-scoped tool endpoint**. "You configure nothing."

**Status:** `preview`. Runnable only where a base image exists; check `GET /v1/harnesses` (`runnable`) before you rely on it.

**What you lose on `hermes`, compared with `pi`:**
- No `approvals`. Any `ask` tool is left out, and a session whose toolset contains an `ask` is **refused at start** (a 400 naming `harness`). Set `social.post` and `social.cancel` to `allow` if you need them.
- No `structured_output` and no `streaming_deltas`, so the answer arrives whole as `message.completed`.
- No Naïve built-ins: `web_search` / `web_fetch`, image and video generation, skill disclosure, `publish_file` and the managed browser are absent. Use Hermes's own tools instead.
- No `mobile` tool. It is on `pi` only, which matters for the Mobilerun track.
- `wait_for_agents` doesn't pause the turn. A coordinator parks once the turn goes idle, at the cost of a few extra model calls.
- Tool calls made over the endpoint **don't appear in the event stream** (no `tool.started` / `tool.completed`), though cost is still metered.

**What still works:** a `hermes` agent can be a team **member** or a **coordinator**, and teams can mix harnesses freely. For example, a `pi` coordinator could delegate to a `hermes` member.

**Not in either set of docs (test before you rely on it)** *(inferred)*:
- Whether your own Hermes plugins or `SOUL.md` load inside a Naïve-hosted `hermes` session. The Naïve docs only say Hermes runs from a base image.
- Which Hermes version Naïve runs.
