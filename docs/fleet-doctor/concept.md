# Fleet Doctor: concept

- **Status:** proposal, Sat Nov 7 ~5:45 PM PST. Nothing here is decided. The decisions it asks for are D23–D27 in [`strategy.md`](strategy.md) §6.
- **Owns:** what the Fleet Doctor is, why it exists, its principles and features. How it works is in [`architecture.md`](architecture.md); when and in what order it gets built is in [`strategy.md`](strategy.md). Outside facts are in [`research.md`](research.md).
- **Labels:** (D#) is the cloud decision log ([`../cloud-platform-planning/cloud-platform-decisions.md`](../cloud-platform-planning/cloud-platform-decisions.md)). *(inferred)* marks our own reasoning.

---

## 1. One line

**The Fleet Doctor is a strong model in the cloud that the robot's own healer can call on when it's stuck and the robot can reach Romulus.** First it gives advice. When that isn't enough, it sends a tested fix. Later it may investigate live through the healer's own tools. The device stays the only thing that ever changes the robot.

The name comes from Kent's original Romulus design, where a cloud "Fleet Doctor" (Claude plus typed tools) proposed fixes (`~/git2/docs/archive/ARCHITECTURE.md` §2, §6). D12 narrowed it out of v0. This proposal brings it back in a form that fits today's decisions.

## 2. Why it should exist

- **The healer handles the common case offline.** Ishaan's benchmark has qwen3.5:4b (thinking on) passing 6/6 planted faults on h3 (`docs/healer-benchmark-progress.md`; single runs, so noisy).
- **The long tail is where a small model breaks.** Cases a small model can't know or reason through *(inferred, from [`research.md`](research.md) §4 and the repo audit)*:
  - a library released two days ago (fault A2: the LeRobot 0.6 → 0.7 API);
  - C firmware bugs that need a rebuild and reflash (F1, F2b/c);
  - silent resets that need `addr2line` and linker-script reasoning (F3, F7);
  - wrong behavior with no error at all (tier 3; D1 is the borderline case);
  - the healer's own model server being down, so the healer has no brain (J1, J2, J4).
- **Frontier models are much better at the hard tier.** In RoboBugBench, frontier models fixed 66.7% of bugs that crash the process but only 22.2% of bugs that show up as wrong robot behavior ([`research.md`](research.md) §4). The tail is hard even for them, so the Doctor has to check its own work.
- **Nobody ships this.** Robotics tools stop at reading data. AI SRE tools act behind approvals and don't touch hardware. Nobody pairs an offline healer on the device with an optional cloud doctor and a learning loop ([`research.md`](research.md) §1).
- **It makes the company story compound.** Every case the Doctor solves becomes a skill or a known fix for the healer, so the robot needs the cloud less over time. **The escalation rate is the metric we pitch** *(inferred)*.

## 3. Principles

1. **Local-first.** The healer never waits on the Doctor (D8). With no connection, the Doctor does nothing at all, and healing works exactly as it does today. Formally `heal = local_heal >> doctor`, where `doctor ≡ Id` when Romulus is unreachable (Constitution: composable, identity law) *(inferred)*.
2. **One hand on the robot.** Only the device changes the device. A Doctor fix goes through the healer's own overlay process: snapshot, park with torque off, verify, a mark-good timer, and automatic revert (D5a, D7). The Doctor proposes and the device decides.
3. **Same tools, two minds.** The healer's tools (logs, audit, frame, stack dump, health checks, tests, overlay, park) become one service on the device that both the healer and the Doctor call. Foxglove does this for its sidebar and its MCP server ([`research.md`](research.md) §2).
4. **The smallest intervention that works.** Advice comes first, a drafted fix second, and a live session last. In SWE-Protégé, an expert that only gives guidance was 8.2× cheaper than an expert that does the whole job ([`research.md`](research.md) §4).
5. **Check before touching.** A proposal climbs the cloud ladder first: shape, compile, a reproduction test, mocks, replay, emulation if available. Only then is it offered to the device ([`research.md`](research.md) §5).
6. **Readable, never magical (D9).** Every Doctor step lands on the incident timeline as a hypothesis ledger: validated, invalidated or inconclusive, each with its evidence. That ledger is hero demo step 3, made real.
7. **Teach, then step back.** A Doctor fix that holds becomes a known fix (replayed with no LLM next time) and a skill for the healer. Each skill is re-tested on the small model before it ships ([`research.md`](research.md) §4).
8. **Never the safety baseline.** The Doctor doesn't propose changes to the safety layer. "The cloud writing safety-layer changes on its own initiative" is already a PRD non-goal (`cloud-platform-prd.md` §6). One baseline layer stays out of everyone's reach *(inferred; see [`architecture.md`](architecture.md) §6)*.

## 4. Three levels of help

Each level reuses the one before it. The device can stop at any level.

| Level | What happens | Who acts | Connection | When |
|---|---|---|---|---|
| **L1 · Advise** | The healer calls an `ask_doctor` tool with its question and context. The Doctor returns hypotheses, where to look and what to try. The healer carries on | Healer | The device makes an HTTP request; a timeout counts as no answer | Mid-heal, when a trigger fires ([`architecture.md`](architecture.md) §3) |
| **L2 · Consult** | The healer gives up or escalates. The Doctor diagnoses from the uploaded evidence, writes a fix plus a reproduction test, and checks it in the sandbox. The next check-in offers the proposal. The healer applies it as an overlay | Healer applies; the Doctor drafts | The existing check-in, plus a pull route for the proposal | After a heal fails, or when no healer model is available |
| **L3 · Session** | The device opens a channel to Romulus and offers its tools. The Doctor investigates live under a lease, while the healer yields. Reading tools are open; acting tools need permission | The device runs every call through its own policy | A WebSocket the device opens, relayed by Romulus | Post-hackathon |

## 5. User stories

| # | Story | Level |
|---|---|---|
| FD1 | As the healer, when I'm going in circles, I can ask a stronger model where to look, and keep going if nobody answers. | L1 |
| FD2 | As the healer, when my fix keeps reverting, I can hand the incident to the cloud and later receive a fix that's already been tested. | L2 |
| FD3 | As the fleet operator, I can read each Doctor case on the timeline: its hypotheses, the evidence, the proposed fix, its tests, and whether it held. | L1–L3 |
| FD4 | As the fleet operator, I know the Doctor can't touch the safety layer, can't loop on one incident, and stops when I pull the kill switch. | All |
| FD5 | As the company, every fix the Doctor gets right makes the healer better, and I can watch the escalation rate fall. | All |
| FD6 | As an engineer, I can open a live session on a sick arm and watch the Doctor investigate with the healer's tools, every call recorded. | L3 |

## 6. Features, curated

Priority labels match the cloud build plan's cut lines (P0 must, P1 should, P2 could). The order inside each level is the build order.

| Feature | Level | Priority | Notes |
|---|---|---|---|
| Reserved contract fields and a stub stage that marks itself as a stub | — | P1 (tonight, cheap) | Keeps the door open without a contract version bump ([`strategy.md`](strategy.md) §2) |
| `ask_doctor`: advice for a question plus context | L1 | P2 | One route and one healer tool |
| Hypothesis ledger on the timeline | L1+ | P2 | `doctor.*` events; the readable heart of the feature |
| Escalation triggers emitted by the healer | L1+ | P2 | Objective signals first ([`architecture.md`](architecture.md) §3) |
| Consult pipeline: gather → diagnose → draft → check → offer | L2 | Post | Reuses D21's coder and the M8 sandbox |
| Proposal delivery and result route | L2 | Post | New check-in field, pull route and result route |
| Known-fix library keyed by fault signature | Teach | Post | Repeats skip the LLM |
| Skills written from Doctor cases, re-tested on the small model | Teach | Post | Extends the batch `write_skill` (I6) |
| Escalation-rate metric in the digest | Teach | Post | The pitch number |
| Device tool service (healer tools as one service) | L3 | Post (could start earlier on the device) | Also the natural owner of "one serial-port owner (arm host)" in `features.txt` |
| Live session relay, leases, per-tool permissions, auth | L3 | Post | Auth comes first |
| Distilling Doctor traces into the healer's model (LoRA) | Teach | Later | Needs thousands of traces and GPU time |

**Cut on purpose:**
- the Doctor touching the safety layer;
- the Doctor pushing anything straight to a device;
- a raw remote shell;
- the Doctor promoting versions (D6 keeps that job);
- a second agent framework (the Doctor reuses D21/D22's adapters).

## 7. Non-goals

- **Replacing the healer.** The Doctor is an add-on. A robot that never connects must still heal.
- **Real-time control.** No LLM sits in the control or safety loop. The arm is parked while anyone heals it (`docs/project-overview.md` §8.1).
- **Human approval steps.** D11 holds: the kill switch is the only human control. Proposals that touch safety or go over budget are *blocked* and wait for a person, like over-cap work orders (D19b). They aren't approved.
- **Hardware diagnosis beyond a second opinion.** Faults that end in park and a work order stay with triage (D12). The Doctor can annotate them, but it never orders parts.

## 8. How we'd pitch it *(inferred)*

> "The healer on the robot fixes what it can, offline. When it's stuck and online, it calls the Fleet Doctor, a frontier model that sends back a fix it has already tested. The robot applies the fix under the same safety net as its own patches. If the fix holds, the whole fleet gets it overnight, and the healer learns it, so the next robot fixes this fault without calling home. Watch the escalation rate fall."

It answers "why not just run a big model in the cloud?" (offline, cost, latency, privacy). It also answers "why not just a small model?" (the long tail). And it sharpens the positioning against fixpi and NeSy-Edge, which need the cloud to heal at all ([`research.md`](research.md) §1).
