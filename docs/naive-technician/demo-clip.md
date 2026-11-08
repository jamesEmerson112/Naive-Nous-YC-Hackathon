# The demo clip: 10–15 seconds of Naïve Technician

_What the beat has to land, in order of priority:_
1. _Naïve agents took over when the robot couldn't fix itself._
2. _They touched the real world: an email, a phone, a real web store._
3. _A person stepped in exactly once._

_Anything that doesn't serve those three gets cut._

## The script
Your draft is about 50 words, roughly 20 s at a normal 150 words a minute. It also says "a part needs to be replaced" as a certainty. On a wedged elbow, an honest agent will rank *jam* next to *failed servo*. So the script should say what the agent did, not overclaim what it knew *(inferred)*.

**15 s version (about 36 words):**
> When the robot's own healer gives up, Romulus hands the case to a Naïve technician agent. It traces the stall to the elbow servo, emails a technician, and puts the exact replacement in a cart — one click for a human to approve.

**10 s version (about 25 words):**
> When the healer gives up, a Naïve technician agent takes over: diagnosis, technician emailed, replacement in the cart. A human makes one click.

**Captions only** (no voice-over; on-screen text does the work):
`Healer gave up → Naïve technician` · `Diagnosed in 2 min · $0.41` · `Technician emailed` · `Part in cart · waiting on you` · `1 human click`

## Shot list (15 s; drop shot 1 for the 10 s cut)
| t (s) | Shot | On screen | Must be true when filmed |
|---|---|---|---|
| 0.0–2.5 | **Arm**, close: the elbow strains against a foam block and the arm parks | lower third: `Elbow stalled · healer: 2 tries · can't fix → hand off` | the real arm, `stories stall --real` (`prototypes/arm-sim/MORNING.md:15`) |
| 2.5–6.0 | **Romulus Technician page**: the status rail goes Escalated → Diagnosed; feed lines tick in; the verdict appears | caption `Naïve agent · researching (2 min, sped up ×8)` | a real live session. Speed it up in the edit and **say so on screen** |
| 6.0–9.0 | **Actions column**: "✓ Emailed technician" → hard cut to a teammate's phone: the email notification from the Romulus Technician inbox (or the phone ringing, if T1 is built) | caption `Technician emailed (and called)` | the real Naïve inbox. The email really arrives |
| 9.0–12.5 | **"Order part · STS3215 · WowRobo · $15.99"** with the gate ticks → cursor clicks **Approve & open cart** → the real WowRobo cart page with the servo in it | caption `Human approves → real vendor cart` | the real Shopify cart link, built and price-checked by Romulus |
| 12.5–15.0 | **Back to the page footer**: `4 agent actions · 3 on their own · 1 human click` and the session cost | caption `1 human click` | the counter counts the real actions |

**Why this order.** Each shot is one idea, and the eye always has one big thing to read: the verdict, a phone, a cart, a number. The phone shot and the cart shot are "proof of the real world"; they're the most persuasive frames, so each gets at least 3 s *(inferred, standard demo editing)*.

## Making each frame legible (page requirements that come from the clip)
- **The verdict is the largest text on the page,** one line of 140 characters at most (the schema enforces it, `design.md` §2.1).
- **Status rail at the top:** four dots that fill. It reads at phone-video resolution where tables don't.
- **Feed lines name real places:** "Read shop.wowrobo.com/…", "Checked Amazon". These lines are what make "did internet research" credible in under a second.
- **Gate ticks under each action:** `✓ vendor ✓ in stock ✓ price ✓ under $50`. They show the human isn't approving blind, and that code, not the model, checked the money.
- **The footer counter** is the judging question as a number.
- **Before filming:** browser zoom at 125–150%, a dark or light theme matching the rest of the video, the cursor visible, notifications off except on the teammate's phone.

## Live demo at 1 PM (if presented live, not only as video)
A Naïve session takes minutes, not seconds *(inferred: about 10–15 model turns at a median of a few seconds each; measure it in the spike)*. So don't wait on it on stage:
1. **Start of the pitch:** wedge the elbow and run the stall. Say "it can't fix this one; it's escalated" and move on.
2. **Middle:** the other beats (software heal, rollback, fleet).
3. **End:** open the Technician page. The case finished while we talked. Read the verdict, show the phone, click **Approve & open cart** live.
4. **Fallback, decided before going on stage:** if the live case hasn't finished or failed, open the case recorded at 9:30 AM (`TECHNICIAN_MODE=replay`, with the "Replay of Naïve session …" badge visible). Say it's the run from this morning. Never present a replay as live (Constitution, Ethical).

## Capture checklist (by 10:00 AM, the code freeze)
- [ ] One clean live run on the real arm. Its session is exported to the replay file (`build-plan.md` step 6).
- [ ] A screen recording of the Technician page from escalation to done, 1080p or higher.
- [ ] A phone recording of the email arriving (and the call, if T1). Film the phone with another phone; screen recordings of notifications look fake *(inferred)*.
- [ ] A screen recording of Approve → the WowRobo cart.
- [ ] The arm shot of the stall and park. Reuse the stall story footage if it was already filmed.
- [ ] A backup: the whole beat run once in `replay` mode, for the backup video.

## What not to show
- **Naïve's dashboard live view.** It's withheld from API keys, so we can't embed it (F:20086), and it takes the eye off Romulus.
- **Raw JSON, logs, or the work-order `needs_human` row.** That's the old dead end; the Technician page replaces it.
- **Any claim that it "ordered" or "paid".** It staged a cart; a person checks out. "Puts it in a cart for a human to approve" is accurate, so keep that wording.
