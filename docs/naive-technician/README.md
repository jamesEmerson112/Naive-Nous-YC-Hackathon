# Naïve Technician: some faults need hands

_Kent, Sun Nov 8, 7:45 AM PST. A sprint plan for the hours before the 10:00 freeze and the 11:00 stop (`docs/hackathons.md:39`). Status: **v2 proposal after a three-lens review**; nothing built yet._

## The idea
Romulus heals software faults on the arm and across the fleet. Hardware faults hit a **dead end** today:
- When a fault kind has no part in `cloud/config/parts.yaml`, `workorders.triage_new` leaves the order at `needs_human`, with part `(none)` and the note "a person must pick one".
- `resolve` can't even approve such an order: it returns `409 no_part`.
- Every real hard failure we can stage lands there. Real devices send `joint_stall` and `us_no_echo`; the catalogue lists `servo_absent` and `ultrasonic_dead`.

**The Naïve Technician takes over that order:**
1. **A Naïve agent session diagnoses it.** The `technician` agent, on the `pi` harness with four read-only research tools, returns one typed report: verdict, ranked hypotheses with physical checks, sources, a technician brief, and an optional spare part.
2. **Romulus's gate acts on it in plain code.**
   - It **emails the technician** from the Naïve Technician's inbox, on its own.
   - It **checks the part** against the vendor's live store and **builds a real cart link**, then stages that on the work order.
3. **A person approves the purchase** through the existing work-order `resolve`. It's the single human step, and the vendor checkout is the final check.

It's the heal loop again (the model proposes, code checks, then promote or hold), this time at company scale *(inferred: the pitch's "one heal loop at arm, fleet and company scale")*.

## Why this is the Naïve beat
Judges look for "what its agents actually delivered, what decisions they made, and where you had to step in", and "how much of the company runs without you" (`docs/hackathons.md:12,24`). The case page answers all of it on one screen, headed by three measured numbers: **duration · cost · 1 human click**. A 2 s cut to the **Naïve Studio**, showing the session transcript, makes it unmistakably Naïve.

## The loop
```
 healer gives up              Romulus (Python, your laptop stack)                    Naïve
 ───────────────              ───────────────────────────────────                    ─────
 joint_stall, unresolved ─► triage_new: work order needs_human, part "(none)"
                            technician.open ─► TechnicianCase (one per work order)
                            POST /v1/sessions (frozen body, Idempotency-Key) ──────►  agent "technician" (pi)
                                                                                      web_search · web_fetch ·
                            poll events → "agent at work" feed            ◄──────     data_search · data_run
                            poll session → typed report                   ◄──────     → submit_output
                            GATE (plain code):
                             • email: recipient from settings, links stripped ─────►  Naïve inbox ──► teammate's phone
                             • part: origin in the allowlist (schema enum) → fetch /products/{handle}.js →
                               variant, stock, cents÷100 ≤ $50 → cart link built in code → work order raw.technician_part
                             • (T1) Twilio <Say> call to the teammate
                            Case page: verdict · part card with ticks · [Approve & open cart] → resolve → order done
```

## Documents
| Doc | What it holds |
|---|---|
| [`demo-clip.md`](demo-clip.md) | The 10–15 s beat: the "needs hands" story, scripts (26–37 words, one close to Kent's draft), a 5-shot list including the Naïve Studio cut, honesty rules, table and stage choreography |
| [`design.md`](design.md) | The spec: trigger, Naïve agent and session, hand-written report schema, gate and channels, one table, worker steps, adapters, **where each piece shows up in Romulus vs the Naïve Studio**, API, settings, events, draft D30, tests |
| [`build-plan.md`](build-plan.md) | 7:45 → 10:00: tiers, timeline with gates G0 (email, 8:15), G1 (spike, 8:45), go or break-glass (9:00), guaranteed clip (9:15), arm slot (9:30); file list; build brief; break-glass; cut order |
| [`review.md`](review.md) | The three reviews, what changed in v2, and what was deliberately not taken |

## Decisions for Kent, now
1. **D30, amending D11:** a person checks out. It's forced by Naïve agents being unable to pay (card issuing returns 501), not a new policy gate. Draft in `design.md` §12.
2. **The servo variant on our arm:** the C018 12 V (WowRobo variant `48037575033049`) or the C001 7.4 V (`48037674090713`), both $15.99.
3. **The teammate technician:** an email (and a phone for T1). They need to consent; never use a real business (the `parts.yaml` safety header).
4. **The live Naïve writes:** the agent, identity, system domain, inbox, and one test email (`build-plan.md` step 1). This Claude session can make them through the Naïve MCP once you OK each one.
5. **Which cloud runs the 1 PM demo:** it needs main plus `TECHNICIAN_MODE` (Kent's stack, or a teammate's after merging).
