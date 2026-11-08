# The demo clip: 10–15 seconds of Naïve Technician (v2)

_What the beat has to land, in order:_
1. _Some faults need hands, and the robot knows it._
2. _A **Naïve** agent took the case._
3. _It touched the real world: a technician's inbox and a real web store._
4. _A person stepped in exactly once, to spend money._

_Anything that doesn't serve those four gets cut. Numbers on screen are **measured** in the spike or the live run, never typed in. Measured this morning on the live agent (`claude-sonnet-5.5`): sessions take 26–33 s and cost $0.07–$0.08 (spike runs and the 8:44 rehearsal). Re-read the numbers off the case you film._

## The story: "Some faults need hands"
The fault is a jammed elbow, and that should be visible on camera. Nobody needs it explained that a robot can't pull out its own jam, so the jam makes "can't fix itself" obvious at once. It also sets up the honest verdict: *"Elbow blocked or servo failing: needs hands."*
- The technician is told to **clear the jam first**.
- The servo is staged as a **spare**, in case the stall damaged it.
- The human click is then a real judgment, not a rubber stamp.

The v1 framing ("traces the stall to the elbow servo… the exact replacement") would show foam in the joint and then buy a motor. A judge would read that as "they jammed it and the AI ordered a part." Cropping the jam out to sell a "failed servo" story would mislead by omission, so don't do that either *(review, demo #1)*.

**How to jam it.** Lock the elbow **in both directions**: a zip tie around the forearm and upper arm, or a firm grip that holds through the move back home.
- **Why foam alone fails.** It blocks only the reach, so the healer's checked move home succeeds, and the stall is then reported `resolved=True` and no case opens (`armsim/host.py`'s `verify` counts it fixed once the home move arrives). Rehearsing in sim doesn't catch this, because sim's `joint_stuck` blocks both ways *(review, execution #2)*.
- **Limits.** At most 2 takes, with 2 minutes between them, because a C018 heats up at stall. The servo stalls at about 2.94 N·m (`armsim/real.py`).

## The script
**Kent's draft:**
- It's about 50 words, roughly 20 s.
- It opens on jargon ("integrate with the platform").
- "It's concluded a part needs to be replaced" overclaims.
- "Added it to a cart" is slightly off: the cart comes into being when a person opens the link.

**15 s, recommended (37 words):**
> Some faults need hands. The healer can't clear this jam, so a Naïve agent takes the case: diagnoses it, emails a technician what to check, and puts a spare servo in a cart. Our one step: approve.

**15 s, closest to Kent's draft (35 words):**
> Naïve agents take over when the robot can't fix itself. Here it diagnosed a jammed elbow, emailed a technician what to check, and staged a spare servo in a cart for a human to approve.

**10 s (26 words):**
> Some faults need hands. A Naïve agent takes the case: diagnoses it, emails a technician, puts a spare servo in a cart. We click once.

Say "Naïve" in the first 3 s: the Naïve SDK is required and the judges are the Naïve team *(review, demo #3)*.

## Shot list (15 s; for the 10 s cut, drop shot 1 if the beat before ends on the arm, and keep shot 2 to 1.5 s)
| t (s) | Shot | Caption (5 words or fewer) | Must be true when filmed |
|---|---|---|---|
| 0–3 | **Arm**: the locked elbow strains and the arm parks; the story banner flips to "healer gave up → Naïve technician" | Some faults need hands | the real arm, or labelled footage (see the honesty rules below) |
| 3–5 | **Naïve Studio**: the technician session's chat, with tool calls scrolling (`web_search`, `web_fetch shop.wowrobo.com/…`) | Naïve agent on the case | the same live session as shot 3 |
| 5–7 | **Romulus page, zoomed**: the verdict lands under the "NAÏVE AGENT · technician" header | Diagnosed in 27 s | the measured duration. Say the speed-up on screen if it's sped up (27 s into 2 s is about 14×) |
| 7–10 | **Phone filling the frame**, with the email open. From: "Romulus Technician (Naïve agent)". Subject: `arm-1 down: clear elbow jam, spare servo ready` (45 characters at most, so it reads on a lock screen) | Technician emailed | the real email arrived in a teammate's inbox. Film the phone with another phone |
| 10–15 | **Spare-part card** with four ticks → the cursor clicks **Approve & open cart** → the **real WowRobo cart**, held 1.5 s → overlay `27 s · $0.08 · 1 human click` | You approve. That's it. | the cart link was built and price-checked by Romulus; the overlay numbers come from the case |

**Why this order.** Each shot is one idea with one big thing to read: a jam, a transcript, a verdict, a phone, a cart. The two "proof of the real world" frames, the phone and the cart, get at least 3 s each.
- **Shot 2 is new in v2.** It shows the run in Naïve's own UI: the strongest single frame for "this is really Naïve."
- **The overlay replaces v1's separate counter shot.** v1's "4 agent actions · 3 on their own · 1 human click" didn't add up, since the human click isn't an agent action and tool calls weren't counted. The overlay is the business in three numbers: speed, cost per incident and human involvement *(review, demo #2, #4)*.

## Honesty rules for the edit
- **Match the arm to the incident.** If the page's incident came from a sim device, either leave the real arm out of the beat or caption the arm shot "re-enacted". Never splice footage so a sim incident looks like the real arm's.
- **Label speed-ups.** Say on screen how much a shot is sped up.
- **Label replays.** A replayed case shows its "Recorded Naïve session" badge in every frame.
- **Say "staged a cart", never "ordered" or "paid".**

## Page requirements that come from the clip
These are acceptance criteria, not polish (`design.md` §8):
- **The verdict** is the largest text on the page, 36 px or more and 140 characters at most. Hypotheses, sources and gate details go below the fold.
- **The header** reads "NAÏVE AGENT · technician · live session" (or the replay badge) in large type.
- **The story banner** reuses James's "broke → parked → healing → …" wording, extended with "healer gave up → Naïve took over → waiting on you". Viewers will already know that banner from the earlier beats.
- **Feed lines name real places:** "Read shop.wowrobo.com/…", "Checked Amazon".
- **The gate ticks** sit on the part card: `✓ allowlisted vendor ✓ in stock ✓ price checked ✓ ≤ $50`.
- **Approve opens the cart inside the click handler,** so no pop-up blocker fires.
- **Before filming:** browser zoom 125–150%, the cursor visible, notifications off everywhere except the teammate's phone. In the edit, zoom into one region of the page per shot.

## At the table (11:00 judging) and on stage (1 PM)
**30-second table version.**
1. Open the finished case page and read the verdict.
2. Hand over the teammate's phone, with the email open.
3. Click Approve; the WowRobo cart opens.
4. When asked where you stepped in: "Once, to spend money. The technician is a contractor the agent dispatched."

**On stage.** A session takes minutes *(measure it in the spike)*, so don't wait for it live.
1. **Before going on:** if the spike took more than about 90 s, jam the arm before the pitch and say so. **Pre-open the replay case in a tab.**
2. **During the other beats:** the case runs.
3. **End:** open the case. If it's done, read the verdict, show the phone (mirrored to the projector, since a phone held up on stage is invisible) and click Approve live.
4. **Fallback:** if it isn't done, switch to the replay tab, with its badge visible, and say "this is the run from this morning." No restart needed, because replay is chosen per case.
5. **Logistics:** bring a hotspot, since the Naïve API, the email and WowRobo all need internet. Don't use sound: a Twilio trial reads a notice first.

**Line for the business story, using measured numbers only:**
> That's a field-service dispatcher's job, done by a Naïve agent in under thirty seconds for eight cents. Every arm in the fleet gets it.

## What not to show
- **Naïve's browser live view.** It can't be embedded, because it answers only a signed-in Studio session (F:20086), and the T0 agent has no browser anyway.
- **Raw JSON, logs, or the old `needs_human` row.** The Technician page replaces that dead end.
- **The sponsor tools (AgentMail, Mobilerun, Monid).** They'd weaken the main-track Naïve story. The only exception: AgentMail is the email fallback if Naïve's outbound mail returns 501, labelled honestly (`design.md` §4) *(review, demo #10)*.
