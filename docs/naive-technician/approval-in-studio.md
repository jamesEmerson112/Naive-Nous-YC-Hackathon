# Approval in the Naïve Studio: a working example

Option B from [`design.md`](design.md) §8: the person approves the Technician's staged part on Naïve's own question card in the Studio, and Romulus stays in sync. A side example, built and run live on Sun Nov 8 at 08:09–08:12 PST. It isn't on the T0 path, and the real `technician` agent is untouched (its prompt still forbids `ask_operator`, `design.md` §2).

## The flow
1. Romulus starts a session of the example agent. Its message carries the case and the staged part, with the gate's ticks (`POST /v1/sessions`, F:10137).
2. The agent calls `ask_operator` once: one prompt and one `choice` field, `options: ["Approve & open cart", "Deny"]`, `other: false`, with the cart link as the `help` line (F:10067, F:22576–22592).
3. The session parks: `idle`, `stop_reason: "awaiting_answer"`, and the question becomes a `pending_actions` row with `kind: "question"` (F:10129, F:10418). It burns no budget while it waits (F:22567).
4. Naïve emails the owner a link to the paused session, because the agent has `notifications.questions` on (F:170). The Studio shows the question card in the session's chat *(inferred: the docs don't describe how the card looks)*.
5. Someone answers, in either place. **Studio:** pick an option on the card. **Romulus button:** `POST /v1/sessions/{id}/answers` `{tool_call_id, answers: {decision: "Approve & open cart"}}` (F:10420–10432). The answer must be the option's words, never an index (F:22619).
6. The log records `tool.confirm.answered` `{tool_call_id, answers, answered_at}` (F:6055). Romulus reads it and calls `workorders.resolve(session, "wo_example", "approve"|"deny", by=…, why=…, now)`. On approve, the order goes `done` with "Approved by {by}: 1 × … (cart …)" (`design.md` §4).
7. The agent gets the answer back as the call's result (F:22621), says `DECISION: approve` and ends its turn.

```
Romulus case page               Naïve session (agt_t1qy…)                Naïve Studio
  │ POST /v1/sessions ───────────▶ ask_operator(prompt, decision)
  │                               idle · awaiting_answer ───────────────▶ question card [Approve & open cart] [Deny]
  │ poll GET /sessions/{id} ◀──── pending_actions[kind=question]           (and the email link, F:170)
  ├─ [Approve] ─ POST /answers ──▶ answered  ◀────────────── or the person picks on the card
  │ poll events ◀──────────────── tool.confirm.answered {answers}
  └─ workorders.resolve(approve, by=…) → work order done; the agent says "DECISION: approve" and ends its turn
```

## What was created (live)
- **Agent** `agt_t1qy0q1xagnhsqbkt81qtk74c1`, `technician-approval-example`: harness `pi`, `anthropic/claude-haiku-5.5`, tools deny-by-default with only `ask_operator` named in `configs` at `ask` (needed under a deny default, F:13884; `ask_operator` has no `allow`, F:14005). Budget $5 a month and $1 a task, `handoffs: false`, question emails to Kent.
- **Session 1** `ses_vndazsrs5vsm0mffvt42nqkx3a`: parked on `awaiting_answer` 6 s after creation and was left for Kent. It was answered **Approve & open cart** at 16:24:13Z, outside our scripts, presumably by Kent on the Studio card *(inferred: the event names no actor)*. The script's watcher caught it, printed `resolve(approve, by="naive-studio")`, and the agent replied `DECISION: approve`. The session ended `idle` / `end_turn` at a cost of $0.0016.
- **Session 2** `ses_7fmtg6ae3zprf939s2pycy2nhv`: started with `--answer approve`. It parked at 16:11:17Z, was answered through the API at 16:12:08Z, the agent replied `DECISION: approve`, and the session ended `idle` / `end_turn` at a cost of $0.0003.
- **Script:** [`cloud/scripts/technician_approval_example.py`](../../cloud/scripts/technician_approval_example.py) (stdlib only). Without flags it waits up to 15 min for a Studio answer. `--answer approve|deny` answers through the API. `--session ses_…` watches or answers an existing session.

## Where Kent clicks
Open [app.usenaive.ai](https://app.usenaive.ai), then the agent **technician-approval-example**, then its chat for session `ses_vndazsrs5vsm0mffvt42nqkx3a`. Or follow the "question" email link (F:170). Pick **Approve & open cart** (or **Deny**) on the card and submit. The API returns no session URL, so we don't build one by hand (`design.md` §8). To see Romulus's side, run `python3 cloud/scripts/technician_approval_example.py --session ses_vndazsrs5vsm0mffvt42nqkx3a`.

## Found live (differs from the docs or isn't in them)
- **The answer has no actor.** `tool.confirm.answered` carries only `answers`, `answered_at` and `tool_call_id` (F:6055, confirmed live). `tool.confirm.resolved` does carry `actor` (F:6054). The audit log would say who answered, but this key lacks `audit:read`. So `by` is the channel, `"naive-studio"`, unless Romulus answered it itself. That's a weaker trail than a click on the work order *(inferred)*.
- **A question does emit `tool.confirm`** (with `kind: "question"`) before `session.idle`, though F:6159 says it emits no event of its own. Branch on `pending_actions[].kind`, as F:22561 advises.
- **`tool_call_id` was identical across both sessions** (`tc_796b…`). It's derived from the call (F:10071), so key answers by session and id.

## `ask_operator` vs `permission: "ask"`
The other primitive: put a tool at `ask`, and its call parks as `awaiting_approval` and is answered allow or deny through `tool_confirmations`, which records the `actor` (F:10343–10360, F:6054, F:22627). That fits gating a *tool Naïve runs*, such as a real `pay`. Our purchase is a person's checkout outside Naïve, so there is no tool call to gate. `ask_operator` is the fit: it carries the part, price and cart as a question with named options. The trade-off is that `tool_confirmations` records an actor and an answer doesn't *(inferred)*.

## Costs and caveats
- **It holds the session open.** On `pi`, an idle session holds a micro-VM (F:21540), though it burns no budget (F:22567). The card waits until someone answers or Romulus cancels the session.
- **Both sides must sync.** Romulus has to poll the session (or stream it) to catch a Studio answer, and answer the Studio when its own button is pressed. Otherwise the card goes stale. Whichever answer lands first wins *(inferred)*. A second answer should get a refusal, since the row is gone (F:10420).
- **Platform latency today.** `POST /v1/sessions` and the polls returned `429 "the service is at capacity"` several times. The script retries reads and keyed writes with backoff for up to 3 min, and resends the session create with the same Idempotency-Key. Once a session started, it parked in about 5 s.
- **Spend:** two sessions, about $0.002 in total.
