# Naïve company: setup, state and decisions

- **Snapshot:** Sat Nov 7, 3:00 PM PST. Live facts were read from the Naive API through the MCP at that time; doc facts link their page.
- **Owner:** Kent. Update the **Decisions** table when a decision changes.

## 1. Connect your Claude Code to the Naive MCP

Each person uses their **own** token. The token is a bearer for the whole account, so keep it out of the repo: use `local` scope (stored in `~/.claude.json`), never `.mcp.json`.

```bash
# 1. Get a token (signed in as yourself). It lasts 90 days and has plain-member scopes.
naive mcp create --name "<your-name> claude code"     # prints mcp_… once; copy it
# 2. Register it for this repo only
claude mcp add --scope local --transport http naive https://api.vetta.sh/v1/mcp \
  --header "Authorization: Bearer mcp_…"
# 3. Restart Claude Code. The tools load at session start.
```

- **What you get:** two tools, `tools_search` (find any API operation by describing it) and `tools_execute` (run it with `writes: true|false`). Tools marked dangerous ask you first. ([api/mcp-sessions](https://usenaive.ai/docs/api/mcp-sessions), [cli/mcp-sessions](https://usenaive.ai/docs/cli/mcp-sessions))
- **Tool results are cut at 8,000 characters.** For big reads (an agent's system prompt, a session's events), call the REST route instead, with the same bearer: `curl https://api.vetta.sh/v1/agents/<id> -H "authorization: Bearer mcp_…"`. ([api/mcp](https://usenaive.ai/docs/api/mcp))
- **Missing scopes:** a member token has no `webhooks:*` scope (a call returns `403`).

## 2. What the org holds (live)

| Item | State |
|---|---|
| Plan | Pro, active, **set to cancel at period end** (Dec 1), so it won't renew at $200 |
| Balance | **$249.92** ($1.08 spent so far, all by the CEO) |
| Agents | `CEO` only (`agt_j2jy5qp878yhfhqwpxd200ay12`): `pi` harness, `anthropic/claude-opus-5.5`, `immediate` window, caps of $50 a month and $5 a task |
| Board | `brd_v2zcnyzc559hv8awzc4hqvgcsm`, one card: `crd_px969fbze2p5fhh0ywczxzc5ap` (specs for Operator, Insights and Roboticist) |
| Templates, apps, identities, deployments | none |

## 3. Findings that change the plan

- **Why the CEO's hires failed.** The CEO session logged `session.degraded: mcp_server_unavailable` and then got "Tool vetta.agents_create not found" for each hire. At that point the org had no plan and $1, and every route outside billing needs a plan ([platform/pricing](https://usenaive.ai/docs/platform/pricing)). The org now has Pro and $249.92, and the API answers *(cause inferred)*.
- **Hermes can't run hosted.** `GET /v1/harnesses` reports `hermes` and `claude_code` as `runnable: false` on this deploy; `pi` and `vetta` are runnable. Hermes stays on the bench.
- **Cheap windows exist on only three models.** `priority` and `loose` are served only by `zai-org/GLM-5.2-FP8`, `moonshotai/Kimi-K2.6` and `google/gemma-4-31B-it` (376 models in the catalogue). Any other model runs at `immediate` prices. ([concepts/completion-window](https://usenaive.ai/docs/concepts/completion-window))
- **CEO cost:** about $0.10–0.30 per Opus turn (from the session's `session.usage` events).
- **Templates would drain the balance.** The published templates declare caps of $155 to $15K a month (for example `agency/blank` at $13,020 and most `media/*` at $9,300). Don't install one without lowering its caps first.
- **The bench can use the board directly, with no tunnel.**
  - The board API is public: `boards_list_cards`, `boards_get_card`, `boards_patch_card`, `boards_create_card` and `boards_create_comment` (`agents:read` / `agents:write`).
  - An admin-minted MCP session limited to those tools is the bench's credential. It also works on the REST routes behind them, and on nothing else. ([api/mcp-sessions](https://usenaive.ai/docs/api/mcp-sessions))
  - This answers the team's open question about how the bench and the board connect: the bench pulls cards and needs nothing exposed *(inferred design)*.

```bash
naive mcp create --name bench \
  --scopes agents:read,agents:write \
  --tools boards_list_cards,boards_get_card,boards_patch_card,boards_create_comment,boards_create_card \
  --expires-in 172800        # 48 h, enough for the hackathon
```

## 4. Decisions

| # | Question | Decision | Date |
|---|---|---|---|
| N1 | Hire Operator, Insights and Roboticist now? | **Hold off.** Specs stay on `crd_px969…` until the control plane exists | Sat 3 PM (Kent) |
| N2 | Model and caps once hired | **Quality first:** all three on `anthropic/claude-sonnet-5.5` at $10 a day each | Sat 3 PM (Kent) |
| N3 | Where the Romulus control plane lives | **A Naive fullstack app with `mcp: "/mcp"`.** Naive holds the database and API and mints `VETTA_MCP_TOKEN`. Every agent gets `romulus.*` tools, and the bench pushes traces to it over HTTPS ([api/apps](https://usenaive.ai/docs/api/apps)) | Sat 3 PM (Kent) |

**Watch for N2** *(inferred)*: an agent that hits its daily cap goes to `budget_paused` and stops ([concepts/budgets](https://usenaive.ai/docs/concepts/budgets)). A Sonnet Operator on a 15-minute cron could stall overnight, before the morning tally of agent and human actions. Run its cron every 30 minutes, or check `agents_get_spend` before bed.

## 5. Next steps, in order

1. **Provision the control-plane app:** `apps_create {name: "romulus", type: "fullstack", mcp: "/mcp"}`. Its first tables are devices, heartbeats, incidents and releases.
2. **Build the app's `/mcp` tools** to match the card's specs, for example `fleet_health`, `list_incidents`, `rollback(device, rev)`, `set_mode(device, safe)` and `record_heal`. Keep them typed and reversible, as in the Romulus design rules.
3. **Point the bench at it.** It pushes heartbeats and incidents to the app's REST API, and pulls cards with the `bench` MCP session above.
4. **Hire the three agents** (N2 sizing) with `romulus.*` tools: reads `allow`, `rollback`/`set_mode` `ask`. Then add the Operator's cron.
5. **AgentMail for incident emails** (hero demo step 4). Whether its MCP takes a bearer key is still unconfirmed (see [../sponsors.md](../sponsors.md)). If it doesn't, wrap its REST API inside the `romulus` app's `/mcp`.
