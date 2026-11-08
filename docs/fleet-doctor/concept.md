# Fleet Doctor: concept

- **Status:** proposal v2, Sat Nov 7 ~6:20 PM PST, revised after a red-team review. Nothing here is decided; the decisions requested are D23–D27 in [`strategy.md`](strategy.md) §6.
- **Owns:** what the Fleet Doctor is, why it exists, its principles and features. How it works is in [`architecture.md`](architecture.md); when it gets built is in [`strategy.md`](strategy.md); outside facts are in [`research.md`](research.md).
- **Labels:**
  - (D#) is the cloud decision log ([`../cloud-platform-planning/cloud-platform-decisions.md`](../cloud-platform-planning/cloud-platform-decisions.md)).
  - "Fault X" is an entry in `docs/fault-catalog.md`; "flow F#" is a flow in the cloud flows doc.
  - *(inferred)* marks our own reasoning.

---

## 1. One line

**The Fleet Doctor is a strong model in the cloud that the robot's healer can call on when it's stuck and the robot can reach Romulus.** First it gives advice. When that isn't enough, it sends a fix it has already checked. Later it may investigate live through the healer's own tools. The device stays the only thing that ever changes the robot.

The name comes from Kent's original Romulus design, where a cloud "Fleet Doctor" (Claude plus typed tools) proposed fixes (`~/git2/docs/archive/ARCHITECTURE.md` §2, §6). D12 narrowed it out of v0. This proposal brings it back in a form that fits today's decisions.

## 2. Why it should exist

- **The healer handles the common case offline.** Ishaan's benchmark has qwen3.5:4b (thinking on) passing 6/6 planted faults on h3 (`docs/healer-benchmark-progress.md`; single runs, so noisy).
- **The long tail is where a small model breaks** *(inferred)*:
  - a library released two days ago (fault A2 in the background catalog: a script written for LeRobot 0.6);
  - C firmware bugs that need a rebuild and reflash (faults F1, F2b/c);
  - silent resets that need linker-script reasoning (faults F3, F7);
  - wrong behavior with no error at all (tier 3; fault D1 is the borderline case, which the thinking-off config fails by looping).
- **Tier 3 is where "fixed" is easiest to fake.** Patches that only hid a robot bug fooled a crash-only grader, even from frontier models ([`research.md`](research.md) §4). The Doctor has to check its own work with behavior tests, and so does the healer.
- **Nobody ships this.** Robotics tools stop at reading data. AI SRE tools act behind approvals and don't touch hardware. Nobody pairs an offline healer on the device with an optional cloud doctor ([`research.md`](research.md) §1).
- **It makes the story compound.** A Doctor fix that holds is locked in for the fleet and turned into a skill for the healer, through the batch job that already exists (D0, I6). So the robot needs the cloud less over time. **The escalation rate is the metric to pitch**, though it can't exist with real data by Sunday *(inferred)*.

## 3. Principles

1. **Local-first.** With no answers and no proposals, healing is exactly today's healing (D8).
   - The healer never blocks on the Doctor. After the first timeout in a heal, it stops asking for the rest of that heal (a circuit breaker).
   - Formally, the Doctor is an optional input: `heal(state, advice=None)` is today's `heal(state)` *(inferred)*.
2. **One hand on the robot.** Only the device changes the device.
   - A Doctor fix is applied through the healer's own overlay process: snapshot, park with torque off, verify, revert itself on failure (D5a).
   - One new safeguard is added on top: a mark-good timer ([`architecture.md`](architecture.md) §6, item 2).
   - The Doctor proposes and the device decides.
3. **Same tools, two minds.** The healer's tools (logs, audit, frame, stack dump, health checks, tests, overlay, park) can become one service on the device that both the healer and, later, the Doctor call. Foxglove does this for its sidebar and its MCP server ([`research.md`](research.md) §2).
4. **The smallest help that works.** Advice comes first, a checked fix second, and a live session last. Guidance from an expert is far cheaper than an expert doing the whole job ([`research.md`](research.md) §4, SWE-Protégé).
5. **Check before touching.** A proposal climbs the cloud ladder (shape, compile, a reproduction test, mocks, replay) before the device sees it ([`research.md`](research.md) §5).
6. **Readable, never magical (D9).** Every Doctor step lands on the incident timeline as a hypothesis ledger: claims, evidence, checks, outcome. That's hero demo step 3, made real.
7. **Teach through what exists.** A Doctor fix that holds goes through the same lock-in (D0, D6) and skill writing (I6) as a healer fix. No separate learning system.
8. **Out of safety entirely.** The Doctor doesn't comment on safety-layer changes or propose them; "the cloud writing safety-layer changes on its own initiative" is a PRD non-goal (`cloud-platform-prd.md` §6). The healer's own D5a powers are unchanged.

## 4. Three levels of help

Each level builds on the one before. The device can stop at any level.

| Level | What happens | Who acts | Connection | When |
|---|---|---|---|---|
| **L1 · Advise** | When a trigger fires, the healer's *harness code* (not the model) sends the case to the Doctor. It injects the reply (≤ 300 tokens of hypotheses and checks) into the healer's conversation, and the healer carries on | Healer | The device makes an HTTP request; a timeout means no answer | Mid-heal ([`architecture.md`](architecture.md) §3) |
| **L2 · Consult** | The heal failed. The Doctor diagnoses from the uploaded evidence, writes a reproduction test and a fix, and checks them in the sandbox. The next check-in offers the proposal, and the device applies it as an overlay | The device applies; the Doctor drafts | The existing check-in, plus a pull route for the proposal | After a failed heal |
| **L3 · Session** | The device opens a channel and offers its typed tools. The Doctor investigates live under a lease while the healer yields | The device runs every call through its own policy | A socket the device opens | After the hackathon; needs a decision first ([`architecture.md`](architecture.md) §8) |

**Why harness code, not a tool the model picks** *(inferred)*: the healer is mini-swe-agent, a 3–4B model with 16k of context whose only tool is bash. In SWE-Protégé, the model needed RL training before it learned *when* to call the expert ([`research.md`](research.md) §4). Simple trigger code is reliable today.

## 5. User stories

| # | Story | Level |
|---|---|---|
| S1 | As the healer, when I'm going in circles, I get hypotheses from a stronger model, and I keep going if nobody answers. | L1 |
| S2 | As the healer, when my fix keeps reverting, I can hand the incident to the cloud and later receive a fix that's already been checked. | L2 |
| S3 | As the fleet operator, I can read each Doctor case on the timeline: its hypotheses, evidence, proposed fix, checks, and whether it held. | L1–L3 |
| S4 | As the fleet operator, I know the Doctor stays out of the safety layer, can't loop on one incident, and stops when I pull the kill switch. | All |
| S5 | As the company, every fix the Doctor gets right reaches the fleet and the healer's skills, and I can see the escalation rate. | All |
| S6 | As an engineer, I can open a live session on a parked arm and watch the Doctor investigate with typed tools, every call recorded. | L3 |

## 6. Features, curated

Priority labels match the cloud build plan's cut lines (P1 should, P2 could). The order inside each level is the build order.

| Feature | Level | Priority | Notes |
|---|---|---|---|
| Doctor route plus the healer's trigger hook (T1–T7) | L1 | P2 | One route, plus harness code on the device |
| Hypothesis ledger on the timeline | L1+ | P2 | `doctor.*` events; the readable heart of the feature |
| Consult as a polled state machine: admit → gather → diagnose → draft → check → offer | L2 | After the hackathon | Reuses D21's `Coder` adapter and the M8 sandbox |
| Proposal delivery, result route, overlay provenance | L2 | After the hackathon | A new check-in field and two routes |
| Escalation and Doctor hit rate in the digest | Teach | After the hackathon | One metric; lock-in and skills reuse the batch |
| Device tool service (the healer's tools as one service) | L3 | Device side can start any time | Natural owner of "one serial-port owner (arm host)" in `features.txt` |
| Live session relay, leases, per-tool permissions, auth | L3 | After the hackathon, after D27 | Auth first |
| Training the healer's model on Doctor traces (LoRA) | Teach | Later | Needs thousands of traces and GPU time |

**Cut on purpose:**
- any Doctor role in safety-layer changes;
- the Doctor pushing anything straight to a device;
- a raw remote shell;
- the Doctor promoting versions (D6 keeps that job);
- a second agent framework (L1 and L2 call the D21/D22 adapters);
- reserving contract fields early (unknown fields already pass both ways: [`architecture.md`](architecture.md) §7);
- a separate known-fix library (lock-in already gives the fleet the fix).

## 7. Non-goals

- **Replacing the healer.** A robot that never connects must still heal.
- **Real-time control.** No LLM sits in the control or safety loop. The arm is parked while anyone heals it (`docs/project-overview.md` §8.1).
- **Human approval steps.** D11 holds: the kill switch is the only human control. A Doctor proposal that touches safety is discarded, not held for sign-off.
- **Hardware decisions.** Faults that end in park and a work order stay with triage (D12). The Doctor never orders parts.

## 8. How we'd pitch it *(inferred)*

> "The healer on the robot fixes what it can, offline. When it's stuck and online, it gets a second opinion from the Fleet Doctor, a frontier model, and if that isn't enough, a fix the Doctor has already checked. The robot applies that fix under the same safety net as its own patches. If it holds, the whole fleet gets it, and the healer learns it."

It answers both "why not just a big cloud model?" (offline, cost, latency, privacy) and "why not just a small model?" (the long tail). It also sharpens the positioning against fixpi and NeSy-Edge, which need the cloud to heal at all ([`research.md`](research.md) §1). On stage, say plainly which parts are live and which are design.
