# Cloud platform: Naïve integration

- **Status:** researched and approved Sat Nov 7 (**D19**, and **D19b** for over-cap orders; [decision log](cloud-platform-decisions.md)).
- **Source:** the local Naïve docs dump `docs/raw/naive-llms-full.txt` (snapshot 2026-11-07; refresh with `bash docs/raw/fetch.sh`). Each citation below is a path under `https://usenaive.ai/docs/`. *(inferred)* marks our own reasoning; **ask** marks what the docs don't say.

---

## 1. Summary

1. **Outbound only.** The platform calls Naïve; Naïve never calls the platform.
   - Naïve agents run in Naïve's cloud, and a hosted session can't execute locally (concepts/harnesses).
   - MCP server URLs and webhook URLs must be **public HTTPS**. Loopback and private addresses are refused (capabilities/tools, capabilities/webhooks).
   - Our platform is on a laptop, so v0 needs no tunnel.
2. **Work orders go out as self-contained board cards.** The card carries everything the agent needs, and the agent replies only through the card's `note` and comments.
3. **Status comes back by polling.** There are no webhook events for board cards (api/webhooks). The platform polls each open card every 10–20 s.
4. **Spend caps live in our code, not in Naïve.**
   - Naïve policy decides allow, ask or deny per tool; it can't express "max $X per order" (concepts/policies).
   - Naïve's wallet and prepaid cards return `501` (identity/wallet, identity/cards).
   - This **corrects D11 and D12**, which assumed a Naïve "policy file" with spend caps. See §8.
5. **The crew is declared as config in the repo** (`naive/naive.config.json`, applied with `naive up`). The ops agent's role, tools and budget are versioned like everything else (cli/naive).

## 2. What the platform uses from Naïve

| Uses | Doesn't use (v0) |
|---|---|
| Board cards: create, read, comments (api/board) | Webhooks: they need a public URL and don't cover cards (api/webhooks) |
| The CEO's company board, via `ceo_agent_id` (api/organizations) | MCP into our platform: it needs a public HTTPS URL (capabilities/tools) |
| An API key with board scopes (api/authentication) | Mini apps: they read only platform data (api/mini-apps) |
| Session events, as optional evidence of the Mobilerun call (api/events) | Wallet and cards: `501` (identity/wallet, identity/cards) |
| The `naive` CLI with `naive.config.json` for the crew (cli/naive) | The TypeScript SDK: there's no Python package, so we use REST with `httpx` (sdk/typescript) |

## 3. Design

### 3.1 Work orders out

1. When `domain/triage.py` decides a work order is needed (v0 plan §7.5), the platform writes it to the `work_orders` table with status `pending`. This table is the outbox.
2. The worker (`workorders.sync()`) posts each pending order:
   - `POST https://api.vetta.sh/v1/boards/{ceo_agent_id}/cards`
   - headers: `Authorization: Bearer sk_live_…`, `Vetta-Version: 2026-09-30`, `Idempotency-Key: wo_<id>`
   - body: `{title, body, assignee: "fleet-ops"}`
   - The `Idempotency-Key` header works on every mutating POST and never expires (api/errors).
3. It stores `naive_card_id`, sets the status to `posted`, and writes the `workorder.card_posted` event.

**Why `Idempotency-Key` and not the card's `key` field** *(inferred)*:
- A card created with `key` is a "seed" left to the board's clock, which sweeps once a minute. That means up to a minute's delay on stage.
- A card without `key` starts its agent within the request (api/board).
- The `Idempotency-Key` header gives us the immediate start and still makes retries safe.
- **Check this in the M0 spike.** If it misbehaves, fall back to `key: "wo_<id>"` and accept the delay.

### 3.2 Status back

Every 15 s the worker reads each open card **by id**: `GET /v1/boards/{id}/cards/{cid}`.
- **Don't page through the list.** Lists are sorted by column before paging, so `done` cards can fall off the first page (cli/board).
- When `updated_at` changes, the worker fetches new comments, parses the first line of `note`, and writes `workorder.status_changed` with the card's own words as the `why`.

| Card state | Platform status | Shown on the timeline as |
|---|---|---|
| `todo` / `doing` | `in_progress` | "fleet-ops is on it" |
| `done` + `RESULT: ordered ORDER_REF=… COST_USD=…` | `done` | "Ordered: ref …, $…" |
| `done` + `STOPPED: <reason>` | `stopped` | "Stopped: <reason>", which needs a person |
| `done` without a valid `RESULT`/`STOPPED` line | `needs_human` | "Card closed without an order ref" |
| `blocked`, or parked after 3 starts | `needs_human` | "Parked by Naïve: <last comment>" |

Polling survives Wi-Fi drops and a sleeping laptop, because cards are stored durably on Naïve's side *(inferred)*.

### 3.3 Card template

```
Title: WO-17 · arm-1 · replace gripper servo (STS3215)

WORK ORDER wo_17
Robot: arm-1 (SO-101 follower, bench 1)
Fault: gripper_stall. Gripper current above 900 mA; the agent's fix reverted twice.
Request: order 1 × Feetech STS3215 servo (12 V)
Supplier: <name> · <phone>        (from cloud/config/parts.yaml)
MAX_USD: 40
Deliver to: <address>

Rules:
- If the price is over MAX_USD or the part is out of stock: set the card to blocked and comment why.
- When finished: set done, with the first line of the note exactly
  RESULT: ordered ORDER_REF=<ref> COST_USD=<amount>
  or
  STOPPED: <reason>
```

- **Keep raw logs and code out of the body.** Bodies that look like SQL can get an HTML 403 from the edge (api/errors). Bodies are capped at 20,000 characters (api/board).
- **The platform needs a small parts catalogue,** `cloud/config/parts.yaml`, mapping fault kind → part, supplier, phone and typical price *(inferred)*.

### 3.4 Spend governance (pending D19b)

Naïve can't cap the dollar amount of an order, so the caps are layered *(inferred)*:

1. **In our code, before posting:** limits on dollars per order, orders per day, and orders per robot. These are in `settings` and are not visible to any LLM.
2. **Over the cap: decided (D19b), option (a).**
   - **(a) recommended, keeps D11:** the work order isn't posted. It shows as `blocked: over cap` and waits for a person. That's an escalation, not an approval step, so the kill switch stays the only human control.
   - **(b)** Route it to a second agent, `fleet-ops-gated`, whose Mobilerun tool is set to `ask`, so a person approves it inside Naïve. The gate then lives in Naïve policy, which the agent can't change, but this adds a second human control and changes D11.
3. **The agent's own model spend:** a Naïve budget on fleet-ops (for example `max_task_micro_usd` for $2 per task, and a daily cap) (concepts/budgets).
4. **Payment itself:** unresolved. Naïve's wallet and cards return `501`, so a phone order would be paid by invoice, cash on delivery, or a person. **Ask** on site (§7, Q10).

### 3.5 Secrets

| Secret | Where it lives |
|---|---|
| The platform's Naïve API key (`agents:read`, `agents:write`, `sessions:read`, `audit:read`) | `.env` → Docker Compose secret. Never in a card or a log |
| The Mobilerun bearer (`dr_sk_…`) | The Naïve vault as `static_bearer`, set with `naive vault set --mcp-bearer https://api.mobilerun.ai/v1/mcp` and bound to fleet-ops by `agent_id`. Once bound, editing that agent needs `admin` (identity/vault, api/vaults; sponsors.md) |

The board scopes can't be narrowed: a key that can write cards can also manage agents (api/board). Keep the key out of everything except the worker *(inferred)*.

### 3.6 Crew as config

`naive/naive.config.json` sits in the repo. Run `naive up --dry-run`, then `naive up` (cli/naive). It declares:
- the `fleet-ops` agent: `pi` harness, a short system prompt that explains the card template, a budget, and a tool policy (below);
- a vault credential read from the environment (`from_env: MOBILERUN_API_KEY`);
- optionally, a nightly schedule that posts a fleet report.

**Its tool policy:**
- Default `deny`; explicitly allow `board_read`, `board_write`, and the one Mobilerun tool it needs *(inferred)*.
- Default `deny` also blocks the board tools unless they're listed.
- MCP tools default to `ask` unless configured (capabilities/tools).

**Pick `pi`, not `hermes`, for fleet-ops.** The `hermes` harness drops `ask` tools, and its tool calls don't show in the event stream, so we'd lose the evidence of the Mobilerun call (concepts/harness-capabilities).

**Keep fleet-ops off any coordinator's roster,** so it sits on the company board where we post *(inferred from team/board)*. The vault's `agent_id` binding is done once by CLI or REST, because the config has no `agent_id` field.

Field names have to be checked against concepts/templates and capabilities/agent before the first apply. Ownership of `naive/` is still open (D14 / build plan §7).

### 3.7 Autonomy ledger

Every platform event gets an `actor_class` of `agent`, `automation` or `human` *(inferred)*:
- **agent:** device agents, the batch job's LLM, and Naïve card comments whose author starts with `ses_`.
- **automation:** the gate and the worker, including the platform's own `key_` actions on Naïve.
- **human:** the kill switch, dashboard actions, parked cards that a person resolves, and any other `usr_` or `key_` author.

The morning digest counts each class. The Naïve audit log covers only control-plane actions, not agent tool calls (api/audit-logs), so our event log is the main record.

## 4. Reference

| Item | Value | Source |
|---|---|---|
| Base URL | `https://api.vetta.sh/v1` | api/overview |
| Version header | `Vetta-Version: 2026-09-30`. Unknown body keys are refused with `400` | api/overview |
| Auth | `Authorization: Bearer sk_live_…`; check with `GET /v1/me` | api/authentication |
| Create a card | `POST /v1/boards/{brd_ or agt_}/cards` `{title (1–200), body (≤20k), assignee (agent name), status?, blocked_by?, key?}` | api/board |
| Read a card | `GET /v1/boards/{id}/cards/{cid}`; comments at `GET /v1/boards/{id}/cards/{cid}/comments` | api/board |
| Lifecycle | `todo → doing → done`; `done` needs a `note`; each assignee holds one in-flight card per board; at most 3 starts, then parked `blocked` | api/board, team/board |
| Company board | owned by the CEO; `ceo_agent_id` on the organization object | api/organizations |
| Rate limits | 6,000/min per org, 3,000/min per key; `429` with `Retry-After` | api/errors |
| Errors | `{error: {type, code, message, request_id, param}}`. Retry `429` and `5xx`; for `409`, branch on `code` | api/errors |
| SDK | TypeScript only; REST from Python | sdk/typescript |

## 5. M0 spike (15 minutes)

1. `GET /v1/me` with the key: check the organization and scopes.
2. `GET /v1/organizations/…`: read `ceo_agent_id` (check the exact route in api/organizations).
3. `POST /v1/boards/{ceo_agent_id}/cards` with `Idempotency-Key: spike-1`, `{title: "spike", body: "test card, please mark done with note RESULT: test", assignee: "<an existing agent>"}`.
4. Send the same request again and confirm it returns the same card.
5. `GET` the card by id until its `status` and `note` change.

Write down: the board id, the agent name, how long until the agent started, and whether `Idempotency-Key` started the agent immediately.

### 5.1 Spike results (Sat Nov 7, ~4:45 PM PST)

Source: the orchestrator's spike notes, recorded during M0 with the team's Naïve key.

| Step | Result |
|---|---|
| `GET /v1/me` | `200`. Org **Romulus** (`org_re7d4swgnqf6npz1804z9q107v`), mode `live`. Scopes listed: `agents:read/write`, `sessions:read/write`. No board or proxy scope is listed, yet both board reads and the proxy work with this key |
| Company board | CEO agent `agt_j2jy5qp878yhfhqwpxd200ay12` → board `brd_v2zcnyzc559hv8awzc4hqvgcsm`. Reading cards works (an existing card, "Romulus agent specs…", came back) |
| Agents | Only `hello-world` (James's test agent) and `CEO`. **No `fleet-ops` agent yet** (§3.6) |
| Models | `GET /v1/models` works. `GET /v1/proxy/openai/v1/models` returns `404`: the proxy has no models route, so pick model ids from `/v1/models` |
| Proxy chat completions | `200` with this key, without a `Vetta-Version` header. `openai/gpt-oss-120b`: ~1.8 s, good answers, so `LLM_MODEL=openai/gpt-oss-120b`. GLM-5.2 returned empty content, and ling-tiny returned cut-off text: their reasoning used up the tokens |
| Card create + `Idempotency-Key` (steps 3–5) | **Not done: pending Kent's go-ahead.** Agent pickup time and whether the key starts the agent at once are still unknown |

**So the batch LLM isn't blocked.** The proxy works with the team key today; earlier notes that tied the LLM to blocker B4 or to a missing `proxy:write` key are out of date. `LLM_MODE=live`, `LLM_PROVIDER=naive_proxy` and `LLM_MODEL=openai/gpt-oss-120b` turn it on (`.env.example`). The coding agent also ran on it (build plan §3, M8, B4 🟢, #19).

## 6. Risks

| Risk | Mitigation |
|---|---|
| **Overnight chaos makes real phone calls to a real supplier at 3 AM** | **Overnight, set `NAIVE_MODE=fake`, or point `parts.yaml` at a teammate's phone, and cap orders per day.** Never call a real business from a test loop |
| fleet-ops sits on a coordinator's board, so cards on the company board aren't seen | Keep it off rosters; check in the spike |
| One stuck card blocks the agent's queue (one in flight per board) | Keep the routine path on `allow`; detect parked cards → `needs_human` |
| The agent marks `done` without ordering (for example because the Mobilerun MCP was down) | Require `ORDER_REF` in the note; otherwise `needs_human` |
| A flagged chat makes every MCP call ask for approval | Keep fleet-ops away from mail, contacts and connected-app reads (capabilities/tools) |
| `budget_paused` stops the agent starting | Watch `GET /v1/digest` → `waiting.budget_paused` (api/digest) |
| The org's plan refuses calls with `402 subscription_required` | Confirm the hackathon org's plan in the spike |
| "Must use the Naïve SDK" may not count REST from Python | The team uses Naïve for the business and job board; `naive up` for the crew is CLI usage. **Ask** (Q1) |

## 7. Questions for Naïve staff

1. Does calling the REST API from Python count as "using the Naïve SDK"?
2. Do comments notify the assignee? Are webhook events for board cards planned?
3. Does an organization-wide vault (no identity) inject the bearer, or must the agent hold the persona?
4. Can `naive.config` `vaults[]` take an `agent_id`?
5. Do reads through an MCP server flag the chat?
6. Does AgentMail's MCP accept `Authorization: Bearer`?
7. What plan, credits and concurrency does the hackathon org have?
8. Can a hand-written config declare mini apps?
9. When an admin drives the CEO, does its per-turn credential carry `admin`?
10. How should a phone order be paid, and will Naïve cards be turned on at the event?

## 8. Corrections to earlier docs

- **D11 and D12 said spend caps live in "Naïve's policy file".** Naïve has no policy file that can cap spend. Policy is allow, ask or deny per tool, and `naive.config` is the source of truth that `naive up` reconciles (concepts/policies, cli/naive). The caps move into our code (§3.4). D12's core decision, that Naïve runs the agents, still stands. The decision log records the correction.
- **The v0 plan proposed `key: "wo-<id>"`.** It now uses the `Idempotency-Key` header, to avoid the one-minute seed delay (§3.1).
