# Hermes Agent (Nous Research) — terminology

- **Sources:** https://hermes-agent.nousresearch.com/docs (index: `llms.txt`, full text: `llms-full.txt`, both in `docs/raw/`). Repo: https://github.com/NousResearch/hermes-agent (MIT).
- **Snapshot:** 2026-11-07. Refresh the dumps with `bash docs/raw/fetch.sh`.
- **Rules:** every definition comes from the linked page. Link labels are paths under `https://hermes-agent.nousresearch.com/docs/`. *(inferred)* marks anything I worked out myself rather than read.
- **Hackathon note:** plugin development is section 1 because the Plugin Hackathon is judged on the plugin itself (see [../hackathons.md](../hackathons.md)).

In one line, from the docs index: "The self-improving AI agent built by Nous Research. A terminal-native autonomous coding and task agent with persistent memory, agent-created skills, and a messaging gateway that lives on 21+ messaging platforms". ([llms.txt](https://hermes-agent.nousresearch.com/docs/llms.txt))

Contents:
1. [Plugin development (hackathon-critical)](#1-plugin-development-hackathon-critical)
2. [Core concepts](#2-core-concepts)
3. [Tools, toolsets, skills](#3-tools-toolsets-skills)
4. [Memory and context](#4-memory-and-context)
5. [Automation and multi-agent](#5-automation-and-multi-agent)
6. [Gateway, messaging, integrations](#6-gateway-messaging-integrations)
7. [Models and providers](#7-models-and-providers)
8. [Runtime, safety, secrets](#8-runtime-safety-secrets)
9. [Internals (developer guide)](#9-internals-developer-guide)
10. [Quick reference](#10-quick-reference)

---

## 1. Plugin development (hackathon-critical)

- **Plugin**: extends Hermes "with custom tools, hooks, and integrations". There are three places Hermes discovers plugins:
  - `~/.hermes/plugins/` (user)
  - `.hermes/plugins/` (project)
  - pip entry points

  ([user-guide/features/plugins](https://hermes-agent.nousresearch.com/docs/user-guide/features/plugins), [developer-guide/architecture](https://hermes-agent.nousresearch.com/docs/developer-guide/architecture))
- **Native plugin layout**: a `plugin.yaml` manifest plus an `__init__.py` that defines `register(ctx)`. The guide's calculator example has four files:
  - `plugin.yaml`: the manifest.
  - `__init__.py`: the wiring.
  - `schemas.py`: what the LLM reads.
  - `tools.py`: what runs.

  ([developer-guide/plugins](https://hermes-agent.nousresearch.com/docs/developer-guide/plugins))
- **Manifest** (`plugin.yaml`):
  - Required: `name`, `version`, `description`, `provides_tools`, `provides_hooks`.
  - Optional: `author`, `requires_env` (stops the plugin loading if the variables are missing), `capabilities`.
  - `provides_tools` doesn't decide what loads, but `hermes plugins validate` fails if it doesn't match what is actually registered, which blocks catalog admission. ([developer-guide/plugins](https://hermes-agent.nousresearch.com/docs/developer-guide/plugins))
- **Capabilities** (consent): privileged host features a plugin requests, which the user consents to once.
  - Known ids: `tools.override`, `llm.provider_override`, `llm.model_override`, `llm.agent_id_override`, `llm.profile_override`, `llm.task_override`, `llm.side_question`, `gateway.platform_actions`.
  - Anything undeclared stays off (fail closed). Check first with `ctx.has_capability(...)`.
  - The docs say this is "consent + audit, **not a sandbox**". ([developer-guide/plugins](https://hermes-agent.nousresearch.com/docs/developer-guide/plugins))
- **`PluginContext` (`ctx`)**: the object passed to `register(ctx)`. Commonly used methods (counted in the guide):
  - Registration: `ctx.register_tool`, `ctx.register_hook`, `ctx.register_command` (slash command), `ctx.register_cli_command`, `ctx.register_skill`, `ctx.register_middleware`, `ctx.register_platform`, `ctx.register_rpc`.
  - Doing things: `ctx.dispatch_tool`, `ctx.inject_message`, `ctx.call_mcp`, `ctx.get_config` / `ctx.set_config`.

  ([developer-guide/plugins](https://hermes-agent.nousresearch.com/docs/developer-guide/plugins))
- **Hooks**: run your code at lifecycle points. Most are "fire-and-forget observers"; a few can change what happens:
  - `pre_tool_call` can return `{"action": "block"}` to veto a call, or `{"action": "approve"}` to send it to the human-approval gate.
  - `pre_llm_call` can inject context.
  - Others: `post_tool_call`, `post_llm_call`, `pre_api_request` / `post_api_request` / `api_request_error`, `pre_auxiliary_call` / `post_auxiliary_call`, `on_session_start` / `on_session_end` / `on_session_finalize` / `on_session_reset` / `on_session_delete`, `post_compaction`, `gateway_platform_event`, `kanban_task_claimed` / `kanban_task_completed` / `kanban_task_blocked`.

  ([developer-guide/plugins](https://hermes-agent.nousresearch.com/docs/developer-guide/plugins), [user-guide/features/hooks](https://hermes-agent.nousresearch.com/docs/user-guide/features/hooks))
- **Middleware**: unlike hooks, "Middleware changes what happens".
  - Request middleware rewrites the payload; execution middleware wraps the call.
  - Register with `ctx.register_middleware("tool_request", fn)`. Other kinds include `llm_request`; the full list is `VALID_MIDDLEWARE`. ([developer-guide/middleware](https://hermes-agent.nousresearch.com/docs/developer-guide/middleware), [developer-guide/plugins](https://hermes-agent.nousresearch.com/docs/developer-guide/plugins))
- **Observer hooks**: "the read-only telemetry contract for plugins" (traces, metrics, audit, replay), with correlation IDs and sanitized payloads. ([developer-guide/observer-hooks](https://hermes-agent.nousresearch.com/docs/developer-guide/observer-hooks))
- **Plugin LLM access** (`ctx.llm`): "the supported way for a plugin to make an LLM call": chat or structured, sync or async. Hermes owns the credentials and the trust gate. The smallest call is `ctx.llm.complete(messages=[...])`. ([developer-guide/plugin-llm-access](https://hermes-agent.nousresearch.com/docs/developer-guide/plugin-llm-access))
- **Public plugin API**: the Hermes modules a plugin may import. `hermes plugins validate` warns about any other Hermes import (catalog rule 9). The list is generated from `hermes_cli/data/plugin_api.json`. ([developer-guide/plugins/public-api](https://hermes-agent.nousresearch.com/docs/developer-guide/plugins/public-api))
- **Application declarations**: lets a plugin whose MCP server fronts a desktop app declare that app (`app:` per OS, plus `requires:` with `app`, `min_version` or `gpu`). Hermes only offers the server's tools when the app is present. ([developer-guide/plugins/application-declarations](https://hermes-agent.nousresearch.com/docs/developer-guide/plugins/application-declarations))
- **Portable Agent Plugins v1** (`plugin.json`): a cross-tool package format holding `skills/*/SKILL.md` and stdio MCP servers in `mcp.json`. Hermes loads it through a compatibility adapter. It stays disabled after install until you run `hermes plugins enable`. ([developer-guide/plugins](https://hermes-agent.nousresearch.com/docs/developer-guide/plugins))
- **Plugin entrypoints the catalog accepts**: `__init__.py` (Python), `desktop/plugin.js` (Desktop), `plugin.json` (portable) and `dashboard/manifest.json` (web dashboard). ([developer-guide/plugins/catalog-submission](https://hermes-agent.nousresearch.com/docs/developer-guide/plugins/catalog-submission))
- **Plugin types that plug into a backend**: there is one guide per backend type, each with its own `ctx.register_*` call where one exists.

  | Type | What it adds | Guide |
  |---|---|---|
  | Model provider | An LLM backend | [developer-guide/model-provider-plugin](https://hermes-agent.nousresearch.com/docs/developer-guide/model-provider-plugin) |
  | Memory provider | A cross-session knowledge backend; only one can be active | [developer-guide/memory-provider-plugin](https://hermes-agent.nousresearch.com/docs/developer-guide/memory-provider-plugin) |
  | Context engine | Replaces the built-in `ContextCompressor`; only one can be active | [developer-guide/context-engine-plugin](https://hermes-agent.nousresearch.com/docs/developer-guide/context-engine-plugin) |
  | Delegation provider | Where subagents run | [developer-guide/delegation-provider-plugin](https://hermes-agent.nousresearch.com/docs/developer-guide/delegation-provider-plugin) |
  | Browser provider | A cloud browser backend | [developer-guide/browser-provider-plugin](https://hermes-agent.nousresearch.com/docs/developer-guide/browser-provider-plugin) |
  | Browser vault | A password manager via `ctx.register_login_backend`; vendor or Nous only | [developer-guide/browser-vault-plugin](https://hermes-agent.nousresearch.com/docs/developer-guide/browser-vault-plugin) |
  | Image generation | An image backend | [developer-guide/image-gen-provider-plugin](https://hermes-agent.nousresearch.com/docs/developer-guide/image-gen-provider-plugin) |
  | Video generation | A video backend | [developer-guide/video-gen-provider-plugin](https://hermes-agent.nousresearch.com/docs/developer-guide/video-gen-provider-plugin) |
  | Web search | A search, extract or crawl backend | [developer-guide/web-search-provider-plugin](https://hermes-agent.nousresearch.com/docs/developer-guide/web-search-provider-plugin) |
  | Terminal environment | Where commands run | [developer-guide/terminal-environment-plugin](https://hermes-agent.nousresearch.com/docs/developer-guide/terminal-environment-plugin) |
  | Secret source | A secret-manager backend | [developer-guide/secret-source-plugin](https://hermes-agent.nousresearch.com/docs/developer-guide/secret-source-plugin) |
  | Platform | A gateway messaging channel | [developer-guide/adding-platform-adapters](https://hermes-agent.nousresearch.com/docs/developer-guide/adding-platform-adapters) |

- **Desktop Plugin SDK** (`@hermes/plugin-sdk`): extends the native Hermes Desktop app with panes, pages, sidebar navigation, status bar, palette commands, keybinds, themes, and a scoped backend namespace, "with one import and no build step". ([developer-guide/desktop-plugin-sdk](https://hermes-agent.nousresearch.com/docs/developer-guide/desktop-plugin-sdk))
- **Dashboard extension**: themes and plugins for the web dashboard (custom tabs, slots, backend API routes). ([user-guide/features/extending-the-dashboard](https://hermes-agent.nousresearch.com/docs/user-guide/features/extending-the-dashboard))
- **Built-in plugins**: plugins shipped with Hermes that run automatically through lifecycle hooks, for example disk-cleanup. ([user-guide/features/built-in-plugins](https://hermes-agent.nousresearch.com/docs/user-guide/features/built-in-plugins))
- **Plugin Catalog / Hermes Marketplace**:
  - The catalog is "a human-reviewed directory of Hermes plugins"; each entry is a YAML file in `plugin-catalog/`.
  - Entry fields: `name`, `description`, `repo`, `sha` (exact 40-hex commit), `subdir`, `tier` (`official` / `verified` / `community`), `category`, `maintainer`, `capabilities`, `requires_hermes`, `platforms`, `title`, `version`.
  - The hackathon post calls the place users install from the "Hermes Marketplace". ([user-guide/features/plugin-catalog](https://hermes-agent.nousresearch.com/docs/user-guide/features/plugin-catalog), [developer-guide/plugins/catalog-submission](https://hermes-agent.nousresearch.com/docs/developer-guide/plugins/catalog-submission))
- **Plugin CLI**:
  - `hermes plugins validate <path> --install-deps`: the check catalog CI runs.
  - `hermes plugins doctor <path> --ci`: runs Hermes's own loader and reports drift between what is declared and what is registered.
  - `hermes plugins install|list|enable|update <name>`.

  ([developer-guide/plugins](https://hermes-agent.nousresearch.com/docs/developer-guide/plugins), [developer-guide/plugins/catalog-submission](https://hermes-agent.nousresearch.com/docs/developer-guide/plugins/catalog-submission))
- **Admission rules worth memorising**: SHA pin, no self-updating code, owner submits, declared capabilities match reality, no runtime overrides of core, secrets via `agent.secret_scope.get_secret(NAME)`, respect approvals, disclose risky behaviour, `requires_hermes` is a SemVer floor, no binaries. The full checklist is in [../hackathons.md](../hackathons.md). ([developer-guide/plugins/catalog-submission](https://hermes-agent.nousresearch.com/docs/developer-guide/plugins/catalog-submission))

## 2. Core concepts

- **`AIAgent`**: the one agent class behind every entry point (CLI, gateway, ACP, batch, API server); the loop lives in `agent/conversation_loop.py`. ([developer-guide/architecture](https://hermes-agent.nousresearch.com/docs/developer-guide/architecture))
- **Entry points**: the CLI (`cli.py`), the TUI, the gateway (`gateway/run.py`), ACP (`acp_adapter/`), the batch runner, the API server, the Python library, and Hermes Desktop. ([developer-guide/architecture](https://hermes-agent.nousresearch.com/docs/developer-guide/architecture), [user-guide/desktop](https://hermes-agent.nousresearch.com/docs/user-guide/desktop))
- **CLI / TUI**:
  - The classic terminal interface: [user-guide/cli](https://hermes-agent.nousresearch.com/docs/user-guide/cli).
  - The "modern terminal UI", mouse-friendly and with non-blocking input: [user-guide/tui](https://hermes-agent.nousresearch.com/docs/user-guide/tui).
- **Session**: "Session persistence, resume, search, management, and per-platform session tracking". Stored in SQLite with FTS5 full-text search, and tracks lineage (parent and child sessions across compressions). ([user-guide/sessions](https://hermes-agent.nousresearch.com/docs/user-guide/sessions), [developer-guide/architecture](https://hermes-agent.nousresearch.com/docs/developer-guide/architecture))
- **Profile** (`hermes -p <name>`): "a separate Hermes home directory" with its own `config.yaml`, `.env`, `SOUL.md`, memories, sessions, skills, cron jobs and state database. Profiles run side by side. ([user-guide/profiles](https://hermes-agent.nousresearch.com/docs/user-guide/profiles))
- **`HERMES_HOME`**: the profile-aware home directory, `~/.hermes` by default. ([developer-guide/architecture](https://hermes-agent.nousresearch.com/docs/developer-guide/architecture))
- **`config.yaml`**: the main configuration file (providers, models, API keys and more). ([user-guide/configuration](https://hermes-agent.nousresearch.com/docs/user-guide/configuration))
- **The agent's files** ([user-guide/which-file-does-what](https://hermes-agent.nousresearch.com/docs/user-guide/which-file-does-what)):
  - `SOUL.md`: the agent's primary identity and personality. You write it; it is slot 1 of the system prompt.
  - `USER.md`: a profile of you. The agent writes it with the `memory` tool.
  - `MEMORY.md`: the agent's own notes. The agent writes it with the `memory` tool.
  - `AGENTS.md`: project instructions loaded from the working directory.
  - `.hermes.md` / `HERMES.md`: Hermes-specific project instructions, which take priority over `AGENTS.md`.
- **Personality**: customize Hermes with a global `SOUL.md`, built-in personalities, and custom personas. ([user-guide/features/personality](https://hermes-agent.nousresearch.com/docs/user-guide/features/personality))
- **Context files**: project files (`.hermes.md`, `AGENTS.md`, `CLAUDE.md`, global `SOUL.md`, `.cursorrules`) injected automatically into every conversation. ([user-guide/features/context-files](https://hermes-agent.nousresearch.com/docs/user-guide/features/context-files))
- **Context references**: inline `@` syntax for attaching files, folders, git diffs and URLs to a message. ([user-guide/features/context-references](https://hermes-agent.nousresearch.com/docs/user-guide/features/context-references))
- **Bot Mode**: turns profiles into "a roster of named Bots — each with its own chat, role, model, memory, skills, and avatar". ([user-guide/bot-mode](https://hermes-agent.nousresearch.com/docs/user-guide/bot-mode))
- **Hermes Desktop**: the native app for macOS, Windows and Linux. ([user-guide/desktop](https://hermes-agent.nousresearch.com/docs/user-guide/desktop))
- **Web Dashboard**: a browser-based admin panel for config, keys, MCP servers, gateway, memory, sessions, cron and skills. ([user-guide/features/web-dashboard](https://hermes-agent.nousresearch.com/docs/user-guide/features/web-dashboard))

## 3. Tools, toolsets, skills

- **Tool registry**: `tools/registry.py`, with "70+ registered tools across ~28 toolsets". Each tool file registers itself when imported (`registry.register()`). ([developer-guide/architecture](https://hermes-agent.nousresearch.com/docs/developer-guide/architecture))
- **Toolset**: "named bundles of tools that control what the agent can do". Every tool belongs to exactly one toolset. The kinds are core (for example `file` = `read_file`, `write_file`, `patch`, `search_files`), composite, platform and dynamic. ([reference/toolsets-reference](https://hermes-agent.nousresearch.com/docs/reference/toolsets-reference))
- **Tools reference**: the authoritative list of built-in tools, grouped by toolset. ([reference/tools-reference](https://hermes-agent.nousresearch.com/docs/reference/tools-reference))
- **Tool Search**: helps the agent find tools when there are many. ([user-guide/features/tool-search](https://hermes-agent.nousresearch.com/docs/user-guide/features/tool-search)) *(the definition is inferred from the page title)*
- **Code execution** (`execute_code`): runs Python programmatically with RPC access to tools, to "collapse multi-step workflows into a single turn". ([user-guide/features/code-execution](https://hermes-agent.nousresearch.com/docs/user-guide/features/code-execution))
- **Browser** (`browser_exec`): drives a real browser (Chromium that Hermes launches, cloud browsers, Lightpanda, or your own Chrome over CDP). ([user-guide/features/browser](https://hermes-agent.nousresearch.com/docs/user-guide/features/browser))
- **Web search & extract** (`web_search`, `web_extract`): multiple backends, including self-hosted SearXNG. ([user-guide/features/web-search](https://hermes-agent.nousresearch.com/docs/user-guide/features/web-search))
- **MCP**: connects external tool servers and controls which of their tools load. The model sees them named `mcp__<server>__<tool>`. ([user-guide/features/mcp](https://hermes-agent.nousresearch.com/docs/user-guide/features/mcp), [developer-guide/plugins](https://hermes-agent.nousresearch.com/docs/developer-guide/plugins))
- **Nous Tool Gateway**: "One subscription, every tool": web search, image generation, TTS and cloud browsers, all routed through Nous Portal. ([user-guide/features/tool-gateway](https://hermes-agent.nousresearch.com/docs/user-guide/features/tool-gateway))
- **Skill**: an "on-demand knowledge document" loaded with **progressive disclosure**, compatible with the agentskills.io standard.
  - Skills live in `~/.hermes/skills/`, and the agent can create, edit and delete them.
  - Format: `SKILL.md`. ([user-guide/features/skills](https://hermes-agent.nousresearch.com/docs/user-guide/features/skills), [developer-guide/creating-skills](https://hermes-agent.nousresearch.com/docs/developer-guide/creating-skills))
- **Skills Hub**: the `/skills` slash command and the place skills are installed from. ([user-guide/features/skills](https://hermes-agent.nousresearch.com/docs/user-guide/features/skills))
- **Bundled and optional skills**: about 90 skills ship with Hermes, and about 60 more can be installed. ([reference/skills-catalog](https://hermes-agent.nousresearch.com/docs/reference/skills-catalog), [reference/optional-skills-catalog](https://hermes-agent.nousresearch.com/docs/reference/optional-skills-catalog))
- **Curator**: "Background maintenance for agent-created skills": tracks usage and staleness, archives old skills, and runs LLM-driven reviews. ([user-guide/features/curator](https://hermes-agent.nousresearch.com/docs/user-guide/features/curator))

## 4. Memory and context

- **Memory**: how Hermes remembers across sessions, using `MEMORY.md`, `USER.md` and session search. ([user-guide/features/memory](https://hermes-agent.nousresearch.com/docs/user-guide/features/memory))
- **Memory providers**: external memory plugins (Honcho, OpenViking, Mem0, Hindsight, Holographic, RetainDB, ByteRover, Supermemory). Only one can be active. ([user-guide/features/memory-providers](https://hermes-agent.nousresearch.com/docs/user-guide/features/memory-providers))
- **Honcho**: "AI-native persistent memory": dialectic reasoning and modelling the user across agents. ([user-guide/features/honcho](https://hermes-agent.nousresearch.com/docs/user-guide/features/honcho))
- **Prompt assembly**: system-prompt tiers in order, `stable` → `context` → `volatile`. "Prompt stability" means the system prompt doesn't change mid-conversation. ([developer-guide/prompt-assembly](https://hermes-agent.nousresearch.com/docs/developer-guide/prompt-assembly), [developer-guide/architecture](https://hermes-agent.nousresearch.com/docs/developer-guide/architecture))
- **Context compression & caching**: summarizes the middle of a conversation when context passes a threshold, and sets Anthropic prompt-cache breakpoints. ([developer-guide/context-compression-and-caching](https://hermes-agent.nousresearch.com/docs/developer-guide/context-compression-and-caching))
- **Micro-compaction**: spreads the cost of compression across turns (triggers, tail sizing, and a trade-off with the cached prefix). ([developer-guide/micro-compaction](https://hermes-agent.nousresearch.com/docs/developer-guide/micro-compaction))
- **Checkpoints & rollback**: "Filesystem safety nets for destructive operations using shadow git repos and automatic snapshots". ([user-guide/checkpoints-and-rollback](https://hermes-agent.nousresearch.com/docs/user-guide/checkpoints-and-rollback))

## 5. Automation and multi-agent

- **Cron jobs**: "Schedule automated tasks with natural language", manage them with one cron tool, and attach skills. They are full agent tasks, not shell tasks. ([user-guide/features/cron](https://hermes-agent.nousresearch.com/docs/user-guide/features/cron), [developer-guide/architecture](https://hermes-agent.nousresearch.com/docs/developer-guide/architecture))
- **Chronos**: the wire contract for the managed-cron provider. ([developer-guide/chronos-managed-cron-contract](https://hermes-agent.nousresearch.com/docs/developer-guide/chronos-managed-cron-contract))
- **Delegation** (`delegate_task`): spawns child `AIAgent`s with isolated context and their own terminal sessions; only each child's final summary returns to the parent. ([user-guide/features/delegation](https://hermes-agent.nousresearch.com/docs/user-guide/features/delegation))
- **Subagent lifecycle API**: the public API for subagent lifecycles. ([developer-guide/subagent-lifecycle-api](https://hermes-agent.nousresearch.com/docs/developer-guide/subagent-lifecycle-api))
- **Kanban**: a "Durable SQLite-backed task board for coordinating multiple Hermes profiles". It emits `kanban_task_*` hooks. ([user-guide/features/kanban](https://hermes-agent.nousresearch.com/docs/user-guide/features/kanban))
- **Persistent goals**: "Set a standing goal and let Hermes keep working across turns until it's done" (their take on the Ralph loop). ([user-guide/features/goals](https://hermes-agent.nousresearch.com/docs/user-guide/features/goals))
- **Recurring loops**: re-run a prompt on an interval inside the session. ([user-guide/features/loops](https://hermes-agent.nousresearch.com/docs/user-guide/features/loops))
- **Session heartbeats**: a recurring prompt that comes back into the current session whenever it is idle (`/heartbeat every 10m …`). ([user-guide/features/heartbeat](https://hermes-agent.nousresearch.com/docs/user-guide/features/heartbeat))
- **Mixture of Agents (MoA)**: named presets that show up as selectable models. ([user-guide/features/mixture-of-agents](https://hermes-agent.nousresearch.com/docs/user-guide/features/mixture-of-agents))
- **Batch processing / trajectories**: generates agent trajectories at scale. They are saved in ShareGPT format for training. ([user-guide/features/batch-processing](https://hermes-agent.nousresearch.com/docs/user-guide/features/batch-processing), [developer-guide/trajectory-format](https://hermes-agent.nousresearch.com/docs/developer-guide/trajectory-format))

## 6. Gateway, messaging, integrations

- **Messaging gateway**: a long-running process with 25+ platform adapters. It handles session routing, user authorization (allowlists plus DM pairing), slash commands, hooks and cron ticks. ([developer-guide/architecture](https://hermes-agent.nousresearch.com/docs/developer-guide/architecture), [developer-guide/gateway-internals](https://hermes-agent.nousresearch.com/docs/developer-guide/gateway-internals))
- **Messaging platforms**: Telegram, Discord, Slack, WhatsApp, Signal, SMS, Email, Matrix, Teams, webhooks and more. ([user-guide/messaging](https://hermes-agent.nousresearch.com/docs/user-guide/messaging))
- **Platform adapter**: how a new gateway channel is added; bundled platforms live in `plugins/platforms/`. ([developer-guide/adding-platform-adapters](https://hermes-agent.nousresearch.com/docs/developer-guide/adding-platform-adapters))
- **API server**: "Expose hermes-agent as an OpenAI-compatible API for any frontend". ([user-guide/features/api-server](https://hermes-agent.nousresearch.com/docs/user-guide/features/api-server))
- **ACP (Agent Context Protocol)**: lets Hermes run inside editors (VS Code, Zed, JetBrains) over stdio and JSON-RPC. ([user-guide/features/acp](https://hermes-agent.nousresearch.com/docs/user-guide/features/acp))
- **Programmatic integration**: "Three protocols for driving hermes-agent from external programs: ACP, the TUI gateway JSON-RPC, and the OpenAI-compatible HTTP API". ([developer-guide/programmatic-integration](https://hermes-agent.nousresearch.com/docs/developer-guide/programmatic-integration))
- **Python library**: use Hermes as a library. ([guides/python-library](https://hermes-agent.nousresearch.com/docs/guides/python-library))

## 7. Models and providers

- **Providers**: Nous Portal, OpenRouter, OpenAI, Anthropic, Google, or "any OpenAI-compatible endpoint". ([integrations/providers](https://hermes-agent.nousresearch.com/docs/integrations/providers), [llms.txt](https://hermes-agent.nousresearch.com/docs/llms.txt))
- **Nous Portal**: "One subscription, 300+ frontier models, and the Tool Gateway — the recommended way to run Hermes Agent". ([integrations/nous-portal](https://hermes-agent.nousresearch.com/docs/integrations/nous-portal))
- **Provider runtime / API modes**: the resolver maps `(provider, model)` to `(api_mode, api_key, base_url)`. There are three API modes: `chat_completions`, `codex_responses` and `anthropic_messages`. ([developer-guide/provider-runtime](https://hermes-agent.nousresearch.com/docs/developer-guide/provider-runtime), [developer-guide/architecture](https://hermes-agent.nousresearch.com/docs/developer-guide/architecture))
- **Provider routing**: OpenRouter preferences for cost, speed or quality. ([user-guide/features/provider-routing](https://hermes-agent.nousresearch.com/docs/user-guide/features/provider-routing))
- **Fallback providers**: automatic failover to backup providers. ([user-guide/features/fallback-providers](https://hermes-agent.nousresearch.com/docs/user-guide/features/fallback-providers))
- **Credential pools**: several keys or OAuth tokens per provider, rotated automatically when you hit rate limits. ([user-guide/features/credential-pools](https://hermes-agent.nousresearch.com/docs/user-guide/features/credential-pools))
- **Subscription proxy**: use a Nous Portal (or other OAuth) subscription as an OpenAI-compatible endpoint for other apps. ([user-guide/features/subscription-proxy](https://hermes-agent.nousresearch.com/docs/user-guide/features/subscription-proxy))
- **Auxiliary client**: a second LLM for side tasks (vision, summarization, titling, compression). It has its own `pre_auxiliary_call` hook. ([developer-guide/architecture](https://hermes-agent.nousresearch.com/docs/developer-guide/architecture), [developer-guide/plugins](https://hermes-agent.nousresearch.com/docs/developer-guide/plugins))

## 8. Runtime, safety, secrets

- **Terminal backends**: local, Docker, SSH, Daytona, Modal and Vercel Sandbox, plus plugin backends (for example Singularity). ([developer-guide/architecture](https://hermes-agent.nousresearch.com/docs/developer-guide/architecture), [user-guide/docker](https://hermes-agent.nousresearch.com/docs/user-guide/docker))
- **Security / dangerous-command approval**: approval of dangerous commands, user authorization, container isolation. ([user-guide/security](https://hermes-agent.nousresearch.com/docs/user-guide/security))
- **YOLO mode**: auto-approval, switched on with `hermes --yolo`, `/yolo` or `HERMES_YOLO_MODE=1`; shown as `⚠ YOLO`. ([reference/cli-symbols](https://hermes-agent.nousresearch.com/docs/reference/cli-symbols))
- **Egress proxy**: controls what the sandbox can reach on the network. `iron-proxy` injects credentials at the egress point. ([user-guide/egress](https://hermes-agent.nousresearch.com/docs/user-guide/egress))
- **Managed scope**: "Administrator-pinned, user-immutable config and secrets". ([user-guide/managed-scope](https://hermes-agent.nousresearch.com/docs/user-guide/managed-scope))
- **Secrets**: secret sources such as Bitwarden, 1Password and command helpers. ([user-guide/secrets](https://hermes-agent.nousresearch.com/docs/user-guide/secrets))
- **Passwords & Logins (credential vault)**: "The agent signs into sites, pays and fills addresses for you without ever seeing a password." ([user-guide/features/credential-vault](https://hermes-agent.nousresearch.com/docs/user-guide/features/credential-vault))
- **Git worktrees**: run several Hermes agents safely on one repository. ([user-guide/git-worktrees](https://hermes-agent.nousresearch.com/docs/user-guide/git-worktrees))

## 9. Internals (developer guide)

- **Agent loop**: walks through how `AIAgent` executes: API modes, tools, callbacks and fallback. ([developer-guide/agent-loop](https://hermes-agent.nousresearch.com/docs/developer-guide/agent-loop))
- **Tools runtime**: registry, toolsets, dispatch, and terminal environments. ([developer-guide/tools-runtime](https://hermes-agent.nousresearch.com/docs/developer-guide/tools-runtime))
- **Adding tools**: schemas, handlers, registration and toolsets. ([developer-guide/adding-tools](https://hermes-agent.nousresearch.com/docs/developer-guide/adding-tools))
- **Session storage**: the SQLite schema, FTS5 and session lineage. ([developer-guide/session-storage](https://hermes-agent.nousresearch.com/docs/developer-guide/session-storage))
- **Design principles**: prompt stability, observable execution, interruptible, platform-agnostic core, loose coupling, profile isolation. ([developer-guide/architecture](https://hermes-agent.nousresearch.com/docs/developer-guide/architecture))

## 10. Quick reference

- Install (Linux, macOS, WSL2): `curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh | bash` ([llms.txt](https://hermes-agent.nousresearch.com/docs/llms.txt))
- Reference pages:
  - CLI commands: [reference/cli-commands](https://hermes-agent.nousresearch.com/docs/reference/cli-commands)
  - Slash commands: [reference/slash-commands](https://hermes-agent.nousresearch.com/docs/reference/slash-commands)
  - Environment variables: [reference/environment-variables](https://hermes-agent.nousresearch.com/docs/reference/environment-variables)
  - MCP config: [reference/mcp-config-reference](https://hermes-agent.nousresearch.com/docs/reference/mcp-config-reference)
  - FAQ: [reference/faq](https://hermes-agent.nousresearch.com/docs/reference/faq)
  - UI glyphs: [reference/cli-symbols](https://hermes-agent.nousresearch.com/docs/reference/cli-symbols)
- Plugin loop for the hackathon:
  1. Create `~/.hermes/plugins/<name>/` with `plugin.yaml` and `__init__.py`.
  2. Check it with `hermes plugins doctor . --ci`.
  3. Check it with `hermes plugins validate . --install-deps`.
  4. Open the catalog PR, post the demo on X, and submit the form. ([developer-guide/plugins](https://hermes-agent.nousresearch.com/docs/developer-guide/plugins))
