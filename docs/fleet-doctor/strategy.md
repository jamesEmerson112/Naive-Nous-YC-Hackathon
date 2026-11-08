# Fleet Doctor: development strategy

- **Status:** proposal v2, Sat Nov 7 ~6:20 PM PST, revised after a red-team review. Effort numbers are estimates *(inferred)*.
- **Decided Sat Nov 7 ~7:45 PM PST** (Kent; [decision log](../cloud-platform-planning/cloud-platform-decisions.md), round 3): D23–D26 accepted as proposed (§6); D27 deferred, with only the typed device-tool contract built now; D28 settles §7 question 3. **This supersedes the timing below** (the "nothing to build tonight" stance in §1, the "When" column in §2, and the P2 ordering line under this one): L1, L2 and Teach get built now, behind `doctor_mode=off` by default, with new tables only, and PRs merge to main as they're ready. Build order: [`implementation-plan.md`](implementation-plan.md); seams: [`interfaces.md`](interfaces.md).
- **Owns:** phases, what goes in this weekend, the demo, handoffs, risks, the decisions requested, the open questions and the research backlog. The design is in [`architecture.md`](architecture.md).
- **The cloud build plan wins any conflict.** Its rule is: "a lower item never takes time from a higher one" ([`../cloud-platform-planning/cloud-platform-build-plan.md`](../cloud-platform-planning/cloud-platform-build-plan.md) §1). The Fleet Doctor is a P2, below every P0 flow and every P1 item, including F11 (the coding agent) and M6 (datasets).
- **Labels:** "flow F#" is a flow in the cloud flows doc, "fault X" is an entry in `docs/fault-catalog.md`, and FR# is a risk in §5.

---

## 1. Stance

- **Thinnest working slice first** (BUILD §2 principles 1 and 8). Each phase leaves something demoable, and each stage starts as a stub that labels itself.
- **L1 is the cheapest real slice, and nothing blocks it.** It needs no sandbox, no device code in the repo and no gate, so none of M8's blockers B1–B8. It needs only the LLM adapter, which already answers through the Naïve proxy (`llm.py`). L2 has to wait for the M8 pieces anyway.
- ~~**Nothing to build tonight.**~~ *Superseded by Kent's call (status, top): build L1, L2 and Teach now, default-off.* Fields don't need reserving: unknown fields already pass both ways ([`architecture.md`](architecture.md) §7). A "not wired" stub would clutter the overnight timeline and collide with the open M3b and M5 branches. Tonight's only Doctor work is talk: one line at I0 and settling question 3 in §7.

## 2. Phases

| Phase | Scope | Done when | Effort | Depends on | When |
|---|---|---|---|---|---|
| **0 · Talk** | At I0, ask Ishaan the §4 questions. Settle the contract version rule (§7, question 3) | Answers written down | 5–10 min | I0 | Tonight |
| **1 · Advise (L1)** | `POST /doctor/ask` (§4 of the architecture doc), the ledger rows on the timeline. Device: the trigger hook for T2/T4/T6 plus the circuit breaker | The healer stalls on a real fault, gets hypotheses, and fixes it; the ledger shows on the timeline | Cloud ~1–1.5 h · device ~1 h | LLM adapter (works); the harness can make an HTTP call | **Only when every P0 flow is green and every P1 item is done or blocked.** Not before |
| **2 · Consult (L2)** | `consults` and `proposals` tables, the consult state machine, text-only evidence readers, the reproduction-test rule, proposal and result routes, the overlay row created on `applied`. Device: the proposal applier and mark-good timer | Flow F12 (below) passes with the fake device, then once on the real arm | Cloud ~1–2 days · device ~2–3 h | M8 blockers B1, B4, B5, B8 (for C); B6 for lock-in; B7, or it stops at `awaiting_canary`; the K17 path list (until then, every proposal is discarded) | After the hackathon, alongside M8 |
| **3 · Session (L3)** | Device tool service, device-opened socket relay, leases, per-tool permissions, auth, session recordings | An engineer watches the Doctor investigate a parked arm live, every call recorded | 1–2 days | Phase 2; D27 decided (where the agent loop runs) | After the hackathon |
| **4 · Teach** | Held Doctor overlays flow through lock-in and `write_skill`; one digest metric; traces exported for later training | The digest shows asks, consults, holds and escalations per fault family | Small (reuses the batch) | Phase 2 data | After the hackathon |

**Flow F12, a new flow for the flows doc** *(proposal)*:
1. The fake device reports `resolved: false` with `consult.wanted`.
2. The consult runs admit → gather → diagnose → draft → check.
3. The next check-in carries the `proposal`.
4. The device pulls it, applies it as an overlay with its `proposal_id`, and reports `applied`, then `held`.
5. A candidate is built from the patch and goes through the gate (D6): to the fleet, or to `awaiting_canary` until B7.
6. The story shows the ledger, the checks and the outcome.

**Checks along the way:**
- a base that doesn't match gives `stale`;
- a patch touching safety gives `discarded`;
- a third consult on one incident gives `gave_up`;
- the daily dollar cap gives `blocked_over_cap` at admit;
- `frozen` stops the offer.

## 3. What we'd show (hero demo step 3)

Today step 3 ("Fleet Doctor writes a root cause") maps to the device's own reasoning plus a templated batch explanation. It has no cloud root cause for unresolved incidents ([`research.md`](research.md) §6).

If Phase 1 ships, the candidates are, best first *(inferred)*:
1. **Fault A2 in the background catalog: a script written for LeRobot 0.6.** v0.7 shipped Nov 5, so a local 4B model may not know the new API.
   - *Untested:* nobody has run it on qwen3.5:4b, and the healer has bash, so it could read the installed LeRobot source and solve it alone.
   - Run it in the benchmark first.
2. **Fault D1 with the thinking-off model config.** That config really does loop on D1 (`docs/healer-benchmark-progress.md`). It's genuine, but it depends on choosing the weaker config, so say so on stage.
3. **Faults F1 or F2b (C control-law bugs).** These need L2 and the STM32 toolchain in the sandbox (B8), so they're out of reach this weekend.

**Be honest on stage:**
- Don't fake the escalation. Name the model config that's running.
- Say the escalation-rate metric is design, not data, by Sunday.
- If Phase 1 doesn't ship, present the Doctor as the roadmap, using [`concept.md`](concept.md) §8.

**Fix while we're here:** demo step 1 is inconsistent. Covering the camera is fault C5, whose correct response is to park and leave the code alone (`fault-catalog.md`), but the cloud demo has the agent raise a threshold (v0 plan §8). Reconcile it whether or not the Doctor ships.

## 4. Handoffs: what we need from whom

| Who | Ask | By | Fallback |
|---|---|---|---|
| Ishaan (at I0) | (a) Can the harness detect T1–T7 and call one HTTP route, injecting ≤ 300 tokens? (b) Later: can the healer apply an outside patch through its overlay process, with provenance and a mark-good timer? (c) The h3 tool list, which defines the device tool service. (d) The safety and visor path list (K17). (e) The folder name: his benchmark doc says the test bed moves into the repo as `device/` | I0 | L1 shown with a scripted call from the fake device |
| Ishaan and Olir | A baseline nothing can patch: servo EEPROM torque and angle limits, the STM32 watchdog, a hardware e-stop ([`architecture.md`](architecture.md) §6, item 5) | Before any Doctor fix touches the real arm | Keep the Doctor off the real arm |
| Olir | Does the firmware talk to the Pi over USART2 (the ST-LINK virtual COM port, which Renode can emulate) or the chip's own USB (which it can't)? Plus the firmware build script (B8) | Phase 2 | Host-compiled unit tests only |
| James | The RunPod VLM endpoint for camera frames (D22) | Phase 2 | Text-only evidence |

## 5. Risks

| # | Risk | Mitigation |
|---|---|---|
| FR1 | **It eats P0 or P1 time** (K12) | The phase gates in §2; nothing to build tonight |
| FR2 | **A cloud fix reaches the real arm without a canary** (K6) | The honest statement plus overlay, mark-good timer, cloud checks, base check and expiry ([`architecture.md`](architecture.md) §6, items 2–3); proposals blocked until K17 |
| FR3 | **Prompt injection through logs or frames** | Evidence treated as data; schema-bound output; a sandbox that reaches only the LLM (§6, item 6) |
| FR4 | **Revert → consult loops and runaway spend** (K3, K13, K16) | 6 asks and 2 consults per incident, then `gave_up`; a daily dollar cap at admit; overnight runs use a fake or capped Doctor |
| FR5 | **Latency or venue Wi-Fi** (K5) | The circuit breaker (one timeout per heal at most); fake mode by default |
| FR6 | **The healer never asks, or always asks** | Trigger code, not model judgment; the cloud starts consults itself; caps |
| FR7 | **Patches that game the check** | Hidden `verify` scripts run once, pass/fail only; reproduction tests that check behavior |
| FR8 | **Camera frames sent to an outside LLM** (K15) | Text-only evidence first; frames low-res and opted in, or a VLM we host |
| FR9 | **The pitch drifts into "needs the cloud"** | Local-first framing; the healer works unchanged with no answers |
| FR10 | **Load on Ishaan** (K2) | L1 needs one hook; the applier waits for Phase 2 |
| FR11 | **The worker or API gets blocked** | The consult as a polled state machine; L1 never holds a database session during the LLM call ([`architecture.md`](architecture.md) §4, §5.1) |

## 6. Decisions requested

To be logged in [`../cloud-platform-planning/cloud-platform-decisions.md`](../cloud-platform-planning/cloud-platform-decisions.md) if Kent accepts. Decisions go in the log first (planning README rules).

**Outcome (Sat Nov 7 ~7:45 PM PST):** D23–D26 accepted as proposed; D27 deferred (only the typed device-tool contract gets built now). Logged in the decision log, round 3, with D28.

| # | Question | Proposed decision |
|---|---|---|
| **D23** | Is the Fleet Doctor in scope? | Yes, as a P2 add-on to the healer: L1 advise and L2 consult, with L3 later. L1 and L2 call the LLM and `Coder` adapters D21 and D22 already put behind Romulus, so they add no new agent system and need no change to D12. The D22 order holds (`local_agent` first) |
| **D24** | Who changes the device? | Only the device. The Doctor's output is a code patch the device applies as an overlay carrying `proposal_id`, after hash and base checks, before it expires, and under a mark-good timer. The fleet changes only through D6. A held proposal becomes a candidate without a second `Coder` pass |
| **D25** | What limits the Doctor? | It stays out of safety: patches touching safety are discarded, advice naming safety paths is removed, and everything is blocked until the K17 list arrives. Caps are 6 asks and 2 consults per incident (then `gave_up`) plus a daily dollar cap (`blocked_over_cap`). `frozen` stops everything. Evidence counts as untrusted data |
| **D26** | Who decides to escalate? | The device's harness code, using triggers T1–T7 ([`architecture.md`](architecture.md) §3), with a circuit breaker. The cloud also starts consults on unresolved incidents that aren't hardware, within the caps |
| **D27** | Live sessions, and where their agent loop runs? | Deferred until after the hackathon. Before building, choose between an agent loop in Romulus (which needs D12 and PRD principle 8 amended) and a Naïve agent with inbound access (which needs D19 and the "Won't" line amended) ([`architecture.md`](architecture.md) §8). Either way: a socket the device opens, typed tools only, leases, auth first |

## 7. Open questions

1. Should the cloud start a consult on *every* unresolved software incident, or only when the healer asks? (D26 proposes both, within the caps.)
2. Which fault carries hero step 3 (§3), and how do we fix the step 1 inconsistency?
3. ~~**The contract version rule.** The APIs header and contracts README say optional additions before I0 don't bump the version. `cloud-platform-architecture.md` §9 rule 6 and the `version.py` docstring say every change does. Which wins?~~ **Settled by D28:** optional, additive changes don't bump `CONTRACT_VERSION`; only breaking changes do.
4. **Device code folder.** `device/` (Ishaan's benchmark doc) or `edge/` (his local tree today)? `CODE_ROOT` follows whatever he picks.
5. Frames: which go to an outside LLM, and at what resolution?
6. Should the mark-good timer (N = 60 s proposed) also apply to the healer's own overlays?

## 8. Research backlog (next, not done tonight)

- Read SWE-Protégé closely for the advice format and trigger design (L1).
- Test whether a skill written by the Doctor helps qwen3.5:4b or hurts it, using Ishaan's benchmark (SkillX-style).
- Run fault A2 (LeRobot 0.6 script) on qwen3.5:4b to see whether it's a real escalation case.
- Does the Python MCP SDK support spec 2026-07-28? Read `openai/tunnel-client` as the reference relay (L3).
- Build a small Renode setup for the Nucleo-F042K6, if the firmware uses USART2.
- Behavior checks for tier-3 faults in the style of RoboBugBench, so neither healer nor Doctor can pass by hiding the symptom.
