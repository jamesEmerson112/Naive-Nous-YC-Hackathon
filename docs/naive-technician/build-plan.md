# Naïve Technician: build plan (v2, Sun Nov 8, 7:45 AM → 10:00 AM freeze)

_Times are PST. The 9:00 go or break-glass call and the 10:00 freeze are fixed; if things start late, squeeze the steps in between. Hacking stops at 11:00 (`docs/hackathons.md:39`). The rest of the team is on the real arm from 7 to 10 AM, so this is Kent plus **one** Claude session, with a 15-minute arm slot at about 9:30. [`review.md`](review.md) says why v1's plan wouldn't have landed._

## Three facts that shape this plan (from the execution review)
1. **Kent's laptop isn't running a Romulus cloud.** Only `romulus-testdb` is up. The cloud with real data is Olir's laptop (`docs/cloud-link-status.md`), and the arm reported to James's Mac overnight. Both run built images.
   - So Kent runs **his own throwaway stack** with the source mounted, and **never deploys to Olir's laptop**.
2. **Foam alone won't produce a hand-off.** It blocks only the reach, the healer's move back home succeeds, and the stall reports `resolved=True`. So lock the elbow both ways (`demo-clip.md`).
   - **Break-glass:** James's branch has a `handoff` op that forces the hardware report, if the Pi is running that branch.
3. **v1's backend lane was milestone-sized,** about as big as M5 (#11). A full test run takes 6 min 28 s. So T0 is cut down (`design.md` v2: one table, approval through the existing work-order `resolve`, four event kinds, no list page) and built by a single session.

## Tiers
| Tier | What | Done means |
|---|---|---|
| **T0, must** | a `needs_human` order with no part opens a case (plus a manual button) · one live Naïve session with a typed report · email sent from the Naïve inbox · the part checked in code and staged on the work order · Approve through `resolve` · the case page · per-case replay | the clip in `demo-clip.md` can be filmed |
| **T1, phone** | Twilio `<Say>` call to a teammate | the phone rings on camera |
| **T2, only if ahead** | approval mirrored into the Naïve Studio as an `ask_operator` card (`design.md` §8 B) · a `healer_gave_up` trigger once the bridge sends a hardware cause · the agent's browser and cart screenshot · `naive/technician/setup.mjs` re-creating the setup with the official TypeScript SDK | anything here is a bonus |

## Timeline
| Time | Who | What | Gate |
|---|---|---|---|
| **7:45–7:55** | Kent | (1) Add `Naive technician: hard failure → Naïve report + actions (docs/naive-technician/) <--- Kent` to `features.txt` under ROMULUS, then pull and push. (2) Decide the **servo variant** (read the label on the elbow servo, or ask Andre) and the **teammate technician's** email (and phone, for T1). (3) **Check the key's scopes**: `agents:write` and `sessions:write` (F:5712, F:8172). (4) **Start the stack:** `COMPOSE_PROJECT_NAME=tech make dev && make seed`. `make dev` mounts the source, so a code change needs only `docker compose restart worker`. `seed` registers `arm-1` and `sim-1..3`. (5) **Ask James:** can the Pi reach Kent's IP for `ARMSIM_ROMULUS=http://<kent-ip>:8000` (`armsim/host.py`, `stories.py`), and is the `handoff` op on the Pi? | |
| **7:50–8:50** | Claude, one session, a worktree off main | The **cut-down backend and the page** (file list below). Brief it with `design.md` §0–§13 and the four gotchas under "Briefing the build session". The page templates go against an agreed `view(case)` dict from the start. Inner loop: F14, the unit file and the two guard files only | **F14 green on fake by 8:50** |
| **7:55–8:15** | Kent, or the Claude session he's talking to, through the Naïve MCP with an OK on each write | **Naïve setup** (step 1 below), then **send a test email to the teammate now** | **G0, 8:15: the email arrives in the inbox, not spam.** A 501 (F:16020) or no delivery means switching the executor to AgentMail (`design.md` §4) |
| **8:15–8:45** | Kent | **The spike, in Python, as the shipping adapter** (step 2). Save the run as the first fixture | **G1, 8:45:** the report validates; the variant matches the store's `.js`; the run takes ≤10 min and costs ≤$3; the session shows up in the Naïve Studio (needed for shot 2) |
| **9:00** | Kent | **Go, or break-glass** (below) | |
| 9:00–9:15 | Kent | Merge the worktree into his stack. Open a live `manual` case on a `joint_stall` incident posted to `sim-1` (a contract-shaped JSON, labelled sim). That's the real end-to-end rehearsal, and it needs no arm | the case reaches `waiting`, the email arrives, Approve opens the WowRobo cart |
| **9:15–9:30** | Kent + a teammate with a phone | **Film the guaranteed clip** from that run: shots 2–5. The arm shot comes from 9:30, or from labelled earlier footage | |
| 9:30–9:45 | Kent + the arm team | **Arm slot.** James points the Pi at Kent's stack. Lock the elbow both ways and run `stories stall --real`. If it hands off (about 10–15 s to the cloud), re-film shot 1 and the page; otherwise keep the 9:15 footage | at most 2 takes |
| 9:45–10:00 | Kent | Export the best case's `raw` to `cloud/tests/fixtures/technician/joint_stall_live.json` and set `TECHNICIAN_REPLAY_FILE`. **Pre-open a replay case.** Record the backup video. Open a PR from the worktree, and decide which cloud runs the 1 PM demo (it needs main plus `TECHNICIAN_MODE`) | **freeze** |

## Step 1 · Naïve setup (7:55–8:15)
The account has 4 agents, 0 identities and 0 computers (read-only check, 7:15 AM).

**Fastest verified path: the Naïve MCP control plane from a Claude session.** These tool names were checked this morning: `agents_create`, `agents_get`, `identities_create`, `identities_create_inbox`, `agents_attach_identity`, `identities_send_email`. The Naïve CLI is the alternative. Every one of these calls is a write that needs Kent's OK.
1. **Create the agent `technician`:**
   - harness `pi`, model `anthropic/claude-opus-5.5`, the system prompt from `design.md` §2;
   - `tools.default_config.permission = "deny"`, with `configs` allowing `web_search`, `web_fetch`, `data_search` and `data_run`;
   - budget: $20 a month, $3 per task.
   - **Read it back with `agents_get`** and confirm `data_run` is set to `allow`.
2. **Create the identity and inbox:** identity "Romulus Technician", a system domain (`kind: "system"`, verifies in about a minute, F:23701), and an inbox owned by `technician` with **`wake: false`**. An owner that wakes on mail gets its chat flagged (F:14351).
3. **Attach the identity to the agent.**
4. **Put the three ids in `.env`:** `NAIVE_TECHNICIAN_AGENT`, `NAIVE_TECHNICIAN_IDENTITY`, `NAIVE_TECHNICIAN_INBOX`.
5. **Test email:** `identities_send_email` to the teammate, with subject `arm-1 down: clear elbow jam, spare servo ready`. This is G0.

**On "the SDK."** The runtime calls Naïve's REST API from Python, the same route table the TypeScript SDK and CLI are built on (F:9548). There's no Python SDK (`naive/README.md`). `naive/check.mjs` already uses the official SDK. Re-creating step 1 as `naive/technician/setup.mjs` with `@usenaive-sdk/managed-agents` is T2, worth doing only if a judge is likely to ask to see SDK code.

## Step 2 · The spike is the adapter (8:15–8:45)
Write `cloud/scripts/technician_spike.py <incident.json> [--out fixture.json]`. It calls the **same** `HttpNaive.create_session`, `get_session` and `session_events` methods the worker will use, with the report schema from `domain/technician.py`. It also:
- prints the event lines as `feed_line` renders them;
- prints the validated report, the duration and `consumed_micro_usd`;
- runs the shop check (`/products/{handle}.js`, cents ÷ 100);
- writes `{request, session, events, structured_output}` as a fixture.

If the backend session hasn't landed those methods by 8:15, Kent writes them first in the worktree; they're about 40 lines over `HttpNaive._request`.

**The incident:** a `joint_stall` `IncidentReport`, either from a teammate's cloud (`GET /v1/incidents/{id}`) or built from the arm host's report shape (`armsim/romulus.py`).

**If G1 fails, in order:**
1. Give exact handles and variants as hints, and tighten the prompt.
2. Narrow the tools to `web_fetch` plus `data_run`.
3. Try a faster model.
4. Keep the best run as the fixture and go break-glass.

## Briefing the build session: gotchas it must know
- **Shopify gives prices in cents** (`1599`). The WowRobo servo product also has 5-pack ($74.95) and 10-pack variants, so match on the exact `variant_id`.
- **The email send** takes `to` as an **array** and returns a receipt **with no message id** (F:8172-8195, F:27198).
- **An Idempotency-Key is bound to the request body** (F:9441). Freeze the session request in `case.request`.
- **The guard tests:**
  - every new setting goes in `.env.example`;
  - every event kind needs a `story.LABELS` entry, a row in the APIs §5 table and a literal name (no f-strings);
  - `technician.*` needs a `STAGE` above 62 and must be added to `digest.ACTION_KINDS` for `emailed`.
- **Pull main before merging.** Olir edited the dashboard templates at 4:49 and 5:15, so keep the `base.html` change to one nav line.

## Files for T0
| File | New or edit |
|---|---|
| `cloud/src/romulus_cloud/domain/technician.py`: report model and hand-written schema, `actions_from_report`, gate rules, `cart_permalink`, `feed_line` | new |
| `cloud/src/romulus_cloud/services/technician.py`: `open_cases`, `sync`, `stop`, `case_file`, `view` | new |
| `cloud/src/romulus_cloud/adapters/naive.py`: 6 session and email methods on `HttpNaive`, plus `FixtureNaive` | edit |
| `cloud/src/romulus_cloud/adapters/shop.py`: the `product()` function | new |
| `cloud/src/romulus_cloud/services/workorders.py`: the `technician_part` branch in `resolve` | edit |
| `models.py` (`TechnicianCase`), `worker.py` (2 steps), `settings.py` + `.env.example`, `domain/story.py` (labels, `STATUS`, `STAGE`, `NEEDS_PERSON`), `domain/digest.py` (`ACTION_KINDS`), `events.py` (refs docstring) | edit |
| `api/technician.py` (open, stop, get, page, partial) + `main.py` include; `web/templates/technician.html`, `partials/technician_case.html`; one nav line in `base.html`; a link on the incident page | new, edit |
| `cloud/config/parts.yaml`: a comment saying why `joint_stall` and `us_no_echo` stay uncatalogued | edit |
| `cloud/tests/flows/test_f14_technician.py`, `cloud/tests/unit/domain/test_technician.py`, `cloud/tests/fixtures/technician/joint_stall_fake.json` | new |
| `cloud/scripts/technician_spike.py` | new |
| `docs/cloud-platform-planning/cloud-platform-apis.md` §4.5 and §5; D30 in `cloud-platform-decisions.md` | edit |

## Break-glass (decide at 9:00, if G1 or F14 is red)
Take Romulus's worker out of the live path:
1. The spike script runs one live session on the incident JSON. It already exists by then.
2. The same script sends the email from the Naïve inbox and builds the checked cart link: about 10 more lines.
3. **One Romulus route** renders the captured fixture into the case page: verdict, part card with ticks, Approve linking to the cart, and the overlay numbers. It carries the badge "Recorded Naïve session ses_… · 09:0x".

The cart link works live (WowRobo redirects with a 302 to a cart, checked). The clip still has every shot. This needs only the setup, one good session and one email, which Kent can do alone between 8:00 and 9:00.

## Cut order
1. T2.
2. T1, the phone; the clip uses the email shot.
3. The live feed animation: show it as a static list.
4. The real-arm shot: use the sim incident, and leave the arm out or caption it "re-enacted".
5. Live on stage: use the pre-opened replay tab.

**Never cut:** the typed report on the page, the email really arriving, the checked cart with Approve through the work order, the measured overlay, and the honest badges.

## Coordination
- **James:** the Pi's `ARMSIM_ROMULUS` for the 9:30 slot; the `handoff` op as break-glass; one link from his Mission Control banner to `/technician/{case}`. Don't touch his branch otherwise.
- **Andre:** the servo variant; the arm slot.
- **Olir:** never deploy onto his laptop cloud, and never `make reset`. The T2 `healer_gave_up` trigger reads `agent_runs` as his note asks, and needs the bridge to report `hardware` when the healer hands off.
- **Credits:** the team started with $250 of Naïve credit (`features.txt`), and Hermes has been spending it overnight. Check the balance at 7:55 (`credits_get_balance`).
