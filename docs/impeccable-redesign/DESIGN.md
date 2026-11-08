---
name: Romulus
description: Self-healing fleet. The Sunlit Workshop, where machines mend themselves and a person is called only for the decisions that need one.
colors:
  ground: "oklch(97.4% 0.009 150)"
  surface: "oklch(99.4% 0.004 150)"
  well: "oklch(95.2% 0.013 150)"
  line: "oklch(90.5% 0.014 150)"
  line-strong: "oklch(84% 0.018 150)"
  moss-ink: "oklch(24% 0.025 160)"
  moss-ink-2: "oklch(38% 0.022 160)"
  stone: "oklch(51% 0.02 158)"
  leaf: "oklch(58% 0.145 150)"
  leaf-ink: "oklch(44% 0.11 152)"
  leaf-wash: "oklch(94.5% 0.04 150)"
  on-leaf: "oklch(99% 0.01 150)"
  sun: "oklch(82% 0.155 82)"
  sun-ink: "oklch(48% 0.11 68)"
  sun-wash: "oklch(96% 0.05 90)"
  sky: "oklch(62% 0.12 235)"
  sky-ink: "oklch(47% 0.11 240)"
  sky-wash: "oklch(95.5% 0.025 230)"
  clay: "oklch(57% 0.155 35)"
  clay-ink: "oklch(49% 0.15 33)"
  clay-wash: "oklch(95.5% 0.03 40)"
typography:
  caption: { fontFamily: "system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', sans-serif", fontSize: "0.833rem", fontWeight: 500, lineHeight: 1.45 }
  body: { fontFamily: "system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', sans-serif", fontSize: "1rem", fontWeight: 400, lineHeight: 1.5, fontFeature: "tnum" }
  title: { fontFamily: "system-ui, -apple-system, sans-serif", fontSize: "1.2rem", fontWeight: 650, lineHeight: 1.3, letterSpacing: "-0.01em" }
  headline: { fontFamily: "system-ui, -apple-system, sans-serif", fontSize: "1.44rem", fontWeight: 680, lineHeight: 1.25, letterSpacing: "-0.015em" }
  figure: { fontFamily: "system-ui, -apple-system, sans-serif", fontSize: "1.728rem", fontWeight: 700, lineHeight: 1.1, letterSpacing: "-0.02em", fontFeature: "tnum" }
  display-sm: { fontFamily: "system-ui, -apple-system, sans-serif", fontSize: "2.074rem", fontWeight: 700, lineHeight: 1.15, letterSpacing: "-0.022em" }
  display: { fontFamily: "system-ui, -apple-system, sans-serif", fontSize: "2.488rem", fontWeight: 700, lineHeight: 1.12, letterSpacing: "-0.024em" }
  mono: { fontFamily: "ui-monospace, 'SF Mono', Menlo, monospace", fontSize: "0.88em" }
rounded:
  focus: "8px"
  control: "12px"
  card: "16px"
  pill: "999px"
spacing:
  "1": "4px"
  "2": "8px"
  "3": "12px"
  "4": "16px"
  "6": "24px"
  "8": "32px"
  "12": "48px"
  "16": "64px"
components:
  button-primary: { backgroundColor: "{colors.leaf-ink}", textColor: "{colors.on-leaf}", rounded: "{rounded.control}", padding: "12px 22px", typography: "{typography.body}" }
  button-secondary: { backgroundColor: "transparent", textColor: "{colors.moss-ink}", rounded: "{rounded.control}", padding: "12px 22px" }
  button-danger-quiet: { backgroundColor: "transparent", textColor: "{colors.clay-ink}", rounded: "{rounded.pill}", padding: "6px 14px" }
  pill-good: { backgroundColor: "{colors.leaf-wash}", textColor: "{colors.leaf-ink}", rounded: "{rounded.pill}", padding: "3px 11px" }
  pill-you: { backgroundColor: "{colors.sun-wash}", textColor: "{colors.sun-ink}", rounded: "{rounded.pill}", padding: "3px 11px" }
  pill-work: { backgroundColor: "{colors.sky-wash}", textColor: "{colors.sky-ink}", rounded: "{rounded.pill}", padding: "3px 11px" }
  pill-bad: { backgroundColor: "{colors.clay-wash}", textColor: "{colors.clay-ink}", rounded: "{rounded.pill}", padding: "3px 11px" }
  pill-quiet: { backgroundColor: "{colors.well}", textColor: "{colors.stone}", rounded: "{rounded.pill}", padding: "3px 11px" }
  card: { backgroundColor: "{colors.surface}", rounded: "{rounded.card}", padding: "22px 24px" }
---

# Romulus design system: the Sunlit Workshop

_Impeccable `document` format ([document.md](https://github.com/pbakaus/impeccable/blob/skill-v4.8.1/skill/reference/document.md)). The tokens above are the source of truth. [`mock.html`](mock.html) renders them, and [`README.md`](README.md) is the plan. To let `/impeccable` commands find this file, copy it to the project root._

## Overview
- **Mode:** Operate. Romulus is a working dashboard, so "scanability, consistency, native expectations, and the real usage scene outrank expression" ([mode-operate.md](https://github.com/pbakaus/impeccable/blob/skill-v4.8.1/skill/reference/mode-operate.md)).
- **What the world lends:** in Operate mode a direction contributes four things and nothing else: type, palette, density and one signature move. The layout, navigation and controls stay standard web ones.
- **North Star:** *The Sunlit Workshop.* Machines mend themselves in daylight, and a person comes in only for the decisions that need one.
- **Where that comes from:**
  - **Solarpunk:** "an optimistic vision of the future that rejects environmental pessimism". It "is bright, with light often used as a motif", and it uses "natural colors, bright greens and blues" ([Wikipedia](https://en.wikipedia.org/wiki/Solarpunk)).
  - **Apple:** the system family, quiet chrome, generous space, and regular to bold weights only. The HIG says "avoid Ultralight, Thin, and Light font weights" ([HIG Typography](https://developer.apple.com/design/human-interface-guidelines/typography)).
- **Signature move:** *the heal line.* Each incident is drawn as a short line of stations, one per stage the loop passed through:
  - A finished station is leaf.
  - A station where a person acted, or needs to, is sun.
  - A working station is sky, and a failed one is clay.

  After an approval the line holds exactly one sun station, so "1 human click" can be seen as well as read *(inferred)*. Nothing else on the page is decorative.
- **Theme:** chosen from where it's used, not by habit ([craft-floor.md](https://github.com/pbakaus/impeccable/blob/skill-v4.8.1/skill/reference/craft-floor.md)). The judging table and the stage are lit rooms *(inferred)*, so **Day is the default**. Night is a full variant for the operator.

## Colors
- **Ground** (`ground`): a pale sage daylight, deliberately not cream. The Impeccable detector flags cream and beige grounds as an AI default (`cream-palette`, [antipatterns.json](https://github.com/pbakaus/impeccable/blob/skill-v4.8.1/crates/live/assets/antipatterns.json)).
- **Surfaces:**
  - `surface` is for cards and the header.
  - `well` is the second neutral layer, for nav selection, row hover and code. Operate mode asks for "a second neutral layer" ([operate.md](https://github.com/pbakaus/impeccable/blob/skill-v4.8.1/skill/reference/operate.md)).
- **Moss Ink** (`moss-ink`, `moss-ink-2`, `stone`): text, from primary down to secondary. The neutrals are tinted green because the leaf brand hue ties them together.
- **The five states.** Each colour means one thing everywhere, and it always comes with a word or an icon:

  | Tone | Means | Used for | Old tone |
  |---|---|---|---|
  | **Leaf** `good` | healthy, fixed, done | heal-line stations, healthy dots, the primary button (`leaf-ink`) | `good` |
  | **Sun** `you` | a person: waiting on you, or a person acted | the your-turn card, the "waiting on you" pill, the human-click figure, human timeline nodes | was `bad` for needs-person states |
  | **Sky** `work` | working, automation | agent working, on canary, the email card icon | `wait` |
  | **Clay** `bad` | broken, failed, rolled back | still broken, gate error, frozen | `bad` |
  | **Stone** `quiet` | info, superseded, no check-in yet | | `info` |

- **The violet agent colour (`#7c3aed`) and the purple band are retired.** "Purple/violet gradients and cyan-on-dark are the most recognizable tells" (`ai-color-palette`, antipatterns.json).
- **Contrast,** checked from these OKLCH values *(computed)*:
  - Day text pairs run 5.3–15.2:1, and Night 6.6–16.4:1.
  - The primary button is 7.2:1 in Day and 8.0:1 in Night.
  - The white tick on a leaf dot is 3.9:1. That's an icon, so it needs 3:1.
- **Night** values are in `mock.html`. They aren't a mechanical inversion: grounds sit at oklch 15–21% and the state inks are lifted to 79–86% L.

## Typography
- **One family: SF, through the system stack.**
  - "One family is often right" ([operate.md](https://github.com/pbakaus/impeccable/blob/skill-v4.8.1/skill/reference/operate.md)), and Operate mode permits system fonts.
  - Drop `"Inter"`, which today heads the stack in `romulus.css`; the detector lists it as an overused font.
  - No webfonts, so it works offline.
- **A fixed rem scale at a 1.2 ratio,** never fluid:
  - caption 0.833
  - body 1
  - title 1.2
  - headline 1.44
  - figure 1.728
  - display-sm 2.074
  - display 2.488

  With the 17 px root, the verdict at `display` is 42 px, above the demo's 36 px floor (`demo-clip.md`).
- **Settings:**
  - Weights 400–700.
  - Tracking no tighter than -0.024em.
  - `text-wrap: balance` on headings.
  - Tabular numerals throughout.
- **Mono** only for real data: times in the feed, ids and the joint name. Never as a "technical" costume, such as the incident chain that's set in mono today.
- **No kickers above headings.** Impeccable's craft floor bans them outright, and today there's one on every block (VERDICT, SPARE PART, AGENT'S WORK, TECHNICIAN EMAILED). The heading carries itself, and meta goes after it.

## Layout
- Content is 72rem wide with a 24 px gutter (16 px on phones), as today.
- Spacing comes from the 4 px scale above. There's more space above a heading than below it.
- **Lists, not cards.** Incidents are rows in one surface, with hairlines between them. Cards hold only units you act on or read as one thing: a device, the part, the email, the agent feed. There are never cards inside cards.
- Below 52rem the grids drop to one or two columns, and the heal line turns vertical.

## Elevation & Depth
- **One raised level**, a soft offset shadow on `card`. Elevation is declared once, as a shadow, never as a border plus a shadow.
- **State rings** are the only emphasis: a 1.5 px sun ring with an offset sun shadow marks "your turn", and it turns to a leaf ring once approved.
- The header is a flat surface with a hairline. The current backdrop blur goes: Impeccable refuses "glass and blur as decoration".

## Shapes
- Radii: 16 px cards, 12 px buttons and controls, 999 px pills, 8 px focus rings.
- Icons are one authored SVG set in a 2 px stroke: check, cross, hand, arrow, external, mail, pause, alert, plus the brand mark, a sun on a horizon. They replace the Unicode ✓ ✕ ⚠ ▸ ↗ glyphs (craft floor: "Unicode glyphs or emoji standing in for an icon system").

## Components
- **Header:**
  - The mark plus "Romulus".
  - The nav (Fleet, Technician, Data), with `aria-current` on a `well` pill.
  - A static "● Live" label, which the stale banner replaces when polls fail. The pulse is kept for data that's really changing (`pulsing-dot`).
  - **Freeze fleet:** a quiet clay outline at rest. While the fleet is frozen, the header turns into a clay band: "Fleet frozen · reason · Unfreeze". It's a rollout freeze ("Rollouts stop and every arm is pinned to its last good version", `base.html`), not an e-stop, so at rest it shouldn't be the loudest thing on the page.
- **Status sentence** (Fleet): `display-sm`, then one line of prose, then the day's numbers inline. It replaces the KPI tiles, which the craft floor refuses as the hero-metric template.
- **Your-turn card:** a sun ring, a hand icon, what's waiting, and one sun button ("Review →"). It's on Fleet whenever something needs a person, and on the incident page when that incident has a case.
- **Incident row:**
  - A tone icon.
  - The device and a readable kind ("Joint stall", not `joint_stall`).
  - A one-line summary and the mini heal line, with the stage words beside it.
  - The pill and the time on the right.
- **Heal line:** full size on the Technician case and the incident header, mini on rows. It's built from the existing `links` and `stages`, with no new data.
- **Figure trio** (case header): measured numbers with labels under them (27 s, Naïve session; $0.08, cost; 0, human clicks). The human-click figure is sun.
- **Part card:**
  - The title as the heading, with "Spare part · vendor · host · qty" under it, and the price at `display-sm`.
  - Gate ticks as leaf pills.
  - **Approve & open cart** as primary and **Deny** as secondary.
  - A fine-print line: "nothing is bought until a person checks out".
- **Email card:** the subject as the heading, a "✓ Technician emailed · time" pill, then the recipient and the sender.
- **Feed:** mono times and prose lines; the last line is in `moss-ink`. No blinking caret, which the detector flags as `blinking-cursor`.
- **Timeline step** (incident): a node on the rail, then "who · what" with the time and the "why" in `moss-ink-2`. No card per step. Actor is an icon plus a word. The node is sun for a human step, so a person's steps can be counted at a glance.
- **Recorded badge:** a solid `moss-ink` pill, "● Recorded Naïve session …", kept on every card. It's not yellow any more, because yellow now means "a person".
- **Browser surfaces:** `accent-color`, `caret-color`, `scrollbar-color`, `::selection` (a sun wash), and a `:focus-visible` ring in leaf at 35%. Impeccable's craft floor says to theme these from the palette.

### Motion
- 150 ms for hover, 250 ms for state changes, eased with `cubic-bezier(.16, 1, .3, 1)`. No bounce, and no page-load sequence ([operate.md](https://github.com/pbakaus/impeccable/blob/skill-v4.8.1/skill/reference/operate.md)).
- **One authored moment: Approve.**
  1. The human-click figure ticks 0 → 1 with a brief sun wash.
  2. The last station turns into a sun check that reads "Approved by kent".
  3. The part card's ring moves from sun to leaf.
- **Polling swaps the DOM every 2–3 s,** so a ~15-line `htmx:afterSwap` helper compares each `[data-live]` element's value with the one before and adds `.is-changed` only to the ones that changed. Without it, every entrance animation would replay on every poll.
- **The only loop** is the soft ring on a sun station that's waiting.
- **Reduced motion:** the same changes, near-instant, with no loop.

## Do's and Don'ts
- **Do** say what happened in words, beside the colour. **Don't** use colour alone.
- **Do** show prose first and put ids in a `title` or a disclosure. **Don't** put `wo_3d7cd6464e2e4980a69acd0a3e031264` or a JSON blob in the main read.
- **Do** keep one primary button per screen. **Don't** let Freeze compete with Approve.
- **Don't** use any of these:
  - gradient text (`.tc-shimmer`)
  - purple bands
  - coloured side stripes over 1 px (incident rows, verdict, hypotheses, firmware rows)
  - kickers
  - bouncing dots
  - more than one looping animation per screen
- **Don't** add leaf icons, illustrations, textures or a serif "voice". The world lends type, palette, density and the heal line, and nothing else.
