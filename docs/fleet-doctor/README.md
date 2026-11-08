# Fleet Doctor

**A strong cloud model that the robot's healer can call on when it's stuck and online.** It gives advice first and a checked fix second; later it may investigate live through the healer's own tools. The device stays the only thing that ever changes the robot. With no connection, nothing changes: the healer heals alone.

- **Status:** proposal v2, Sat Nov 7 ~6:20 PM PST.
  - Written from five research passes: prior art, connectivity, escalation and learning, verification and safety, and an audit of our own repo.
  - Revised once after a red-team review.
  - Nothing is decided; the decisions requested are D23–D27 in [`strategy.md`](strategy.md) §6.
- **Owners:** Kent (cloud) and Ishaan (device healer).
- **Priority:** P2 under the cloud build plan. Nothing gets built tonight; L1 only once every P0 flow and every P1 item is done or blocked.
- **Builds on:** the healer (`docs/project-overview.md` §8), the cloud plans (`docs/cloud-platform-planning/`), and the archived Romulus "Fleet Doctor" (`~/git2/docs/archive/ARCHITECTURE.md`).

## Reading order

| # | Doc | Owns | Read it when |
|---|---|---|---|
| 1 | [`concept.md`](concept.md) | What it is, why, principles, the three levels of help, user stories, features, non-goals, the pitch | You want the idea |
| 2 | [`architecture.md`](architecture.md) | Parts, triggers, the L1/L2/L3 flows, the proposal lifecycle, safety, contract changes, fit with decisions, learning loop | You're designing or building it |
| 3 | [`strategy.md`](strategy.md) | Phases, the demo, handoffs, risks, decisions requested, open questions, research backlog | You're deciding what to do next |
| 4 | [`research.md`](research.md) | Every outside fact, with links | You need a source |

## The idea in five lines

1. **L1 · Advise:** when a trigger fires (looping, a fix keeps failing, tier 3, no matching skill), the healer's harness code sends the case to the Doctor. It injects up to 300 tokens of hypotheses and carries on. A timeout means no answer, and no more asks in that heal.
2. **L2 · Consult:** when a heal fails, the cloud diagnoses from the uploaded evidence, writes a reproduction test and a fix, checks them in the sandbox, and offers the fix at the next check-in. The device applies it as an overlay under a mark-good timer.
3. **L3 · Session (later):** the device opens a channel, and the Doctor uses its typed tools live, under a lease. First, a decision on where that agent loop runs (D27).
4. **Teach:** fixes that hold go through the existing lock-in and skill writing, and one digest metric tracks escalations.
5. **Fits what's decided:**
   - It reuses the evidence upload (flow F7), overlays (D5a), the adapters (D21, D22) and the gate (D6).
   - It stays out of the safety layer, never pushes to a device, and stops on the kill switch.
   - L1 and L2 need no change to D12; L3 does.

## Words used here

| Term | Meaning |
|---|---|
| **BUILD** | Kent's local `~/git2/docs/CLOUD-PLATFORM-BUILD.md` (not in the repo) |
| **I0, I6** | Handoffs in BUILD §5: I0 is the contracts walk-through with Ishaan; I6 is a cloud-written skill shipped to the healer |
| **`Id`, `>>`** | A stage that passes its input through unchanged; joining stages in order |
| **Mark-good timer** | After a change, it reverts unless health holds long enough to mark it good (from RAUC) |
| **Simplex** | A trusted, simple fallback that sits beside an unverified controller |

## Rules for these docs

They follow the planning docs' rules (`docs/cloud-platform-planning/README.md`):
- One fact, one place: outside facts live only in `research.md`.
- (D#) cites the decision log.
- *(inferred)* marks our own reasoning.
- A decision is logged before the doc that owns its topic changes.
