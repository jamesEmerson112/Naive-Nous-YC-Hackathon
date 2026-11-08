# Fleet Doctor: architecture

- **Status:** proposal v2, Sat Nov 7 ~6:20 PM PST, revised after a red-team review. Everything here is *(inferred)* design unless a line cites a decision (D#) or a file.
- **Owns:** how the Fleet Doctor works: parts, triggers, flows, the proposal lifecycle, safety, contracts, fit with decisions, the learning loop. For why it exists, see [`concept.md`](concept.md); for build order, [`strategy.md`](strategy.md); for outside facts, [`research.md`](research.md).
- **References:**
  - Files were checked against `origin/main` on Sat ~6 PM.
  - "Fault X" is an entry in `docs/fault-catalog.md`; "flow F#" is a flow in the cloud flows doc.
  - The device code folder is `CODE_ROOT` in `romulus_contracts.codepatch`. Whether that's `edge/` or `device/` is still being settled with Ishaan.

---

## 1. Where it sits

```
 DEVICE (Ishaan)                                            ROMULUS (Kent)
 ┌─────────────────────────────────────────┐              ┌──────────────────────────────────────────────┐
 │ healer (mini-swe-agent + small model)    │              │ device API: check-in, incidents, recordings  │
 │ harness trigger hook (§3) ──── ask ──────┼── L1 POST ──▶│ /doctor/ask ──▶ LLM adapter (D22)            │
 │   ◀──── ≤300 tokens of hypotheses ───────┼──────────────┤                                              │
 │ proposal applier → overlay (D5a)         │ L2 check-in  │ consult state machine (worker step, §5):     │
 │   + mark-good timer (new)                │◀─ proposal ──┤   admit → gather → diagnose → draft →        │
 │ device tool service (later; L3)          │              │   check → offer      (Coder + sandbox, D21)  │
 │                                          ├═ L3 socket ═▶│ session relay (after D27)                    │
 └─────────────────────────────────────────┘ device dials  │ timeline: doctor.* events (D9)               │
                                              out, always   └──────────────────────────────────────────────┘
                                                             held → lock-in → gate (D0, D6), one change §5.4
```

The Doctor adds a new trigger and a new delivery path to pieces the plans already have:
- the evidence upload (flow F7);
- the LLM and Coder adapters (D21, D22);
- overlays (D5a);
- the gate (D6);
- the timeline (D9).

Its output has the same type as the healer's output: a code patch applied as an overlay.

## 2. Parts

| Side | Part | New or reused | Owner |
|---|---|---|---|
| Device | Trigger hook in the healer's harness: detects T1–T7 (§3), calls the Doctor, injects the advice | New, small (harness code, not a model tool) | Ishaan |
| Device | Proposal applier: pull, hash check, base check, apply as an overlay, mark-good timer, report the result | New, built on the overlay machinery | Ishaan |
| Device | Device tool service: the healer's `tools/` as one local service | New; needed for L3, useful before that | Ishaan |
| Cloud | `POST /v1/devices/{id}/doctor/ask` → LLM adapter | New route; reuses D22 | Kent |
| Cloud | Consult state machine: a `consults` table plus one worker step | New; reuses `Coder` (D21), the sandbox (M8), blobs and events | Kent |
| Cloud | `proposals` table and two routes | New | Kent |
| Cloud | Evidence readers. v0 is text only: incident fields, log tail, the JSONL agent trace, MCAP log text through the `mcap` library. Frames from LeRobot tars come later, because `lerobot` pulls in torch | New. Today the cloud only stores these files (`cloud-platform-architecture.md` §7) | Kent |
| Cloud | Timeline chapter (the hypothesis ledger), built from `doctor.*` events | New view | Kent |

## 3. When the healer asks

The device owns the triggers, because only the device sees its own loop. Every trigger is a signal the harness can detect without asking the model what it thinks. Small models' stated confidence is unreliable ([`research.md`](research.md) §4).

| # | Trigger | Default (tunable) | Asks for |
|---|---|---|---|
| T1 | No local model at all (Ollama down, out of memory) | Immediately | L2 |
| T2 | Verify still fails after K patch attempts, or the build or flash fails | K = 2 | L1, then L2 |
| T3 | The healer's overlay reverted itself (the fault came back, or health failed) | Once | L2 |
| T4 | Looping: the same command 3 times, or 5 steps with no new file read or changed | — | L1 |
| T5 | 70% of the step or time budget used with no passing check | — | L1 |
| T6 | Tier 3 (wrong behavior, no error), where hiding the symptom is the likely failure | At the start of the heal | L1 |
| T7 | No skill matches the fault | At the start of the heal | L1 |

- **Left out on purpose:** the model's own confidence (unreliable), and anything about safety-layer patches. The Doctor stays out of those entirely (§6, item 4).
- **Circuit breaker:** after the first L1 timeout or error in a heal, the hook skips the remaining asks in that heal. With dead Wi-Fi, the cost is one timeout per heal, not six.
- **Cloud-started consults:** an incident reported as `resolved: false` whose `suspected_cause` isn't `hardware` qualifies for L2 even without a request, as long as the caps allow it (§6, item 8) and the fleet isn't frozen. This covers healers that never ask.

## 4. L1 · Advise

The device dials out, which fits the rule that "devices always pull, and the platform never calls a device" (`cloud-platform-architecture.md` §1; v0 plan §7.2).

```
POST /v1/devices/{device_id}/doctor/ask
  { incident_id?, trigger: "T4", question,
    context: { tier, code_rev, signals, log_tail,
               recent_steps: [last 5 healer messages],
               files: [{path, excerpt}] },
    budget_s: 20 }
→ 200 { ask_id, hypotheses: [{claim, how_to_check}] }      # ≤ 300 tokens in total
→ timeout / 4xx / 5xx  ⇒  no answer; the circuit breaker trips (§3)
```

- **The answer is hypotheses plus how to check each one,** not a patch. The healer stays in charge, and the output stays small (Constitution: minimal output).
- **The cloud's LLM timeout is shorter than `budget_s`.** The handler makes the LLM call *without* holding a database session, then opens a short session to write `doctor.asked` and `doctor.advised`. A FastAPI handler that holds `SessionDep` would pin a connection for the whole call.
- **Safety filter:** a hypothesis that names a safety or visor path is removed and flagged on the timeline (§6, item 4).
- **Cap:** at most 6 asks per incident (SWE-Protégé's cap; [`research.md`](research.md) §4).

## 5. L2 · Consult

### 5.1 The consult as a polled state machine

One row per consult. The worker step `doctor.tick` moves each open consult forward by at most one stage per tick. Slow work (LLM calls, Coder runs) runs as a background job, and its result is collected on a later tick. This is the same pattern `workorders.sync` uses to poll Naïve cards, so a long consult never stalls `gate.tick`.

| Stage | What it does | v0 stub (labels itself, BUILD principle 8) |
|---|---|---|
| `admit` | Consult or not? Checks the request or the cloud rule (§3), `frozen`, the per-incident cap, the daily dollar cap (`blocked_over_cap`) and that `running.code_rev` is present (if not: decline, never 422) | `decline("fleet doctor not wired")` |
| `gather` | Builds the case file: incident, signals, log tail, the healer's trace (what it already tried), MCAP log text, code at the base `code_rev` from the registry, and matching skills. All marked as untrusted data | Incident and log tail only |
| `diagnose` | One LLM call over the case file (budget about 120 s, 4k tokens) → a hypothesis ledger | Prose only |
| `draft` | `Coder`: write a reproduction test from the recorded trace that fails on the base, then the fix → `{code_patch, repro_test, why}` | None; the consult ends with the ledger as advice |
| `check` | Cloud ladder rungs 0–3 in the sandbox ([`research.md`](research.md) §5). The repro test must fail before and pass after, and the device tests must pass. A `touches_safety` result means discard | `stub_pass`, labeled, and nothing is offered while it's a stub |
| `offer` | Creates the proposal (base, expiry, sha256). The next check-in points at it | — |

### 5.2 Proposal lifecycle

```
admit ─┬─ declined | blocked_over_cap (daily $ cap)
       └→ drafting → checking ─┬─ discarded (touches_safety, or safety paths unknown)
                               ├─ failed checks → retry within cap, else gave_up
                               └→ offered ─┬─ expired | stale (base ≠ running) | rejected
                                           └→ applied ─┬─ failed_verify / reverted → retry within cap, else gave_up
                                                       └→ held → candidate → gate (D6) → fleet, or awaiting_canary (B7)
```

**Per-incident cap:** 2 consults. Running out ends in `gave_up`. Only the daily dollar cap produces `blocked_over_cap`, and it's checked at `admit`, before any money is spent.

### 5.3 On the device

1. **Pull the proposal** when the check-in points at it. Apply it only if all of these hold:
   - the arm is parked and no task is running;
   - the base matches what's running: `firmware`, `code_rev`, `mcu_build` for STM32 patches, and the active overlay if there is one (the proposal replaces it);
   - the proposal hasn't expired.

   Otherwise report `stale` or `rejected`.
2. **Check the hash** (as D7 does for versions), take a snapshot, and apply the patch as an overlay with `proposal_id`.
3. **Run verify and health,** then start the mark-good timer (§6, item 2).
4. **Post `ProposalResult`.** Overlay status (`held` or `reverted`) then flows as it does today.

### 5.4 After it's applied: lock-in with one change

- **The cloud creates the overlay row itself** when the device reports `applied`. Proposal overlays never arrive inside an `IncidentReport`, so `_apply_overlay` would never see their patch. The patch is known from the proposal, and later `OverlayStatus` updates arrive as usual.
- **When it's held,** the batch builds a code candidate straight from the Doctor's checked patch. It skips a second `Coder` pass (D21's path), and the candidate then goes through the gate (D6).
- **Code candidates stop at `awaiting_canary`** until a canary that can run real device code exists (B7, build plan M8).

## 6. Safety model

1. **Only the device acts,** through the overlay process in §5.3. The Doctor has no write path to a device.
2. **Mark-good timer (new on the device).** After a proposal overlay is applied, health must hold for N seconds (proposed: 60). Then the device marks the overlay good; if it doesn't, the overlay reverts.
   - This is the RAUC `mark-good` pattern ([`research.md`](research.md) §5).
   - It's separate from "held", which the device reports after 20 minutes with no recurrence (contracts README, item 2) and which triggers lock-in.
3. **The honest risk statement.** A Doctor proposal is no riskier than a healer patch under D5a: same overlay, same revert, same parked arm. It also passes cloud checks that the healer's own patches never get. It does reach one real arm without replay or canary, because D6 gates versions, not overlays. The fleet only ever changes through D6.
4. **Out of safety:**
   - A proposal whose patch `touches_safety` (the check fails closed, `codepatch.py`) is **discarded**, not held for a person; D11 has no approval steps.
   - Advice that names safety paths is removed (§4).
   - **Until Ishaan's K17 list replaces the placeholder `safety_paths`** (`settings.py`), every proposal is discarded with "safety paths unknown".
5. **A baseline nothing can patch** *(proposal for Ishaan and Olir; K17)*. Simplex needs a trusted fallback, and D5a makes the software safety layer patchable. So keep one layer out of everyone's reach: servo EEPROM torque and angle limits, the STM32 watchdog, a hardware e-stop.
6. **Evidence is untrusted.** Logs, frames and traces can carry prompt injection (RoboPAIR, [`research.md`](research.md) §5). The prompt treats them as data, output is schema-bound and validated (`codepatch.summarize_code_patch`), and the sandbox can reach only the LLM.
7. **Hidden checks stay hidden.**
   - The fault catalog's `verify` scripts grade the benchmark and the demo, and the Doctor never sees them.
   - It writes its own reproduction test instead.
   - The local BUILD doc says code-candidate replay *uses* those scripts (§3 there). So `check` may run them once, at the end, returning pass/fail only, with no iterating against them. Patches that game a check are a known failure ([`research.md`](research.md) §4).
8. **Caps:**
   - per incident, 6 asks and 2 consults, then `gave_up`;
   - a daily dollar cap checked at `admit` (`blocked_over_cap`, the D19b pattern);
   - overnight chaos runs use a fake or capped Doctor (like K13).
9. **The kill switch** (`frozen`) stops asks, consults and offers, and closes sessions (D11).
10. **Privacy:** frames go to an outside LLM only if low-resolution and opted in; otherwise a VLM we host (K15).

## 7. Contract changes

**Nothing needs reserving ahead of time.** Device payloads keep unknown fields (`extra="allow"`), and cloud responses ignore them (`CloudModel`, `extra="ignore"`), so each field can land when its feature does.

**Version rule (D28):** optional, additive changes don't bump `CONTRACT_VERSION`; only breaking changes do (a removed or renamed field, a new required field, a changed meaning). So the L1 and L2 rows below keep contracts at `0.1`.

| Change | Where | Level |
|---|---|---|
| `IncidentReport.consult: {wanted, trigger, attempts}`, optional (the cloud also infers it, §3) | `incident.py` | L2 |
| `running.code_rev` *expected* on incidents; a consult is declined without it (never a 422, which would wedge the outbox) | `incident.py` | L2 |
| `CheckInResponse.proposal: {proposal_id, incident_id} \| None` | `device.py` | L2 |
| `GET /v1/devices/{id}/proposals/{pid}` → `Proposal{proposal_id, incident_id, base{firmware, code_rev, mcu_build?, overlay_id?}, code_patch, repro_test, why, checks, expires_at}`, plus `sha256` over `canonical_json` | new route | L2 |
| `POST /v1/devices/{id}/proposals/{pid}/result` → `ProposalResult{result: applied\|rejected\|stale\|failed_verify, overlay_id, reason, occurred_at}`. Needed because incidents are write-once (`incidents.py`) | new route | L2 |
| `Overlay.proposal_id` (where it came from) | `incident.py` | L2 |
| `POST /v1/devices/{id}/doctor/ask` with `DoctorAsk` → `DoctorAdvice{ask_id, hypotheses}` (§4) | new route | L1 |
| Events, all prefixed `doctor.`: `asked`, `advised`, `consult_opened`, `diagnosed`, `drafted`, `checked`, `proposal_offered`, `proposal_result`, `proposal_expired`, `proposal_discarded`, `blocked_over_cap`, `gave_up` | APIs §5 | L1–L2 |
| `CheckInResponse.session: {session_id, lease_s, token}`; `WS /v1/devices/{id}/session`; messages `Hello`, `ToolCall`, `ToolResult`, `Lease`, `Yield`, `Resume`, `End`; recording kind `doctor_session`. **Bumps to 0.2; auth first** | contracts | L3 |

## 8. Fit with decisions (only what changes)

- **Reused as is:**
  - evidence uploads (flow F7);
  - outcome fields and the `outcome:unresolved` label;
  - overlays and their status (D5a);
  - the fail-closed `touches_safety`;
  - the LLM adapter (the Naïve proxy answers in about 2 s with gpt-oss-120b, per `llm.py`);
  - the `Coder` adapter and its sandbox (D21);
  - events and the story (D9);
  - `frozen` (D11).

| Decision | What changes |
|---|---|
| D0, D21 (lock-in) | One change: a held proposal overlay becomes a candidate straight from the Doctor's checked patch, skipping a second `Coder` pass (§5.4) |
| D4 (the cloud's big model and harness are separate) | Supports it: the Doctor *is* the cloud harness |
| D5a, K17 | The healer is unchanged. The Doctor stays out of safety, and every proposal is blocked until the path list arrives (§6, item 4) |
| D6 | The fleet is unchanged. A proposal reaches one real arm, and §6 item 3 states that exposure plainly |
| D11 | `frozen` stops the Doctor. Discards aren't approvals |
| D12 ("doesn't orchestrate agents"), PRD principle 8 ("Compose, don't build. Naïve handles agents") | **L1 and L2: no conflict.** They're new callers of the adapters D21 and D22 already put behind Romulus. **L3: conflict.** An agent loop in Romulus runs against D12 and PRD 8. A Naïve-hosted agent calling a Romulus MCP runs against D19 and the build plan's "Won't: inbound MCP for Naïve", and Naïve computers can't reach the bench LAN anyway (`project-overview.md` §9.4). D27 has to choose |
| D22 (models) | Order unchanged: `local_agent` first, `naive_session` only "later, if `local_agent` is weak". `naive_session` would also bring general outbound network access (`project-overview.md` §9.4), which breaks the sandbox's LLM-only rule (§6, item 6) |

## 9. L3 · Session (after D27)

- **Tools.** The device tool service exposes typed tools in two tiers:
  - **Read:** logs, audit, frame, stack dump, list devices, read a file, run health checks, run tests on a scratch copy.
  - **Act:** apply an overlay, park or set mode, roll back, build and flash. Each needs an extra grant and passes the device's own policy.
  - **No raw shell.** mini-swe-agent's bash is never exposed remotely; typed tools, not prompts, set the limits ([`research.md`](research.md) §5).
- **Channel.**
  1. The check-in offers a lease.
  2. The device accepts only when it's parked and not frozen.
  3. The device opens `WS /v1/devices/{id}/session` and speaks newline-framed JSON-RPC, which MCP allows as a custom transport.
  4. Romulus forwards calls from a Streamable HTTP MCP endpoint. Under spec 2026-07-28 this is stateless forwarding ([`research.md`](research.md) §3).
- **Lease.**
  - The healer yields while the lease is held, and heartbeats keep it alive.
  - When the lease expires or the socket drops, control returns to the healer and unconfirmed overlays revert.
  - Each lease has a single-use token tied to the device and incident.
- **Auth first:** a key per device (mTLS or a signed token), audit logs at both ends, and sessions recorded as `doctor_session`.

## 10. Learning loop (through what exists)

1. **Lock-in is the known-fix library.** A held Doctor fix reaches the whole fleet as a version (D0, D6), so the fault shouldn't recur anywhere.
2. **Skills:** the batch `write_skill` (I6) runs on held Doctor overlays, just as it does on held healer overlays.
3. **Traces:** each consult's case file, ledger, draft and outcome land in the existing `traces_jsonl` export. That's the dataset for later LoRA or escalation-policy training.
4. **One new metric** in the digest (`make digest`): asks, consults, proposals applied and held, and escalations per fault family.
5. **Next research:** re-test Doctor-written skills on the healer's own model before shipping them ([`strategy.md`](strategy.md) §8).

## 11. Models and where they run

- **L1:** the LLM adapter (D22), Naïve proxy by default, with a timeout shorter than the device's `budget_s`.
- **L2 diagnose:** the same adapter with a bigger budget (about 120 s, 4k tokens), run as a background job (§5.1).
- **L2 draft:** `Coder` `local_agent` in the `coder` container (the D22 order). Frames go to the RunPod VLM later (D22).
- **L3:** runs wherever D27 says.
- **Adapter gaps to close** (`llm.py`): tool use, multi-turn conversations, images, and the 30 s / 800-token defaults.
