# Naïve Technician: when the robot can't fix itself, the company does

_Kent, Sun Nov 8, ~7:30 AM PST. A sprint plan for the last hours before the 11:00 AM stop (`docs/hackathons.md:39`). Status: proposal, nothing built yet._

## The idea in one paragraph
Romulus already heals software faults on the arm and in the fleet. For hardware faults it has a **dead end**: when a fault kind has no entry in `cloud/config/parts.yaml`, the work order stops at `needs_human` with "a person must pick one" (`cloud/src/romulus_cloud/domain/triage.py:194-210`). Every real hard failure we can stage today ends there, because real devices send `joint_stall` and `us_no_echo` while the catalogue lists `servo_absent` and `ultrasonic_dead` (`cloud/config/parts.yaml`). The **Naïve Technician** takes over at that dead end, in three steps:
1. A Naïve agent session researches the failure and returns a typed diagnostic report.
2. Romulus's gate turns the report into real-world actions and checks each one in plain code. The technician email is sent on its own; the replacement part becomes a verified cart link a person approves.
3. A new Romulus page shows what the agent delivered, what it decided, and the single place a person stepped in.

It's the same loop Romulus runs on the arm: the model proposes, plain code checks, and the action is promoted or held. This time it runs at company scale *(inferred: the "one heal loop at arm, fleet and company scale" pitch, CLAUDE.md Romulus block)*.

## Why this is the right Naïve beat
The judges are told to look for "what its agents actually delivered, what decisions they made, and where you had to step in", and "judges will focus on how much of the company runs without you" (`docs/hackathons.md:24`). The Technician page answers those three questions on one screen, and it ends with the count: *N agent actions, N−1 on their own, 1 human click*.

## The loop
```
 arm / pod healer gives up            Romulus (Python)                          Naïve
 ─────────────────────────            ────────────────                          ─────
 incident: resolved=false,  ──►  triage: no catalogued part?  ──yes──►  open TechnicianCase
 cause=hardware (or a broken          (same predicate as work orders)          │
 agent run asking a person)                                                    ▼
                                      POST /v1/sessions  ───────────────►  "technician" agent (pi harness)
                                      (output_schema, budget, idem key)      web_search · web_fetch ·
                                                                             data_run (Amazon search) ·
                                      poll events + structured_output ◄───   browser (read only)
                                                                             → submit_output(report)
                                      GATE (plain code, per action):
                                       • email_technician → recipient from settings,
                                         links stripped → SEND via the Naïve inbox      ──►  technician's phone
                                       • order_part → re-fetch the vendor's product JSON,
                                         check price, stock, cap and vendor allowlist,
                                         build the cart link in code → WAIT for a person
                                       • call_technician (stretch) → Twilio <Say>       ──►  phone rings
                                      Technician page: report · live agent feed ·
                                      actions · [Approve & open cart] → real vendor cart
```

## Documents in this folder
| Doc | What it holds |
|---|---|
| [`demo-clip.md`](demo-clip.md) | The 10–15 s video beat: shot list, script variants (tightened from Kent's draft), on-screen text, live-demo choreography, fallbacks |
| [`design.md`](design.md) | The feature spec: trigger, Naïve agent and session, report schema, gate, channels, data model, worker steps, page, API, settings, events, tests, draft decision D30 |
| [`build-plan.md`](build-plan.md) | The sprint from now to the 10 AM freeze: tiers, go/no-go gates, owners, file list, setup commands, cut order |
| [`review.md`](review.md) | The critical review (demo legibility and impact, execution risk, technical soundness) and what changed because of it |

## Decisions this needs from Kent
1. **D30, a human approves the purchase.** D11 rejected "human approval for booking a technician" (`docs/cloud-platform-planning/cloud-platform-decisions.md:256-268`). This plan keeps booking autonomous and adds one approval, for spending money. Naïve agents can't pay anyway: card issuing returns 501 (`docs/raw/naive-llms-full.txt` F:2962). Draft text: `design.md` §12.
2. **The servo variant on our arm.** The part is the C001 7.4 V or the C018 12 V STS3215 (WowRobo variants `48037674090713` and `48037575033049`, both $15.99). Read the label or ask Andre.
3. **Who plays the technician.** A consenting teammate's email (and phone, for the stretch). It's never a real business (the `parts.yaml` safety header, K13).
4. **The live Naïve writes.** Creating the agent, identity, system domain and inbox (`build-plan.md` step 1).
