# Romulus UI redesign: the Sunlit Workshop

_Sun Nov 8, 9:10 AM. **Built 9:15–10:03** through PRs #80 (foundation), #83 (Technician), #84 (incident, run, Data), #85 (Fleet), #88 (polish), #90 (learning) and #91 (Technician empty state); the lanes are in [`BUILD.md`](BUILD.md). It covers the Romulus cloud pages in `cloud/src/romulus_cloud/web/`: Fleet, incident, agent run, Technician case and Data. The method is Impeccable v4.8.1 ([repo](https://github.com/pbakaus/impeccable/tree/skill-v4.8.1)): `critique` to score what's there, Operate mode for the direction, the craft floor for the rules._

| File | What it is |
|---|---|
| **README.md** | Brief, critique, direction, page changes, build plan, guardrails |
| [**DESIGN.md**](DESIGN.md) | Tokens and components, in Impeccable's `document` format |
| [**PRODUCT.md**](PRODUCT.md) | Users, purpose and constraints, in Impeccable's `init` format |
| [**mock.html**](mock.html) | Open it in a browser. It has the Fleet and Technician screens, Day and Night themes, and a working Approve. The data is illustrative. |

## In one minute
- **The pages should answer two questions at a glance:** is the fleet OK, and is anything waiting on me? The one human step should read as good news, because it is the product working.
- **Direction:** a daylight ground, the system font (SF) only, and five state colours: leaf, sun, sky, clay and stone. The one signature element is **the heal line**, which draws each incident as a line of stations, with a sun-coloured station wherever a person stepped in.
- **The four biggest fixes:**
  1. "Waiting on you" stops being error red.
  2. The KPI tiles become one status sentence plus a "your turn" card.
  3. The Technician page joins the shared system: its purple band and its ~200 lines of scoped CSS go.
  4. Raw ids, JSON and the monospace chain leave the main read.
- **No new features.** Routes, data, polling and the JavaScript handlers all stay the same.
- **Build:** four steps, about 100 minutes. There's a **cut line after step 2**, because the Technician page is what gets filmed and shown at the table.

## 1. The brief
- **Who looks at these pages, and when:**
  - Judges at the 11:00 table, for about 30 s each: the verdict, the phone, Approve.
  - The 1 PM presentation, on a projector at 125–150% zoom.
  - Kent, the operator, who makes the one click ([`demo-clip.md`](../naive-technician/demo-clip.md)).
- **Kent's words:** minimalist, clean, Apple-esque, solarpunk, optimistic. Clear in its design language, cues and function. Joyful, professional-grade, well organised. Keep the functionality.
- **Impeccable's mode is Operate,** the mode for dashboards. "Scanability, consistency, native expectations, and the real usage scene outrank expression. Brand lives in precise details." The direction lends only "type, palette, density, and one signature move" ([mode-operate.md](https://github.com/pbakaus/impeccable/blob/skill-v4.8.1/skill/reference/mode-operate.md)).

## 2. Critique of the current UI
I scored the pages with Nielsen's ten heuristics, 0–4 each, following Impeccable's `critique` ([critique.md](https://github.com/pbakaus/impeccable/blob/skill-v4.8.1/skill/reference/critique.md)). The inputs were the templates and screenshots of the running `tech` stack, taken at 8:53 at 1440×900 in light and dark. Impeccable says "most real interfaces score 20-32 out of 40."

| # | Heuristic | Score | Key issue |
|---|---|---|---|
| 1 | Visibility of system status | 3 | Polling, a stale banner and live states all work. But "0/4 devices healthy" shows when the devices just haven't checked in yet |
| 2 | Match with the real world | 2 | `joint_stall`, `wo_3d7cd6464e2e4980a69acd0a3e031264 (needs human)`, `fw – · h –`, and a JSON blob in the first timeline step's "why" |
| 3 | User control and freedom | 3 | Deny, Unfreeze and "open the cart again" all exist. Freeze asks for a name through `prompt()` |
| 4 | Consistency and standards | 2 | Two visual languages: the base pages, and the Technician page's `.tc-*` CSS. Three button styles, browser-default controls on Data, and no current-page state in the nav |
| 5 | Error prevention | 3 | Gate ticks, confirmations, and honest "staged" and "email" states |
| 6 | Recognition over recall | 3 | Everything has a label. The one action sits two pages deep, behind a link inside a timeline step |
| 7 | Flexibility and efficiency | 2 | Fleet never surfaces "Approve the spare"; you have to dig for it |
| 8 | Aesthetic and minimalist design | 1 | See below |
| 9 | Error recovery | 3 | Errors show the server's own sentence, there's a stale-since banner, and there's a toast when the pop-up is blocked |
| 10 | Help and documentation | 2 | Good inline lines ("nothing is bought until…"). Empty states say `POST /v1/datasets` |
| | **Total** | **24/40** | Solid function under a noisy surface |

What's behind the 1 on heuristic 8:
- Everything is a card, and timeline steps are cards inside cards.
- A tracked-caps kicker sits above every block.
- There are coloured side stripes on rows, the verdict and the hypotheses.
- The page uses a purple-to-indigo band and gradient text.
- While a case is diagnosing, one screen runs six looping animations (ping, beat, breathe, shimmer, bouncing dots, blinking caret).
- Five equal tiles are mostly zeros.
- A red button sits on every page.

**What's working, and stays:**
- Status always comes with words and symbols, never colour alone (`romulus.css` header).
- Every step says who acted and why.
- The honesty mechanics: the recorded badge, "staged, not ordered", measured numbers.
- The case page leads with the verdict.
- It runs offline from one stylesheet, and it handles reduced motion.

**Priority issues:**
- **[P0] The demo's good news is styled as an alarm.**
  - `("diagnosed: waiting on you", "bad")` ([`story.py:1012`](../../cloud/src/romulus_cloud/domain/story.py)) puts a red pill and a red edge on Fleet. That's the one human step the pitch celebrates.
  - *Fix:* a fifth tone, `you` (sun), for states that need a person.
  - *Command:* `colorize`.
- **[P0] Two products in one.**
  - The Technician page brings its own palette (`linear-gradient(115deg, #2e1065, #5b21b6, #4338ca)`), its own type scale and 199 lines of scoped CSS ([`technician.html`](../../cloud/src/romulus_cloud/web/templates/technician.html), lines 8–206).
  - The detector flags purple gradients and gradient text (`.tc-shimmer`) as AI tells.
  - *Fix:* fold it into the shared system.
  - *Commands:* `extract`, then `polish`.
- **[P1] Fleet leads with a hero-metric grid of zeros.** It doesn't say whether you need to do anything.
  - *Fix:* a status sentence, plus a your-turn card.
  - *Commands:* `distill`, `layout`.
- **[P1] Plumbing in the main read.** Full ids, the event chain in monospace, empty firmware fields, JSON.
  - *Fix:* readable kinds, short ids (the full id goes in a `title`), raw details behind a disclosure.
  - *Command:* `clarify`.
- **[P2] Noise.**
  - Kickers above headings, which the craft floor bans outright.
  - Side stripes, the blurred glass header, six looping animations.
  - **Freeze fleet,** a solid red button that's always the loudest thing on the page. It's a rollout freeze ("Rollouts stop and every arm is pinned to its last good version", [`base.html`](../../cloud/src/romulus_cloud/web/templates/base.html)), not an emergency stop.
  - *Command:* `quieter`.
- **[P3] Data page:** browser-default inputs and buttons. It isn't in the demo, so it only gets the base form styles.

**Persona red flags:**
- **A judge at the table, first visit:**
  - They see two red things, "waiting on you" and Freeze, before the one green button.
  - They read a 32-character work-order id.
  - The case header cuts the summary off mid-word ("two clea…"), even at 100% zoom.
- **The presenter:**
  - Kent's Mac is in dark mode (`AppleInterfaceStyle` is `Dark`), so the pages render dark on stage by default.
  - Dark violet on a projector in a lit room is the weakest case *(inferred)*.

## 3. Direction: the Sunlit Workshop
*Machines mend themselves in daylight, and a person comes in only for the decisions that need one.*

**Sources:**
- **Solarpunk** is "an optimistic vision of the future that rejects environmental pessimism". It "is bright, with light often used as a motif", with "natural colors, bright greens and blues" ([Wikipedia](https://en.wikipedia.org/wiki/Solarpunk)).
- **Apple-esque** means the system family, quiet chrome, space, and "avoid Ultralight, Thin, and Light" weights ([HIG Typography](https://developer.apple.com/design/human-interface-guidelines/typography)).

What the direction lends (Operate allows exactly these four):

| Lends | Choice |
|---|---|
| Type | SF through the system stack, one family, a fixed rem scale at a 1.2 ratio. Drop `"Inter"`, which leads the stack today and which the detector flags as overused |
| Palette | A pale sage daylight ground, not cream (the detector flags cream as an AI default). Leaf means healthy or done, **sun means a person**, sky means working, clay means broken, stone means quiet. Violet is retired |
| Density | Lists, not cards. Hairlines, generous space, no kickers |
| Signature move | **The heal line.** Each incident becomes stations, with done in leaf and a person in sun. After Approve it holds exactly one sun station: "1 human click", made visible *(inferred)* |

**Theme:** Day is the default, because the table and the stage are lit rooms *(inferred)*. Impeccable says to pick the theme "from the use scene", not by category ([craft-floor.md](https://github.com/pbakaus/impeccable/blob/skill-v4.8.1/skill/reference/craft-floor.md)). Night stays available as a full variant (decision 1).

**One authored moment, Approve.** The human-click figure ticks 0 → 1, the last station turns into a sun check that reads "Approved by kent", and the part card's ring goes from sun to leaf. Everything else moves only to show a state change, in 150–250 ms ([operate.md](https://github.com/pbakaus/impeccable/blob/skill-v4.8.1/skill/reference/operate.md)).

## 4. Page by page (same function, new arrangement)
**Shell, on every page:**
- The nav reads Fleet · Technician · Data, and the current page is marked with `aria-current`.
- A static "● Live" label sits in the header. The existing stale banner replaces it when polls fail.
- Freeze fleet is a clay outline at rest. While the fleet is frozen, the header turns into a clay band with the reason and **Unfreeze**.
- The footer's "updates every 3 s" moves into the Live label's tooltip.

**Fleet (`/`):**
```
Romulus   Fleet  Technician  Data                    ● Live   ( Freeze fleet )

1 arm needs you. The rest are healthy.                        display-sm, sun on "needs you"
sim-1's elbow stalled. A Naïve technician diagnosed it and staged a spare.
3 incidents today · 2 healed by agents · 0 still broken · 1 waiting on a person

╭─ (sun ring) ───────────────────────────────────────────────────────────╮
│ ✋ sim-1 · Joint stall                                                  │
│    Approve a spare servo: Feetech STS3215 C018 12V, $15.99   [Review →] │
╰────────────────────────────────────────────────────────────────────────╯
Fleet  4 arms     [arm-1 ● healthy] [sim-1 ● parked] [sim-2 ● healthy] [sim-3 ○ no check-in yet]
Incidents
 ✋ sim-1 · Joint stall    elbow_flex stalled at 61°…               (Waiting on you)  8:44
    ●─●─●─● healer gave up · Naïve took over · emailed · waiting on you
 ✓  sim-2 · Camera covered …                                        (Healed)          8:12
```
- **The status sentence** picks the first case that's true, in this order:
  1. frozen
  2. needs you
  3. still broken
  4. agent working
  5. all healthy
  6. no check-ins yet

  It's built from the existing `overview` counts and the incident rows.
- **The your-turn card** shows only when an incident is in the `you` tone. It links to the technician case when one exists, and to the incident otherwise.
- **The mini heal line** replaces the monospace `chain`, using the same `links`.
- **Agent runs with no incident** go behind a disclosure: "Rehearsals and other runs (n)".

**Incident (`/incidents/{id}`):**
- **Header:** "sim-1 · Joint stall", the pill, the summary, a meta line, and a full heal line.
- **A your-turn callout,** "A Naïve technician diagnosed this and staged a spare. [Open the case →]". This link is buried inside a timeline step today.
- **The timeline loses its per-step cards.** Each step is a node on the rail, then "who · what", the time, and the "why" in secondary ink. The actor is an icon plus a word, and human nodes are sun.
- **JSON** in a "why" moves behind "details".
- **The agent-run block** keeps its order and only takes the new tokens. Its side stripes and kickers go.

**Technician case (`/technician/{case}`):** the purple band is gone, and so are the kickers above headings.
```
NAÏVE AGENT · technician  (● live session)              27 s        $0.08     0
sim-1 · Joint stall · elbow_flex stalled at 61°        Naïve session  cost   human clicks
✓━━━━━━━━━━━━━━━━✓━━━━━━━━━━━━━━━━━━✓━━━━━━━━━━━━━━━━━━◉
Healer gave up    Naïve took over    Technician emailed    Waiting on you

"Elbow jammed or servo failing; can't tell yet"            display 2.488rem, hanging quotes
(Verdict · medium confidence) (elbow_flex) (✓ spare part staged)     ← meta below, not a kicker

╭ sun ring ─────────────────────────────╮ ╭──────────────────────────────╮
│ Feetech STS3215 C018 12V      $15.99  │ │ (✓ Technician emailed · 08:44)│
│ Spare part · WowRobo · qty 1           │ │ sim-1 down: elbow stuck, …    │
│ (✓ allowlisted) (✓ in stock) (✓ price) │ │ to kent.y.gang@gmail.com      │
│ [Approve & open cart ↗]  [Deny]        │ │ From Romulus Technician       │
╰────────────────────────────────────────╯ ╰──────────────────────────────╯
Agent's work            9 steps · Naïve session ses_0s1zke…977w
How the agent got there  (below the fold, unchanged)
```
- **Every requirement in `demo-clip.md`, "Page requirements", still holds:**
  - the verdict is the largest text, at 42 px
  - the header wording
  - the stage words
  - real places in the feed
  - the four ticks on the part card
  - the cart opening inside the click
- **The recorded badge** becomes a solid ink pill on every card, because yellow now means a person.

**Agent run and Data:** these take the tokens and base component styles only. No layout work.

## 5. Build plan
Claim the work in `features.txt` first. Each step leaves `make test` green and can be merged on its own.

| Step | Time | What | Impeccable |
|---|---|---|---|
| **1. Tokens and atoms** | 25 min | **`romulus.css`:** <ul><li>the DESIGN.md tokens for Day and Night</li><li>drop `"Inter"`</li><li>`tone-you` and `chip-you`</li><li>buttons, pills, inputs</li><li>focus, selection, caret, scrollbar</li><li>remove the side stripes and the header blur</li></ul> **`api/web.py`:** where `story.status` is unpacked (`_timeline`, `_story_view`), map `technician.diagnosed`, `needs_human` and `blocked_over_cap` to `you`. That's a web-layer change only, so `domain/story.py` and its tests don't move. **`base.html`:** Day by default, `?theme=night` remembered | `colorize`, `typeset` |
| **2. Technician case** | 30 min | <ul><li>Move the `.tc-*` rules into `romulus.css` and delete the band, shimmer, dots, caret and ping</li><li>Meta goes under the verdict</li><li>Card titles become the headings</li><li>A sun ring while waiting</li><li>The ink recorded badge</li><li>The figure trio</li></ul> | `quieter`, `polish` |
| — | — | **Cut line.** If it's past 10:15, stop here, test and freeze | — |
| **3. Fleet and incident** | 30 min | <ul><li>The status sentence and the your-turn card</li><li>Row layout and the mini heal line (`chain.html`)</li><li>A `shortid` Jinja filter and readable kinds</li><li>The incident callout and the timeline without cards</li><li>SVG icons wherever this step touches markup</li></ul> | `distill`, `layout`, `clarify` |
| **4. Approve moment, then check** | 15 min | The `[data-live]` change helper in `base.html` (~15 lines) and the tick and station animation. Then verify (below) | `animate`, `audit` |

**Verify:**
- `make test`.
- Screenshots of `/`, an incident and a case at 1440×900, at 100%, 125% and 150% zoom, in Day and Night.
- One `technician_demo.py replay` run through to Approve ([`runbook.md`](../naive-technician/runbook.md)).
- Optionally, `npx impeccable detect http://localhost:8000/technician/<case>` ([detector docs](https://impeccable.style/docs/detector)).

## 6. Guardrails
- **Keep these exactly:**
  - the `#tech-case`, `.tc-actions` and `data-tech-*` hooks, and the poll-pause logic that depends on them
  - in the Approve handler, `window.open` runs before `await fetch` (a test checks the order)
  - four `tc-rec-tag`s on a replay
  - the stage labels, "human click", "Naïve agent", and the four gate labels
  - polling intervals and routes
- **Tests pin markup:**
  - [`test_technician_template.py`](../../cloud/tests/unit/web/test_technician_template.py) pins glyph strings like `✓ spare part staged` and `✕ Email failed`, plus `<li class="ok"`, `st-skipped` and `st-failed`.
  - [`test_f2_timeline_page.py`](../../cloud/tests/flows/test_f2_timeline_page.py) pins the exact `chain` spans, `actor-badge`, `doctor-ledger` and `status tone-bad`.
  - [`test_f13_agent_runs.py`](../../cloud/tests/flows/test_f13_agent_runs.py) pins `fw fw-bad` and `step step-quiet`.
  - If a step changes one of these, update the assertion in the same commit.
- **The domain stays put.** The `you` tone is a presentation mapping. Leave `("diagnosed: waiting on you", "bad")` in `story.py`, because [`test_f14_technician.py`](../../cloud/tests/flows/test_f14_technician.py) asserts it.
- **Offline:** no webfonts, no new scripts, no CDN.
- **The laptop cloud:** never `make reset` it ([`cloud/CLAUDE.md`](../../cloud/CLAUDE.md)).

## 7. Left out on purpose
New pages or features; charts and sparklines; a theme switcher beyond the query parameter; custom fonts or a serif "voice"; leaf icons, illustrations or textures; a Data page redesign; replacing Freeze's `prompt()` with an inline confirm; switching htmx to morph swaps (the change helper is enough for the one moment).

## 8. Decisions for Kent
_Build started 9:15. Kent asked for the whole redesign (all four steps), the learning page on :8001 included, so the build uses the recommended answers below. Kent can reverse any of them._
1. **Day as the default even when the OS is dark** (recommended: the table and the stage are lit, and your Mac is in dark mode), or follow the OS?
2. **Rename the nav's "Incidents" to "Fleet"?** It's the page's real subject, and no test pins that link.
3. **Scope before 11:00:** steps 1–2 only (recommended: about 55 min, with the demo page done), or all four (about 100 min)?
