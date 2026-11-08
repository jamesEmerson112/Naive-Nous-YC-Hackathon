# Naïve Technician: critical review (Sun Nov 8, ~7:30 AM)

_Three independent reviewers read v1 of these docs, each through one lens: demo legibility and impact, execution risk, and technical soundness and elegance. They checked the docs against the code, the Naïve docs dump, the live Shopify store and the running Docker state. This page records what they found, what changed in v2, and what was deliberately not taken. Findings are ranked within each lens._

## Verdict
- **The core shape held up:** one Naïve session, one typed report, and a plain-code gate that derives and executes the actions. The technical reviewer: "no blocking flaw in the core shape."
- **v1 wouldn't have landed.** Two blockers sat outside the code: no cloud running on Kent's laptop, and a foam jam that would self-resolve.
- **v1 also didn't fit the system cleanly.** It built a second approval path beside work orders, the digest would have been wrong, and the demo story contradicted itself on camera.

## Demo legibility and impact
| # | Finding | Change in v2 |
|---|---|---|
| 1 | Showing foam in the joint and then buying a servo reads as "they jammed it and the AI ordered a part." The v1 script overclaimed ("traces the stall to the elbow servo") | Reframed as **"Some faults need hands."** Verdict: "Elbow blocked or servo failing: needs hands." The technician clears the jam first and the servo is a **spare**. The jam stays visible on camera; cropping it out would mislead by omission (`demo-clip.md`) |
| 2 | The shot list was overpacked: 5 shots with captions of about 50 characters. "Sped up ×8" was wrong (it's about 38×). Numbers were made up | 5 short shots with captions of 5 words or fewer. **Only measured numbers.** Speed-ups are labelled |
| 3 | Romulus was visible but Naïve wasn't, which matters when the Naïve SDK is required and the judges are the Naïve team | A "NAÏVE AGENT · technician" header. Sender "Romulus Technician (Naïve agent)". "Naïve" said in the first 3 s. **A new 2 s shot in the Naïve Studio** showing the session transcript (also Kent's question, see `design.md` §8) |
| 4 | The counter "4 agent actions · 3 on their own · 1 human click" didn't add up and read as a gimmick | Replaced by **"2 min · $0.41 · 1 human click"**: speed, cost per incident and how often a person steps in, all measured |
| 5 | The 3-column page is unreadable at video resolution | The verdict at 36 px or more; one part card with the ticks and Approve, plus one email card; details below the fold; zoom per shot in the edit (`design.md` §8 wireframe) |
| 6 | Kent's script is about 50 words (roughly 20 s) and opens on jargon | Three scripts of 26–37 words, one of them kept close to Kent's wording (`demo-clip.md`) |
| 7 | It doesn't read as a company | A closing line: "a field-service dispatcher's job, done by a Naïve agent in two minutes for forty cents. Every arm in the fleet gets it" |
| 8 | Lock-screen subjects get cut off around 40 characters | Subject capped at **45 characters** in the schema. Film the opened email filling the frame |
| 9 | Live risks: a pop-up blocker on Approve; the phone invisible on stage; Wi-Fi; spam; table judging at 11 | The cart opens inside the click handler; the phone is mirrored to the projector; a hotspot; a pre-send not-spam check; a 30 s table version |
| 10 | Sponsor tracks would weaken the main story | Left out. AgentMail is only the honest fallback if Naïve's email returns 501 |

## Execution risk
| # | Finding (likelihood · impact) | Change in v2 |
|---|---|---|
| 1 | **Kent's laptop runs no Romulus cloud** (certain · blocker). The real data is on Olir's laptop and the arm reported to James's Mac; both run built images | Kent runs **his own stack**, `COMPOSE_PROJECT_NAME=tech make dev && make seed`, with the source mounted. The Pi is pointed at it with `ARMSIM_ROMULUS` for the 9:30 slot. Never deploy to Olir's laptop |
| 2 | **Foam won't hand off** (high · blocker). The checked move home succeeds, so the stall reports `resolved=True` and no case opens. Sim gives false confidence, and the real stall has never run on hardware | **Lock the elbow both ways** (a zip tie or a firm grip). Break-glass is James's `handoff` op. **The guaranteed clip is filmed at 9:15 from a sim incident**, so the arm slot is a bonus, not the critical path |
| 3 | **The backend lane was milestone-sized** (high · high). It's comparable to M5, a full test run takes 6 min 28 s, guard tests add hidden cost, and Lane C would churn rebasing on Lane B | T0 is cut down: one table, approval through the existing `resolve`, four event kinds, no list page, about 10 settings, an inner loop of 4 test files. **One session** builds the backend and the page |
| 4 | **G1 tested a different path** (TypeScript SDK not installed, method names unverified) from the one that ships (Python) | **The spike is the Python adapter.** Gotchas go in the build brief: cents prices, pack variants, `to` as an array, no message id |
| 5 | Naïve runtime: a 300 s timeout is too short; `ask_operator` parks the session; the key's scopes; credits; deliverability; switching to replay needed a restart | Timeout 600 s and the browser cut; the prompt forbids `ask_operator`; a scope check at 7:55; a balance check; **G0, the test email at 8:15; replay chosen per case**, so the fallback is a browser tab |
| 6 | Physical: about 6–8 s of stall load per take; the C018 heats up | At most 2 takes, with 2 minutes between them |
| 7 | Coordination: the Pi runs James's branch; Olir edited the templates this morning | Only a link in James's UI; a one-line `base.html` change; pull main before merging |

## Technical soundness and elegance
| # | Finding | Change in v2 |
|---|---|---|
| 1 | The trigger repeated `triage_new`'s logic and relied on a `parts.yaml` gap that someone might "fix". Same-tick events would sort `technician.opened` before `workorder.created` | **The trigger reads the work-order row** (`needs_human` with part `(none)`). A comment and a test explain why ambiguous kinds stay uncatalogued. `technician.*` gets `STAGE` > 62 |
| 2 | **It was a parallel approval system.** `resolve` refuses `NO_PART` with a 409, so the order would wait forever. The digest's waiting list and its `ACTION_KINDS` would be wrong. Orders would skip the daily caps | **Approval goes through `workorders.resolve`**, with one new branch for `raw.technician_part`. `TechnicianAction` is dropped: one table, with actions as JSONB. `technician.emailed` is added to `ACTION_KINDS` |
| 3 | Idempotency holes: the session key is bound to the body, so a rebuilt case file gets a 409; the email had no key, so a crash could send it twice. **Factual error:** the email reply has no message id | The request is frozen in `case.request`. The email has an `Idempotency-Key`, with "sending" committed before the call. The receipt fields are corrected |
| 4 | `STATUS` holds fixed text, so "approved by {who}" can't be expressed | The text is fixed per kind; the badge after approval comes from the existing `WORK_ORDER_STATUS` |
| 5 | State machine gaps: unhandled `stop_reason`s; no session cancel; no stop when the incident clears; no retry when validation fails; the cost cap missed failed runs; one exception would roll back every case | Every `stop_reason` is mapped. Cancel on timeout or stop. A stop check every tick and a Stop button. One retry through `POST …/messages`. The daily cap sums the cases' `cost_usd`. A savepoint per case and 5 s timeouts |
| 6 | Let the schema do the gate's work | `origin` is an enum of the allowlisted vendors, with patterns on `handle` and `variant_id`. Prices and stock are dropped from the schema, since code fetches them |
| 7 | The tool policy should default to deny; the browser is waste in T0; test outbound mail early (it can 501) | `default_config: deny` with four tools allowed, no browser, and G0 at 8:15 |
| 8 | Trim the schema; Pydantic JSON Schema output has `$ref`, `anyOf` and `format` problems | `evidence`, `urgency` and the price fields are cut. `joint` and `part_needed` (`yes / no / spare`) are added. The schema is hand-written and pinned by a snapshot test |
| 9 | Keep the email in Romulus, not the agent's `email.send` | Kept. The session is created with `identity` so it belongs to the persona, and the page labels the email honestly |
| 10 | D30 understated what it changes: D11 made orders automatic at any amount | D30 now says "amends D11", frames checkout as a hand-off forced by a missing capability (like D19b), and mentions D12 |
| 11 | Delete: the action table, Twilio, the browser, `TECHNICIAN_SCOPE`, a separate replay class, a separate HTTP client | All deleted **except Twilio**, which stays as optional T1 (see below) |
| — | **Citation errors** in v1: F:20498 is a computer VM boot, not a session; the auth headers are in `adapters/naive.py` at `:90-93`, not `:137-150`; there's no email message id; `TECHNICIAN_DEVICE_KINDS=real` would quietly run a sim rehearsal fake | All fixed. `TECHNICIAN_DEVICE_KINDS` is dropped |

## Deliberately not taken
- **Dropping the phone entirely (technical #11).** Kent asked to show email, phone *and* websites. Naïve can't phone (SMS 501, voice false), so a phone needs Twilio. It stays as **T1, optional and clearly labelled**, behind the same gate, and second in the cut order.
- **Showing the approval in the Naïve Studio from the start.** That's approval option B: an `ask_operator` card the Studio and Romulus both answer. It's the most Naïve-native option, but it holds the session open and makes the two sides sync. **It's T2**; the T0 approval lives on the work order.
- **A different hard failure (the pod's `us_no_echo`).** Its evidence is more honest, since a real healer said "hardware" on real hardware. But it needs the T2 trigger (pod incidents can arrive with cause `unknown`), the sensor is tiny on camera, and it's Olir's bench. It works as a **second case** if T2 lands.
