# Fleet Doctor

**A strong cloud model that the robot's own healer can call on when it's stuck and online.** It gives advice first and a tested fix second; later it may investigate live through the healer's own tools. The device stays the only thing that ever changes the robot. Offline, nothing changes: the healer heals alone.

- **Status:** proposal, Sat Nov 7 ~5:45 PM PST. Nothing is decided; the decisions requested are D23–D27 in [`strategy.md`](strategy.md) §6.
- **Owners:** Kent (cloud) and Ishaan (device healer). Written from five research passes (prior art, connectivity, escalation and learning, verification and safety, and an audit of our own repo).
- **Builds on:** the healer (`docs/project-overview.md` §8), the cloud plans (`docs/cloud-platform-planning/`), and the archived Romulus "Fleet Doctor" (`~/git2/docs/archive/ARCHITECTURE.md`).

## Reading order

| # | Doc | Owns | Read it when |
|---|---|---|---|
| 1 | [`concept.md`](concept.md) | What it is, why, principles, the three levels of help, user stories, features, non-goals, the pitch | You want the idea |
| 2 | [`architecture.md`](architecture.md) | Parts, escalation triggers, L1/L2/L3 flows, the proposal lifecycle, the safety model, contract changes, fit with D0–D22, the learning loop | You're designing or building it |
| 3 | [`strategy.md`](strategy.md) | Phases, the demo, handoffs, risks, decisions requested, open questions, research backlog | You're deciding what to do next |
| 4 | [`research.md`](research.md) | Every outside fact, with links | You need a source |

## The idea in five lines

1. **L1 · Advise:** the healer calls `ask_doctor` when a trigger fires (stalled, looping, tier 3, a fix keeps failing). It gets back hypotheses and carries on, and a timeout means no answer.
2. **L2 · Consult:** when the healer gives up, the cloud diagnoses from the uploaded evidence, writes a reproduction test and a fix, checks them in the sandbox, and offers the fix at the next check-in. The device applies it as an overlay with a mark-good timer.
3. **L3 · Session (later):** the device opens a channel and the Doctor uses its tools live, under a lease, with typed tools only.
4. **Teach:** fixes that hold become known fixes and skills for the healer. **The escalation rate falls, and that's the pitch.**
5. **Fits what's decided:**
   - It reuses F7, D5a overlays, the D21/D22 adapters and the D6 gate.
   - It never offers safety-layer changes, never pushes to a device, and stops on the kill switch.
   - It needs one decision to square with D12.

## Rules for these docs

They follow the planning docs' rules (`docs/cloud-platform-planning/README.md`):
- One fact, one place: outside facts live only in `research.md`.
- (D#) cites the decision log.
- *(inferred)* marks our own reasoning.
- A decision is logged before the doc that owns its topic changes.
