# Hermes as a Naïve harness: what works, what's missing

- **Why:** we want Hermes as the agent loop, with `pi` as the fallback, and after the hackathon James wants to improve the Hermes harness. This note checks Kent's "Hermes isn't runnable" claim against Naïve's docs and lists the gaps.
- **Sources:** Naïve docs `concepts/harnesses`, `concepts/harness-capabilities`, `api/harnesses`; Hermes docs `developer-guide/programmatic-integration`, `reference/cli-commands`, `user-guide/security`. All are in `docs/raw/`. *(inferred)* marks our own reading.

## 1. "Published" vs "runnable", in plain words

- **One service, several deployments.** Naïve runs several copies of its service: production (`api.vetta.sh`), staging, and private deployments. Each copy is a *deployment*.
- **Two things a harness needs:**
  - **An adapter compiled into the server.** That makes it *published*: it appears in the catalogue and a picker can show it.
  - **A machine image, for loops that run as a program in a micro-VM** (`hermes`, `claude_code`). The image is built separately in each deployment's cloud account. That makes it *runnable* on that deployment.
- **Analogy:** an item printed on the menu (published) that this branch's kitchen can't cook yet (not runnable).
- **The API answers for the deployment you call:** `GET /v1/harnesses` returns `runnable: true|false`. Starting a session on a non-runnable harness fails up front with **501**, not mid-turn.
- **Per the docs, `pi` is always runnable** and needs no image. `hermes` "needs an image".

**Kent's claim:** at 3:00 PM he read `GET /v1/harnesses` live on our org and got `hermes: runnable: false` and `pi: runnable: true` (`docs/naive/company-setup.md`). That's a direct API reading, and it fits the docs. It's still a snapshot, so re-check before relying on it:

```bash
curl https://api.vetta.sh/v1/harnesses -H "authorization: Bearer <your key or mcp_ token>"
```

If it's still `false`, ask the Naïve organizers to build the Hermes image for our deployment.

## 2. pi vs hermes inside Naïve

| | `pi` | `hermes` |
|---|---|---|
| Status | GA | preview |
| What it is | Naïve's built-in loop driving its sandbox tools | Hermes run **as a CLI process** in a micro-VM |
| Runnable on our deploy | yes | no (3 PM) |
| Approvals (`ask` tools) | yes | **no**: `ask` tools are left out, and a toolset with one is refused at start |
| Structured output | yes | **no** |
| Streaming (`message.delta`) | yes | **no**: one whole answer at the end |
| Tool events in the stream | yes | **no**: `tool.started/completed` aren't published for *any* tool on `hermes` (`claude_code` does publish them) |
| Naïve tools (team, Board, MCP, connections, apps) | handed in directly | yes, through a session-scoped tool endpoint ("you configure nothing") |
| Naïve built-ins (web search, browser, `publish_file`) | yes | **no**: Hermes uses its own web/file/search tools instead |
| `wait_for_agents` pauses the turn | yes | no (a few extra model calls, still correct) |

## 3. Where the gaps come from *(inferred)*

- **Naïve drives Hermes like a command line.** Its docs say the loop "re-emits one whole answer rather than a running transcript". That matches Hermes's **single-query mode** (`hermes chat -q`): it prints a final answer, and its approvals default to **deny** when nobody is attached.
- **Hermes can do more when driven another way.** Its docs list three protocols for driving it from another program:
  - **ACP** (JSON-RPC over stdio): streaming message chunks, **tool-call events**, **permission requests**, cancel.
  - **TUI gateway JSON-RPC**: sessions, approvals, streaming events, plus plugin RPC methods.
  - **API server** (HTTP + Server-Sent Events, OpenAI-compatible).
- **Most "missing" features already exist in Hermes;** the way Naïve calls it doesn't carry them.
  - **Hermes's one-shot mode does stream.** `hermes chat -q "..." --format stream-json` prints one JSON object per line: `system` (init), `text` (assistant text deltas), `tool_use`, `tool_result` (name, output, duration, error flag) and a final `result` (text, tokens, exit code) (`reference/cli-commands`, `docs/raw/hermes-llms-full.txt` L51674 and L51704–51729).
  - **So Naïve's adapter probably doesn't use it** *(inferred)*. Naïve's docs describe one whole answer and no tool events on `hermes`, yet `stream-json` would supply both. `claude_code`, whose CLI has a similar streaming JSON mode, does publish tool events.
  - **What `stream-json` lacks:** permission requests (there is no event for "may I run this?", so approvals still need ACP or the TUI gateway) and a schema-checked final answer.

## 4. The gaps, as things to build or ask for

| Gap on `hermes` | Who could close it | Idea |
|---|---|---|
| No streaming, no tool events | Naïve's adapter | Run Hermes with `--format stream-json` and map `text` → `message.delta`, `tool_use`/`tool_result` → `tool.started`/`tool.completed`. Hermes already emits these. |
| No approvals | Naïve adapter + Hermes ACP, or Hermes upstream | Drive Hermes over ACP and map its permission requests to Naïve tool confirmations, **or** add a permission-request event plus a way to answer it to `stream-json` |
| No structured output | Hermes | A one-shot "final answer must match this JSON schema" option (plugins already have `ctx.llm.complete_structured`) |
| Not runnable on our deploy | Naïve | Build the Hermes image for our deployment |
| No Naïve built-ins | Naïve | Publish built-ins over the session endpoint, or rely on Hermes's own web tools |

## 5. What this means for the hackathon

- **Company agents stay on `pi`:** it has approvals, streaming and everything else.
- **Hermes runs on the bench as a normal program,** not inside Naïve. It reaches the Board through Hermes's own **MCP client** pointed at Naïve's MCP endpoint, with a scoped token *(inferred: may need no plugin at all)*.
- **The plugin Hermes really needs is the arm bridge.** Hermes tools call one arm-host process that owns LeRobot and the USB port.
- **A fair pi-vs-Hermes comparison today:**
  - Give a Naïve `pi` agent (cloud) and bench Hermes the same job, and compare success, steps, cost and visibility.
  - Comparing both *inside* Naïve needs the Hermes image first.
- **After the hackathon:** streaming and tool events need only Naïve's adapter to switch to `stream-json`. The clearest Hermes contribution is approvals in that same stream: a permission-request event and a way to answer it, so any host, Naïve included, can gate risky tools without moving to ACP *(inferred)*.
