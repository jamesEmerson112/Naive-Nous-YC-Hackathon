# Fleet Doctor: development strategy

- **Status:** proposal, Sat Nov 7 ~5:45 PM PST. Effort numbers are estimates *(inferred)*.
- **Owns:** phases, what goes in this weekend, handoffs, risks, the decisions requested and the open questions. The design is in [`architecture.md`](architecture.md).
- **The cloud build plan wins any conflict.** Its rule is: "a lower item never takes time from a higher one" ([`../cloud-platform-planning/cloud-platform-build-plan.md`](../cloud-platform-planning/cloud-platform-build-plan.md) §1). The Fleet Doctor sits below F11 (the first P1 item), so apart from the Phase 0 hooks it's a P2.

---

## 1. Stance

- **Build it as additions that work end to end, thinnest first** (BUILD §2 principles 1 and 8). Each phase leaves something working and demoable, and each stage starts as a labeled stub.
- **L1 is the cheapest real slice, and it's unblocked.** L1 (advise) needs no sandbox, no device code in the repo and no gate. That means none of M8's blockers B1–B8. It needs only the LLM adapter, and the Naïve proxy already answers (`llm.py`). L2 has to wait for the M8 pieces anyway.

## 2. Phases

| Phase | Scope | Done when | Effort | Depends on | When |
|---|---|---|---|---|---|
| **0 · Hooks** | Reserve the optional contract fields (`IncidentReport.consult`, `CheckInResponse.proposal`, `Overlay.proposal_id`) and the `doctor.*` event kinds. Add the `consult_v0 = decline(...)` stage, which labels itself | Fields are in the contracts and the stub's event shows on the timeline | Cloud ~30–45 min | **Must land before I0**, so it needs no contract version bump | Tonight, in a gap between milestones, or folded into I0 |
| **1 · Advise (L1)** | `POST /doctor/ask`, `DoctorAsk`/`DoctorAdvice`, a bigger LLM budget, timeline rows (hypothesis ledger). Device: an `ask_doctor` tool plus triggers T2/T4/T6 | The healer stalls on a real fault, asks, gets hypotheses, and fixes it; the timeline shows the ledger | Cloud ~1–1.5 h · device ~30–60 min | LLM adapter (works); Ishaan's harness can call HTTP | Sunday 7:30 AM, **only if** F1–F5 and F10 are green at that checkpoint |
| **2 · Consult (L2)** | `consults` and `proposals` tables, the §5 pipeline, proposal and result routes, evidence readers, the reproduction-test rule. Device: the proposal applier and mark-good timer | F12 (below) passes with the fake device, then once on the real arm | Cloud ~4–6 h · device ~1–2 h | M8 blockers B1, B4, B5, B8; the device test suite | With M8, or after the hackathon |
| **3 · Session (L3)** | Device tool service, device-opened socket relay, leases, per-tool permissions, auth, session recordings | An engineer watches the Doctor investigate a parked arm live, with every call recorded | 1–2 days | Phase 2; the auth design | After the hackathon |
| **4 · Teach** | Known-fix library, skills re-tested on the healer's model, trace dataset, escalation rate in the digest; LoRA later | The escalation rate is visible and a repeat fault skips the LLM | Ongoing | Phase 2 data; Ishaan's benchmark | After the hackathon |

**F12, a new flow for the flows doc** *(proposal)*:
1. The fake device reports `resolved: false` with `consult.wanted`.
2. The consult runs: admit → gather → diagnose → draft → check.
3. The next check-in carries the `proposal`.
4. The device pulls it, applies it as an overlay with its `proposal_id`, and reports `applied`, then `held`.
5. The overlay is locked in through D6.
6. The story shows the ledger, the checks and "→ fleet".

**Checks along the way:**
- a stale base gives `stale`;
- a proposal touching safety gives `blocked_safety`;
- a third consult on the same incident gives `blocked_over_cap`;
- `frozen` stops the offer.

## 3. What we'd show (hero demo step 3)

Today step 3 ("Fleet Doctor writes a root cause") maps to the device's own reasoning plus a templated batch explanation. It has no cloud root cause for unresolved incidents (repo audit; [`research.md`](research.md) §6). Options, best first *(inferred)*:

1. **J1/J2: the healer's model server dies** (`docs/fault-catalog.md`). The healer has no brain, escalates on T1, and the Doctor diagnoses from the evidence. It's the clearest picture of "augment", and it needs L2, or L1 with the advice printed to the timeline.
2. **A2: a LeRobot 0.6 → 0.7 API break.** The release is two days old, and a local 4B model can't know the new API. L1 or L2.
3. **F1 or F2b: C control-law bugs** that need a rebuild and a reflash. L2, plus the STM32 toolchain in the sandbox (B8).
4. **D1 with the thinking-off model config.** That config really does loop on D1 (`docs/healer-benchmark-progress.md`). It's genuine, but it depends on a config choice, so say so on stage.

**Don't fake the escalation.** Pick a fault the healer truly fails at and say which model config is running.

**Fix while we're here:** demo step 1 is inconsistent across docs. Covering the camera is fault C5, whose correct response is to park and leave the code alone (`fault-catalog.md`), but the cloud demo has the agent raise a threshold (v0 plan §8). That needs reconciling whether or not the Doctor ships.

## 4. Handoffs: what we need from whom

| Who | Ask | By | Fallback |
|---|---|---|---|
| Ishaan (at I0) | (a) Can the healer emit triggers T1–T9 and add an `ask_doctor` tool? (b) Can it apply an outside patch through its overlay process, with provenance? (c) The h3 tool list (defines the device tool service). (d) The safety and visor path list (K17). (e) A ruling on the folder name: his benchmark doc says the test bed moves into the repo as `device/` | I0 | L1 shown with a scripted healer call from the fake device |
| Ishaan and Olir | A baseline nothing can patch: servo EEPROM torque and angle limits, the STM32 watchdog, a hardware e-stop ([`architecture.md`](architecture.md) §6.4) | Before any real-arm Doctor demo | Keep the Doctor off the real arm |
| Olir | Does the firmware talk to the Pi over USART2 (the ST-LINK virtual COM port, which Renode can emulate) or the chip's own USB (which it can't)? Plus the firmware build script (B8) | Sunday morning | Host-compiled unit tests only |
| James | The RunPod VLM endpoint for camera frames (D22) | Phase 2 | Text-only evidence |
| Naïve staff | Can a `naive_session` take a case file and return a diff in a few minutes, and at what cost? | Phase 2 | `local_agent` in the `coder` container |

## 5. Risks

| # | Risk | Mitigation |
|---|---|---|
| FD1 | **It eats P0 time** (K12) | Phase gating in §2; only the Phase 0 hooks land before the 7:30 AM checkpoint |
| FD2 | **A cloud fix reaches the real arm without a canary** (K6) | [`architecture.md`](architecture.md) §6.2: the cloud ladder, a reproduction test, base check and expiry, one device only, the mark-good timer |
| FD3 | **Prompt injection through logs or frames** | Evidence treated as data; schema-bound output; the sandbox reaches only the LLM ([`architecture.md`](architecture.md) §6.5) |
| FD4 | **Revert → consult loops and runaway spend** (K3, K13, K16) | 6 asks and 2 consults per incident; a dollar cap per day; `blocked_over_cap`; overnight runs use a fake or capped Doctor |
| FD5 | **Latency or venue Wi-Fi** (K5) | The healer never waits (timeouts); fake mode by default; cached advice for the demo |
| FD6 | **The healer never asks, or always asks** | Objective triggers; the cloud starts consults on unresolved incidents itself; caps |
| FD7 | **Patches that game the check** (RoboBugBench) | Hidden `verify` scripts; the Doctor writes reproduction tests that check behavior |
| FD8 | **Camera frames sent to an outside LLM** (K15) | Low-res frames, opt-in, or a VLM we host |
| FD9 | **The pitch drifts into "needs the cloud"** | Local-first framing; `doctor ≡ Id` offline; the escalation-rate metric |
| FD10 | **Load on Ishaan** (K2) | L1 needs one tool and two triggers; the applier waits for Phase 2 |

## 6. Decisions requested

To be logged in [`../cloud-platform-planning/cloud-platform-decisions.md`](../cloud-platform-planning/cloud-platform-decisions.md) if Kent accepts. Decisions go in the log first (planning README rules).

| # | Question | Proposed decision |
|---|---|---|
| **D23** | Is the Fleet Doctor in scope, and how does it square with D12 ("doesn't orchestrate agents")? | Yes, as a cloud add-on to the healer: L1 advise and L2 consult, with L3 later. It adds no new agent framework: advice uses the LLM adapter (D22), and drafting uses the Coder adapter (D21), preferably `naive_session`, which keeps the agent in Naïve. The D12 note gets amended |
| **D24** | Who changes the device? | Only the device. The Doctor's output is a proposal the device applies as an overlay carrying `proposal_id` (hash-checked, base-checked, expiring, with a mark-good timer). The fleet changes only through D6 |
| **D25** | What limits the Doctor? | It never offers a proposal with `touches_safety` (`blocked_safety`, which waits for a person). Caps are 6 asks and 2 consults per incident plus a dollar cap per day (`blocked_over_cap`). `frozen` stops everything. Evidence counts as untrusted data |
| **D26** | Who decides to escalate? | The device, using objective triggers T1–T9 ([`architecture.md`](architecture.md) §3). The cloud also starts consults on unresolved non-hardware incidents itself, within the caps |
| **D27** | Live sessions? | Deferred. When built: a socket the device opens, typed tools only (no raw shell), leases, and auth first |

## 7. Open questions

1. Should the cloud start a consult on *every* unresolved software incident, or only when the healer asks? (D26 proposes both, within caps.)
2. Which fault carries hero step 3 (§3), and how do we fix the step 1 inconsistency?
3. The contract version rule conflicts: the APIs draft policy says additions before I0 don't bump it, while architecture §9 rule 6 says any change does. Which one wins?
4. Device code folder: `device/` (Ishaan's benchmark doc) or `edge/` (Ishaan's local tree today)? `CODE_ROOT` follows whatever he picks.
5. Can `naive_session` meet a latency of a few minutes per consult? If not, use `local_agent` in the `coder` container.
6. Frames: which go to an outside LLM, and at what resolution?

## 8. Research backlog (next, not done tonight)

- Does the Python MCP SDK support spec 2026-07-28? Read `openai/tunnel-client` as the reference relay (L3).
- Read SWE-Protégé closely for the `ask_expert` prompt and trigger design (L1).
- Try SkillX-style skill refinement against Ishaan's benchmark: does a Doctor-written skill help qwen3.5:4b, or hurt it?
- Build a small Renode setup for the Nucleo-F042K6 board, if the firmware uses USART2.
- Design the fault signature for known fixes: what makes two incidents "the same"?
- Behavior checks in the style of RoboBugBench for tier-3 faults, so neither healer nor Doctor can pass by hiding the symptom.
