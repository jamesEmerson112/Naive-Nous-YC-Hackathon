# Fleet Doctor: architecture

- **Status:** proposal, Sat Nov 7 ~5:45 PM PST. Everything here is *(inferred)* design unless a line cites a decision (D#) or a file.
- **Owns:** how the Fleet Doctor works: its parts, flows, contracts, safety and learning loop. For why it exists, see [`concept.md`](concept.md). For build order, see [`strategy.md`](strategy.md). For outside facts, see [`research.md`](research.md).
- **File references** point at `origin/main` around `33def3d`. The device code folder is `CODE_ROOT` in `romulus_contracts.codepatch`; whether it's `edge/` or `device/` is still being settled with Ishaan.

---

## 1. Where it sits

```
 DEVICE (Ishaan)                                            ROMULUS (Kent)
 ┌─────────────────────────────────────────┐              ┌──────────────────────────────────────────────┐
 │ healer: detect → snapshot → park →       │              │ device API: check-in, incidents, recordings  │
 │   evidence → diagnose/fix → verify       │              │                                              │
 │   ▲ escalation policy (§3)               │  L1 POST     │ /doctor/ask ──▶ advisor (LLM adapter, D22)   │
 │   │ ask_doctor tool ◀──── advice ────────┼──────────────┤                                              │
 │ proposal applier → overlay (D5a)         │  L2 check-in │ consult pipeline (worker, §5):               │
 │   snapshot, park, verify, mark-good timer│ ◀─ proposal ─┤   admit >> gather >> diagnose >> draft       │
 │ device tool service (logs, audit, frame, │              │   >> check >> offer   (coder sandbox, D21)   │
 │   stack dump, checks, tests, park)       │  L3 socket   │ session relay (post-hackathon, §9)           │
 │                                          ├═════════════▶│ timeline: doctor.* events (D9)               │
 └─────────────────────────────────────────┘ device dials  └──────────────────────────────────────────────┘
                                              out, always     held → lock-in → gate (D0, D6): unchanged
```

The design adds one new trigger and one new delivery path to things the plans already have:
- the evidence upload (F7);
- the LLM and Coder adapters (D21, D22);
- overlays (D5a);
- the gate (D6);
- the timeline (D9).

The Doctor's output has the same type as the healer's output: a patch applied as an overlay. That's why lock-in and the gate stay untouched (Constitution: composable).

## 2. Parts

| Side | Part | New or reused | Owner |
|---|---|---|---|
| Device | Escalation policy: decides when to ask (§3) | New | Ishaan |
| Device | `ask_doctor` tool in the healer's harness (L1) | New, small | Ishaan |
| Device | Proposal applier: pull, hash-check, base check, apply as an overlay, mark-good timer, report the result (L2) | New, built on the overlay machinery | Ishaan |
| Device | Device tool service: the healer's `tools/` as one local service (L3; also used by the healer itself) | New, can start any time | Ishaan |
| Cloud | Advisor: `POST /v1/devices/{id}/doctor/ask` → LLM adapter | New route; reuses D22 | Kent |
| Cloud | Consult pipeline (§5) as one worker step | New service; reuses Coder (D21), sandbox (M8), blob store, events | Kent |
| Cloud | `consults` and `proposals` tables, plus proposal routes | New | Kent |
| Cloud | Evidence readers: text from MCAP log windows, frames from LeRobot tars, the trace | New (the cloud only stores these today: architecture §7) | Kent |
| Cloud | Timeline chapter: the hypothesis ledger | New view over `doctor.*` events | Kent |
| Cloud | Session relay (L3) | Post-hackathon | Kent |

## 3. When the healer asks (the escalation policy)

The device owns the triggers, because only the device sees its own loop. The order is objective signals first and the model's opinion last. Small models' stated confidence is unreliable ([`research.md`](research.md) §4).

| # | Trigger | Default (tunable in the harness) | Asks for |
|---|---|---|---|
| T1 | The healer's model is unavailable (model server down, out of memory) | Immediately | L2 |
| T2 | Verify still fails after K patch attempts, or the build or flash fails | K = 2 | L1, then L2 |
| T3 | An overlay reverted itself (the fault came back, or health failed) | Once | L2 |
| T4 | Looping: the same command 3 times, or 5 steps with no new file read or changed | — | L1 |
| T5 | 70% of the step or time budget used with no passing check | — | L1 |
| T6 | Tier 3 (wrong behavior, no error) | At the start of the heal | L1 |
| T7 | The candidate patch touches safety, the visor or STM32 C | Before applying it | L1 (a second opinion only; see §6) |
| T8 | No skill matches the fault | At the start of the heal | L1 |
| T9 | The model's own confidence is low | Last resort | L1 |

**The cloud can also start a consult on its own.** An incident reported as `resolved: false` whose cause isn't `hardware` qualifies for an L2 consult even without a request, as long as the fleet isn't frozen and the caps (§6) allow it. That covers healers that never learned to ask.

## 4. L1 · Advise

The device dials out, so this fits the outbound-only model (D17, D19).

```
POST /v1/devices/{device_id}/doctor/ask
  { incident_id?, trigger: "T4", question: "...",
    context: { tier, code_rev, signals, log_tail,
               recent_steps: [last 5 healer messages],     # SWE-Protégé's window
               files: [{path, excerpt}] },
    budget_s: 20 }
→ 200 { ask_id, answer, hypotheses: [{claim, how_to_check}],
        suggested_next: [...], evidence_refs: [...], model, cost_usd }
→ 408/5xx/timeout  ⇒  the healer carries on as if no answer came (D8)
```

- **The answer is guidance, not a patch.** That keeps the healer in charge and the output small (Constitution: minimal output). A short code snippet is fine.
- **It's synchronous inside the API process for v0** (one LLM call, roughly 2–20 s). It moves to the worker if latency hurts (K5).
- **Caps:** at most 6 asks per incident (SWE-Protégé's cap; [`research.md`](research.md) §4).
- **Timeline:** each ask writes `doctor.asked` and `doctor.advised`, with the hypotheses as the "why".

## 5. L2 · Consult

### 5.1 The pipeline

```python
consult = admit >> gather >> diagnose >> draft >> check >> offer        # new, worker step
follow  = proposal_result >> overlay_status >> lock_in >> gate          # D0/D6 path, unchanged
teach   = known_fix >> skill >> trace                                   # §10
```

| Stage | What it does | Stub tonight (labels itself, BUILD principle 8) |
|---|---|---|
| `admit` | Consult or not? Checks the request or the implicit rule (§3), `frozen`, caps and duplicates. **If the fault signature has a known fix, it skips the LLM** and offers that fix straight away | `decline("fleet doctor not wired")` |
| `gather` | Builds the case file: the incident, signals, log tail, the healer's trace (what it already tried), MCAP log text, 1–3 frames, code at the base `code_rev` from the registry, similar past incidents, matching skills. Everything is marked as untrusted data | Incident and log tail only |
| `diagnose` | LLM over the case file → a hypothesis ledger: each claim with its evidence and a check | One LLM call, prose |
| `draft` | Coder: **write a failing reproduction test from the recorded trace first, then the fix** → `{code_patch, repro_test, why}` | `Id`: no draft; the consult ends with advice |
| `check` | Runs the cloud ladder rungs 0–3 (4 when Renode works) in the sandbox ([`research.md`](research.md) §5). The repro test must fail before and pass after; the device test suite must pass; `touches_safety` blocks the proposal | `stub_pass`, labeled |
| `offer` | Creates a proposal (base, expiry, sha256). The next check-in points at it | — |

### 5.2 Proposal lifecycle

```
drafted → checking → offered → applied → held → locked_in (via D6)
              │          │         │        └→ reverted → retry once, then doctor.gave_up
              │          │         └→ failed_verify (device) → retry once, then gave_up
              │          ├→ rejected | stale (base ≠ running code_rev) | expired
              └→ blocked_safety | blocked_over_cap   (wait for a person, like D19b)
```

**On the device:**
1. Pull the proposal.
2. Check its hash (D7) and its base. A mismatch is reported as `stale`.
3. Park with torque off and take a snapshot.
4. Apply it as an overlay that carries `proposal_id`.
5. Run verify and health.
6. Start the mark-good timer: the overlay reverts unless health holds for N seconds and the device marks it good (the RAUC pattern; [`research.md`](research.md) §5).
7. Post `ProposalResult`.
8. Overlay status (`held` or `reverted`) then flows as it does today.

## 6. Safety model

1. **Only the device acts,** and only through the overlay process above. The Doctor has no write path to a device.
2. **Closing the D6 gap.** A proposal reaches one real arm without replay or canary (the audit; `cloud-platform-decisions.md` D6 gates *versions*). Five things compensate:
   - the cloud ladder;
   - the reproduction test;
   - the base check and expiry;
   - one device only;
   - the device's mark-good timer.

   The fleet still only changes through D6.
3. **No safety-layer proposals.** A Doctor draft with `touches_safety` (which fails closed, `codepatch.py`) is never offered. It shows as `blocked_safety` and waits for a person, following the PRD non-goal (`cloud-platform-prd.md` §6) and the D19b pattern. The healer's own D5a powers are unchanged.
4. **A baseline nothing can patch.** Simplex needs a trusted fallback, and D5a makes the software safety layer patchable. So keep one layer out of everyone's reach *(proposal for Ishaan and Olir; K17)*:
   - servo EEPROM torque and angle limits;
   - the STM32 watchdog;
   - a hardware e-stop.
5. **Evidence is untrusted.** Logs, frames and traces can carry prompt injection (RoboPAIR, [`research.md`](research.md) §5).
   - The prompt treats them as data.
   - Output is schema-bound and validated (`codepatch.summarize_code_patch`).
   - The sandbox can reach only the LLM.
6. **Hidden checks stay hidden.** The fault catalog's `verify` scripts grade the benchmark and the demo but are never shown to the Doctor. Patches that game the check passed 11 out of 11 times in RoboBugBench. The Doctor writes its own reproduction tests.
7. **Caps:**
   - at most 6 asks and 2 consults per incident;
   - a dollar cap per day;
   - over the cap → `blocked_over_cap` (D19b);
   - overnight chaos runs use a fake or capped Doctor (like K13).
8. **The kill switch** (`frozen`) stops new consults and offers and closes sessions (D11).
9. **Privacy.** Frames go to an outside LLM only if low-resolution and opted in; otherwise use a VLM we host (K15).

## 7. Contract changes

All of these are optional additions. Under the APIs draft policy, additions made before I0 don't bump `CONTRACT_VERSION` (`cloud-platform-apis.md` §0; contracts README). But architecture §9 rule 6 says *any* change bumps it, so one of the two rules needs fixing (the audit found the conflict).

| Change | Where | Level |
|---|---|---|
| `IncidentReport.consult: {wanted, trigger, attempts}` | `incident.py` | L2 |
| Require `running.code_rev` on incidents (already proposed in the contracts README, item 4) | `incident.py` | L2 |
| `CheckInResponse.proposal: {proposal_id, incident_id} \| None` | `device.py` | L2 |
| `GET /v1/devices/{id}/proposals/{pid}` → `Proposal{proposal_id, incident_id, base{firmware, code_rev}, code_patch, repro_test, why, checks, touches_safety, expires_at}` plus `sha256` over `canonical_json` | APIs §3, new | L2 |
| `POST /v1/devices/{id}/proposals/{pid}/result` → `ProposalResult{result: applied\|rejected\|stale\|failed_verify, overlay_id, reason, occurred_at}`. Needed because incidents are write-once (`incidents.py`) | APIs §3, new | L2 |
| `Overlay.proposal_id` (where the overlay came from) | `incident.py` | L2 |
| `POST /v1/devices/{id}/doctor/ask` with `DoctorAsk` / `DoctorAdvice` (§4) | APIs §3, new | L1 |
| Event kinds: `doctor.asked`, `doctor.advised`, `consult.requested`, `doctor.diagnosed`, `doctor.drafted`, `doctor.checks_passed`/`failed`, `proposal.offered`/`applied`/`rejected`/`stale`/`expired`/`blocked`, `doctor.gave_up` | APIs §5 | L1–L2 |
| `CheckInResponse.session: {session_id, lease_s, token}`; `WS /v1/devices/{id}/session`; messages `Hello`, `ToolCall`, `ToolResult`, `Lease`, `Yield`, `Resume`, `End`; recording kind `doctor_session`. **Bumps to 0.2; auth first** | contracts, new | L3 |

## 8. Fit with the existing plans

### 8.1 Pieces that already exist (from the repo audit)

| Piece | Gives the Doctor | Missing |
|---|---|---|
| Evidence: `IncidentReport.signals/recordings/tier/log_tail`; recording kinds `agent_trace`, `log_window`, `incident_window` (`incident.py`, `telemetry.py`) | Transcript, logs, frames | Readers (MCAP and LeRobot files are stored, never parsed) |
| Outcome fields and labels: `resolved`, `suspected_cause`, `category`; `outcome:unresolved` (`labels.py`) | The query "which incidents need a consult" | Any cloud path for unresolved software incidents |
| Check-in response (`device.py`); `CloudModel` ignores unknown fields | The only per-device channel; adding fields is safe | The `proposal` slot |
| Overlays and their status (D5a) | The whole apply, verify, revert cycle | `proposal_id` |
| `touches_safety`, fails closed (`codepatch.py`) | The safety block | Ishaan's real list of safety paths (K17; `safety_paths` is a placeholder in `settings.py`) |
| LLM adapter (`llm.py`) | Naïve proxy, gpt-oss-120b, about 2 s | Tool use, multi-turn, images; 30 s and 800-token defaults |
| Coder adapter and sandbox (`coder.py`; architecture §2) | Sandboxed drafting and tests | A "fix from evidence" entry point (today's `draft()` assumes a device patch exists); B1, B4, B5, B8 |
| Gate's `replay_code_stub` (`gate.py`) | Its real version is the same work as `check` | — |
| Skills: `knowledge_dir`, batch `write_skill`, I6 | Where Doctor fixes teach the healer | — |
| Events and story (`events.py`, `story.py`); actor class `agent` | The timeline picks up `doctor.*` with no rewrite | The new kinds |

### 8.2 Decisions this touches

| Decision | Effect |
|---|---|
| D0 (agent fixes, batch locks in) | Extended: a second fixer. Lock-in is unchanged |
| D4 (the cloud's big model and harness are separate) | **Supports it:** the Doctor *is* the cloud harness |
| D5, D5a (healer patches anything) | Unchanged for the healer. Doctor proposals that touch safety are blocked (§6.3) |
| D6 (gate) | Unchanged for the fleet. The single-device path is covered by §6.2 |
| D7, D8 | Proposals are hash-checked, expire, and the device never waits |
| D9, D10 | The hypothesis ledger becomes a chapter in the incident story |
| D11 (kill switch is the only human control) | `frozen` stops the Doctor. "Blocked" states wait for a person but aren't approvals (like D19b) |
| **D12** ("The platform doesn't orchestrate agents… avoids building a second agent system", decisions line 280) | **Conflict.** Resolution: drafting runs through the Coder adapter's `naive_session` provider (D22, line 450: "a Naïve managed-agent session… clones the repo and returns a diff"). That keeps the agent in Naïve and showcases the mandatory SDK. L1 advice is a single LLM call, like `explain`. Needs D23 ([`strategy.md`](strategy.md) §6) |
| D13 (trust ledger cut) | No per-action autonomy ladder; caps and blocks stand in for it |
| D17, D19 (outbound only) | L1 and L2 fit. L3's socket is opened by the device, so Romulus still never calls a device; Naïve-hosted agents can't reach the LAN anyway (`project-overview.md` §9.4) |
| D21 (coding agent for code patches) | Same machinery, new trigger (unresolved incidents instead of held overlays) |
| D22 (models) | Naïve proxy by default; the RunPod VLM for frames |
| PRD principle 7 ("LLMs write prose and skills, never merged config") | Tension: a Doctor rules or params diff is config written by an LLM. It travels as an overlay and the merge stays deterministic, so the principle holds at lock-in. The D23 note should say so |

## 9. L3 · Session (post-hackathon)

- **Tools.** The device tool service exposes typed tools in two tiers:
  - **Read:** logs, audit, frame, stack dump, list devices, read a file, run health checks, run tests on a scratch copy.
  - **Act:** apply an overlay, park or set mode, roll back, build and flash. Each act tool requires a step-up grant and passes the device's own policy.
  - **No raw shell.** mini-swe-agent's bash tool is never exposed remotely; typed tools, not prompts, set the limits ([`research.md`](research.md) §5).
- **Channel.**
  1. The check-in response offers a lease.
  2. The device accepts only when it's parked and not frozen.
  3. The device then opens `WS /v1/devices/{id}/session` and speaks newline-framed JSON-RPC, which MCP allows as a custom transport.
  4. Romulus exposes a Streamable HTTP MCP endpoint to the Doctor and forwards each call. Under MCP 2026-07-28 this is stateless forwarding ([`research.md`](research.md) §3).
- **Lease.**
  - The healer yields while the lease is held.
  - Heartbeats keep it alive. When it expires or the socket drops, control returns to the healer and unconfirmed overlays revert.
  - Each lease has a single-use token tied to the device and incident.
- **Auth before anything else:** a key per device (mTLS or a signed token), audit logs at both ends, and session recordings stored as `doctor_session`.
- **Not for the hackathon:** direct LAN calls into the Pi. They demo fine but contradict the architecture.

## 10. Learning loop

1. **Known fixes.** A held Doctor fix is stored under its fault signature: incident kind, tier, error code, file and function, and code family. When the same signature shows up again, `admit` offers the fix with no LLM call.
2. **Skills.** The batch `write_skill` (I6) writes a short skill for that fault family. Before shipping it, re-test it on the healer's own model with Ishaan's benchmark, because skills written by a strong model can hurt a weak one ([`research.md`](research.md) §4).
3. **Traces.** Every consult's case file, ledger, draft and outcome is saved as ShareGPT-style JSONL (the telemetry `traces_jsonl` export). That builds the dataset for later LoRA or escalation-policy training (SWE-smith, SWE-Protégé).
4. **The metric.** The escalation rate per fault family, and the Doctor's hit rate, go in the digest (`make digest`).

## 11. Models and where they run

- **L1 advisor:** the LLM adapter (D22), Naïve proxy by default. It needs bigger budgets (about 120 s, 4k+ tokens) and, for frames, the RunPod VLM (Qwen3-VL per D22).
- **L2 drafting:** the Coder adapter, either `local_agent` in the `coder` container or `naive_session` (§8.2, D12). The pipeline itself is an ordinary worker step: no new agent framework.
- **L3:** the agent loop runs in Romulus, because only the relay can reach the device. Naïve sandboxes can't reach the bench LAN.
- **Adapter gaps to close:** tool use, multi-turn conversations, images, timeouts and token limits (`llm.py`).
