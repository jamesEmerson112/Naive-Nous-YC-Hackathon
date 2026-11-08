# Setting up on Naïve vs RunPod

- **Why:** on RunPod we rent a machine and run any image we want. Naïve doesn't work that way, and this note explains the difference.
- **Sources:** Naïve docs `api/harnesses`, `concepts/harnesses`, `concepts/harness-capabilities`, `api/mcp`, `api/mcp-sessions`, `cli/overview`, all in `docs/raw/naive-llms-full.txt`. *(inferred)* marks our own reading.
- **Freshness:** checked Sat Nov 7, ~4:15 PM PST. The live `llms-full.txt` matched our local dump exactly.

## The one-line difference

- **RunPod is a machine room.** You pick a GPU and an image, and run anything.
- **Naïve is an office with fixed job roles.** You configure agents on top of a set list of agent loops (harnesses), and you can't add a loop or an image of your own.
  - **Why:** the harness list is "a constant of the deploy — a harness needs an adapter compiled in — so it does not change without a release" (`api/harnesses`).

| | RunPod | Naïve |
|---|---|---|
| Bring your own container | yes (templates) | **no**. Harness images are built by Naïve, separately in each environment |
| Pick the agent loop | n/a | `pi`, `vetta`, `claude_code` or `hermes`, one field per agent, fixed at creation |
| Whether it will start | when the GPU is in stock | `runnable` on `GET /v1/harnesses`. A harness with no image on this deploy fails with **501** at session start |
| GPUs | yes | no; the sandbox is a Linux micro-VM *(inferred: no GPU offered in `computer/*`)* |
| What you configure | image, GPU, volume, ports | agent: model, harness, tools policy, MCP servers, skills, budget, cron |
| Manage it from Claude | RunPod MCP plugin | Naïve MCP server (see below) or the `naive` CLI |

## The harnesses (`concepts/harness-capabilities`)

| | `pi` | `vetta` | `claude_code` | `hermes` |
|---|:-:|:-:|:-:|:-:|
| Status | ga | preview | preview | preview |
| Runnable | always | only for orgs it's opened to | needs an image | needs an image |
| Approvals (`ask` tools) | yes | yes | no | no |
| Structured output | yes | declared but not served | no | no |
| Streaming deltas | yes | no | no | no |
| Naïve's tools (team, Board, MCP, connections) | yes | yes | yes, over a session endpoint | yes, over a session endpoint |
| Naïve built-ins (web, `publish_file`, browser) | yes | yes | no | no |

- **Kent's 3 PM reading:** `hermes` was `runnable: false` on our org. Only the Naïve organizers can fix that, by building the Hermes image (`docs/naive/company-setup.md`).
- **Consequence:** our Hermes, with our arm plugin, runs on the bench (or RunPod) as a normal program. It reaches Naïve over MCP or the REST API. That's already the plan in `docs/hermes-harness-gaps.md`.

## Managing Naïve like we manage RunPod

- **CLI** (`cli/overview`):
  - Install with `npm i -g @usenaive-sdk/cli`.
  - Log in by piping a scoped key to `naive login`, or by setting `NAIVE_API_KEY`. There is no browser login.
  - Useful commands: `naive models`, `naive agent apply -f ./agents/` (agents as `.agent.yaml` files), and `naive run --agent … --message …`.
- **MCP server** (`api/mcp`):
  - It's at `POST https://api.vetta.sh/v1/mcp` and authenticates with `Authorization: Bearer sk_live_…` or a short-lived `mcp_…` session.
  - It serves 284 tools such as `agents_create`, `sessions_list_events` and `computers_exec_command`. Results are capped at 8,000 characters.
  - Key-minting and secret-accepting routes are withheld.
  - **Connecting Claude Code to it** would give us RunPod-plugin-style control of the org *(inferred)*.
  - A person can mint a 90-day "connected agent" credential for themselves (`api/mcp-sessions`).
- **Not on this Mac yet:** no `naive` CLI and no `NAIVE_API_KEY` (checked 4:15 PM).
