# Naïve Technician: build plan (Sun Nov 8, ~7:40 AM → 10:00 AM freeze)

_Times are PST. Hacking stops at 11:00 AM and judging starts then; the team's code freeze is 10:00 (`docs/hackathons.md:39`, `features.txt` P4). The rest of the team is on the real arm from 7 to 10 AM, so this is Kent's lane plus Claude sessions in worktrees. The arm is needed for one 10-minute slot at about 9:30._

## Tiers
| Tier | What | Done means |
|---|---|---|
| **T0, must** | trigger (`playbook_exhausted` + `manual`) · one Naïve `technician` session per case with a typed report · gate · email sent from the Naïve inbox · verified Shopify cart link with Approve · Technician page with verdict, feed, actions and counter · replay mode | the clip in `demo-clip.md` can be filmed without the phone shot |
| **T1, phone** | `call_technician` through Twilio `<Say>` to a teammate | the phone rings on camera |
| **T2, polish and showcase** | `healer_gave_up` trigger from agent runs · the agent's cart screenshot on the page · email sent by the agent through `email.send` on `ask`, auto-confirmed by Romulus (`tool_confirmations`) · AgentMail or Mobilerun extras for the sponsor tracks | only if T0 and T1 are green by 9:15 |

## Timeline and lanes
```
           7:40      8:00      8:30      9:00      9:15      9:30      9:45     10:00
Lane A  ── decide ─ setup ─── SPIKE ──┤G1├─ live adapter ─ live email ─ ARM RUN ─┤G3├─ film
(Kent)     D30,     agent,    one live          (HttpNaive-   to teammate  (stall,    export
           variant  identity, session on a       Sessions)    spam check   live)      replay
                    inbox     stall case
Lane B  ──────── Romulus backend in fake mode (worktree) ───┤G2├─ fix-ups ──────────────────
(Claude)  domain/technician.py · models · adapters (fake, replay, shop) · services · worker · API · F14
Lane C  ─────────────────────── page + incident link + Approve (worktree) ───┤G2├───────────
(Claude)
Lane D  ─────────────────────── T1 Twilio adapter, standalone (only if someone's free) ─────
```

### Step 0 · 7:40–7:50 · Decide and claim (Kent)
- Add `Naive technician: hard failure → Naïve report + actions (docs/naive-technician/) <--- Kent` to `features.txt` under ROMULUS, then pull and push (`CLAUDE.md` working rules).
- Settle the 4 decisions in `README.md`:
  - D30;
  - the servo variant (read the sticker on the elbow servo, or ask Andre);
  - the technician teammate's email (and phone, for T1);
  - approval for the live Naïve writes.

### Step 1 · 7:50–8:05 · Naïve setup through the official SDK (Kent)
Write `naive/technician/setup.mjs` with `@usenaive-sdk/managed-agents` 0.12.3 (`naive/package.json`). The SDK is TypeScript only, with no Python package (`naive/README.md`). Make it safe to repeat: look each thing up by name and create it only if missing.
1. Create agent `technician`: `pi` harness, the model and tool policies in `design.md` §2, a monthly cap of $20 and a per-task cap of $3.
2. Create identity "Romulus Technician", then a system domain (`kind: "system"`, which verifies in about a minute, F:23701), then the inbox `technician@<domain>` with `owning_agent_id` = the agent and `wake: false` (an inbox owner that wakes on mail would flag its chat, F:14351).
3. Attach the identity to the agent (`POST /v1/agents/{id}/identities`).
4. Print `NAIVE_TECHNICIAN_AGENT`, `NAIVE_TECHNICIAN_IDENTITY` and `NAIVE_TECHNICIAN_INBOX` for `.env`.

**Writes that need Kent's OK:** all of the above. The live account has 4 agents, 0 identities and 0 computers, as of a read-only check at 7:15 AM.

**SDK method names:** `client.agents.create`, `client.sessions.create`, `client.sessions.get` and `client.sessions.events` are inferred from the docs dump. Check them against the package's types after `npm install`.

### Step 2 · 8:05–8:30 · The spike, the riskiest unknown first (Kent)
Write `naive/technician/try.mjs <case.json> [--out fixture.json]`:
- It runs one live session with the real system prompt and `output_schema` (paste the JSON Schema exported from the Pydantic model once Lane B has it, or hand-write it from `design.md` §2.1 for now).
- It streams the events to the terminal and prints the `structured_output`, the time taken and `consumed_micro_usd`.
- With `--out` it writes `{session, events, structured_output}`, which **is the replay fixture**.

**The case:** a real `joint_stall` incident from the laptop cloud, copied from `GET /v1/incidents/{id}`. If there isn't one yet, use the sim stall: `python -m armsim.stories stall`, per `prototypes/arm-sim/MORNING.md`.

**Gate G1 at 8:30.** Go if all of these hold:
- the report validates;
- the part is from the allowlist, with a variant id that matches `/products/{handle}.js`;
- the run takes 5 minutes or less and costs $3 or less.

**If not, in order:**
1. Tighten the prompt and give exact product handles as hints.
2. Try a faster model.
3. Narrow the tools to `web_fetch` plus `data_run` (no open search).
4. Last resort: keep the best spike run as the replay fixture and demo that, clearly labelled.

### Lane B · 7:50–9:15 · Romulus backend in fake mode (a Claude session in a worktree)
Brief the session with `design.md` §1–7 and §9–13. Files:

| File | New or edit | About |
|---|---|---|
| `cloud/src/romulus_cloud/domain/technician.py` | new | `should_escalate`, `TechnicianReport`, `actions_from_report`, gate rules, `cart_permalink`, `feed_line`; pure, no I/O |
| `cloud/src/romulus_cloud/adapters/naive_sessions.py` | new | Http / Replay / Fake |
| `cloud/src/romulus_cloud/adapters/shop.py` | new | Shopify `/products/{handle}.js`, plus a fake |
| `cloud/src/romulus_cloud/services/technician.py` | new | `open_cases`, `sync`, `resolve`, `case_file`, `view` |
| `cloud/src/romulus_cloud/models.py` | edit | `TechnicianCase`, `TechnicianAction` |
| `cloud/src/romulus_cloud/worker.py` | edit | 2 steps, dropped when `TECHNICIAN_MODE=off` |
| `cloud/src/romulus_cloud/api/technician.py`, `main.py` | new, edit | JSON routes and the resolve route |
| `cloud/src/romulus_cloud/settings.py`, `.env.example` | edit | `design.md` §10 |
| `cloud/src/romulus_cloud/domain/story.py`, `events.py` | edit | labels, `STATUS`, `NEEDS_PERSON`, refs keys |
| `cloud/tests/flows/test_f14_technician.py`, `cloud/tests/unit/domain/test_technician.py` | new | `design.md` §13 |
| `cloud/tests/fixtures/technician/joint_stall_fake.json` | new | a small hand-made fake. Swap in the live capture from step 6 |
| `docs/cloud-platform-planning/cloud-platform-apis.md` | edit | §4.5 pages, §5 events |

**Gate G2 at 9:15:** `make test` passes, including F14, and the guard tests are green (story labels, settings defaults).

### Lane C · 8:30–9:15 · The page (a Claude session in a worktree, after Lane B's models land)
- Add `web/templates/technician.html`, `partials/technician_case.html` and `partials/technician_list.html`.
- Add the nav link in `base.html`, plus the "Escalated to Naïve technician →" link and the "Send to Naïve technician" button on the incident page (`timeline.html` / `partials/story.html`).
- Add CSS to `web/static/romulus.css`, reusing its tone classes (:141-155).
- Build it to the wireframe in `design.md` §8. The clip's legibility rules (`demo-clip.md`, "Making each frame legible") are acceptance criteria, not polish.

### Step 5 · 9:15–9:30 · Go live (Kent)
- Set `TECHNICIAN_MODE=live`, the Naïve ids and `TECHNICIAN_EMAIL` in `.env`, then restart the worker. Settings are read once at start (`worker.py` `steps_for`).
- Open a manual case on the spike's incident. Check that the email arrives in the teammate's inbox, not spam. If it lands in spam, mark it not-spam once; if it doesn't arrive at all, the fallback is the T2 AgentMail path or showing the sent message id.
- Leave `NAIVE_MODE=fake`: work orders stay fake, and `cloud/scripts/overnight_check.sh` expects that.

### Step 6 · 9:30–9:45 · One real run on the arm (Kent with whoever is at the arm)
- Wedge the elbow with foam during `stories stall --real`. Don't unplug a cable: `ConnectionError` isn't caught on that path, so no incident would be filed (`prototypes/arm-sim/armsim/real.py:102-104,138-141`).
- Within about 10–15 s the incident reaches Romulus, the work order goes `needs_human`, the case opens and the session runs.
- Export the case's `raw` to `cloud/tests/fixtures/technician/joint_stall_live.json` and set `TECHNICIAN_REPLAY_FILE` to it.

**Gate G3 at 9:45:** if no live run is captured, use the spike's fixture from step 2 and a `manual` case.

### Step 7 · 9:45–10:00 · Film (Kent and a teammate with a phone)
Follow the capture checklist in `demo-clip.md`.

### Lane D, T1 phone (optional, parallel, only with a free pair of hands)
- Write `adapters/phone.py` with Twilio `calls.create(to, from_, twiml="<Response><Say>…</Say></Response>")` and a status poll, plus a fake. It's about 40 lines *(inferred)*.
- Trial accounts call only verified numbers and play a notice first; upgrading removes the notice ([Twilio trials](https://www.twilio.com/docs/usage/trials)). Verify the teammate's number in the Twilio console beforehand.
- It merges only if G2 is green.

## Cut order, when time runs short
1. T2, all of it.
2. T1, the phone. The clip uses the email shot.
3. The live feed animation: show the feed as a static list.
4. The agent's browser and screenshot: drop the `browser` tool from the agent.
5. Live on stage: present the replay with its badge.

**Never cut:** the typed report on the page, the email really arriving, the price-checked cart link with Approve, the counter, and the honest replay badge.

## Coordination
- **James:** Mission Control's story banner (broke, parked, healing, resumed) gets a fourth state, "handed off → Naïve technician", that links to `/technician/{case}`. The stall story is his (`MORNING.md:15`).
- **Olir:** the `healer_gave_up` trigger (T2) reads `agent_runs` as his note asks (`cloud/CLAUDE.md`, "Real device data"). It never reads `sim` runs and never runs `make reset` on the laptop cloud.
- **Andre:** the servo variant, and the arm slot at about 9:30.
- **Overnight and digest:** `TECHNICIAN_MODE` defaults to `off`. The digest will count one human action per approved part, which is honest (D30).
