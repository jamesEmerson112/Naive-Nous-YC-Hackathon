# Product

<!-- impeccable:product-schema 1 -->

_Impeccable `init` format ([init.md](https://github.com/pbakaus/impeccable/blob/skill-v4.8.1/skill/reference/init.md)), written from Kent's brief (Sun Nov 8, 9:00 AM) and the repo. It skipped `init`'s interview round, so anything not in the brief or the repo is marked *(inferred)* for Kent to confirm. To let `/impeccable` commands find it, copy it to the project root._

## Platform

web

## Users
- **Judges at the table, 11:00.** About 30 seconds each: read the verdict, see the email on a phone, watch one click on Approve ([`demo-clip.md`](../naive-technician/demo-clip.md), "At the table").
- **The audience at the 1 PM presentation,** looking at a projector, usually through a 125–150% browser zoom (`demo-clip.md`, "On stage"; [`runbook.md`](../naive-technician/runbook.md)).
- **The operator (Kent),** who runs rehearsals and approves the spare part. He's the only person who clicks anything.

## Product Purpose
Romulus is the self-healing and fleet layer for an arm company. "We sell uptime: one heal loop at arm, fleet and company scale" ([`CLAUDE.md`](../../CLAUDE.md), Romulus block). The pages show what the agents did, why, and where a person stepped in. That's the hackathon brief: "The live demo shows what the agents delivered, what they decided, and where people had to step in" ([`project-overview.md`](../project-overview.md) §2). Success means a viewer can tell within a few seconds whether the fleet is fine and whether anyone needs to act *(inferred)*.

## Positioning
Rivals diagnose ([Alloy](https://theaiinsider.tech/2026/08/13/alloy-robotics-raises-8m-in-new-funding-to-help-engineers-debug-robot-fleets-with-ai-agents/)) or recommend ([Formant F3](https://www.businesswire.com/news/home/20250630008190/en/Formant-F3-Brings-Generative-AI-and-Agentic-Reasoning-to-Robot-Ops)). Romulus closes the loop, and a person touches it only to spend money or pull the kill switch (`CLAUDE.md`, Romulus block). The UI has to make that one human step visible and countable.

## Operating Context
- **Rooms:** the judging table at the YC office and the presentation room (`CLAUDE.md`). Both are lit rooms *(inferred)*. Kent's Mac is in dark mode, so the pages currently render dark by default (screenshots of the `tech` stack, 8:53 AM).
- The pages poll: htmx swaps each section every 3 s, and the Technician case every 2 s ([`base.html`](../../cloud/src/romulus_cloud/web/templates/base.html), [`technician_case.html`](../../cloud/src/romulus_cloud/web/templates/partials/technician_case.html)).
- The case is filmed for a 10–15 s clip and zoomed in one region per shot (`demo-clip.md`, shot list).

## Capabilities and Constraints
- **Pages:** Fleet and incidents (`/`), incident story (`/incidents/{id}`), agent run (`/agent-runs/...`), Naïve Technician case (`/technician/{case}`), Data (`/data`) ([`api/web.py`](../../cloud/src/romulus_cloud/api/web.py)).
- **Works offline at a venue:** one stylesheet, no external fonts or scripts ([`romulus.css`](../../cloud/src/romulus_cloud/web/static/romulus.css) header).
- **Status is never colour alone:** every pill, edge or icon also carries words or a symbol (`romulus.css` header).
- **The demo's acceptance criteria** come from `demo-clip.md`, "Page requirements":
  - the verdict is the largest text (≥ 36 px, ≤ 140 characters)
  - the header reads "NAÏVE AGENT · technician · live session"
  - the stages are "healer gave up → Naïve took over → … → waiting on you"
  - the gate ticks sit on the part card
  - Approve opens the cart inside the click handler
  - a replay shows its recorded badge in every frame
- **Tests pin markup:** class names, glyphs and labels in [`test_technician_template.py`](../../cloud/tests/unit/web/test_technician_template.py), [`test_f2_timeline_page.py`](../../cloud/tests/flows/test_f2_timeline_page.py) and [`test_f13_agent_runs.py`](../../cloud/tests/flows/test_f13_agent_runs.py).
- **Time:** hacking stops Sun Nov 8 at 11:00 AM PST; presentations are at 1 PM (`CLAUDE.md`).

## Brand Commitments
- **Name:** Romulus. **Voice:** plain English, and every step says who acted and why (`base.html` footer).
- **Brief words:** minimalist, clean, Apple-esque, solarpunk, optimistic, clear, joyful, professional-grade, well organised (Kent, Sun Nov 8).
- **Honesty rules:**
  - Say "staged a cart", never "ordered".
  - Label replays and speed-ups.
  - Put only measured numbers on screen (`demo-clip.md`, "Honesty rules").

## Evidence on Hand
- **The 8:44 rehearsal:** a 27 s Naïve session for $0.08, a real email, and the C018 spare staged at $15.99 (`CLAUDE.md`, Romulus block). It's live on the `tech` stack at `/technician/tc_a0177e9cb12f4ee394b91511f851a872`.
- **What doesn't exist yet:** there are no real "healed" incidents on the `tech` stack, so [`mock.html`](mock.html)'s other rows are illustrative and labelled that way.

## Product Principles
1. **Answer the two questions first:** is the fleet OK, and is anything waiting on me? *(inferred)*
2. **A person's step is good news, not an alarm.** The loop asking for one decision is the product working (Positioning).
3. **Show the work, not the plumbing.** The main read gets prose. Raw ids, event kinds and JSON go one click away (`demo-clip.md`, "What not to show").
4. **Measured, labelled, honest** (`demo-clip.md`, "Honesty rules").

## Accessibility & Inclusion
- Body and small text at 4.5:1 or better, large text at 3:1 ([Impeccable craft floor](https://github.com/pbakaus/impeccable/blob/skill-v4.8.1/skill/reference/craft-floor.md)).
- Status always carries a word or icon as well as colour (`romulus.css`).
- `prefers-reduced-motion` gets fewer, gentler animations, not none ([Impeccable animate](https://github.com/pbakaus/impeccable/blob/skill-v4.8.1/skill/reference/animate.md)).
- Pages stay legible at 150% zoom (`demo-clip.md`).
