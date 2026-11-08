# Fleet Doctor: implementation plan

- **Status:** v1, Sat Nov 7 ~8 PM PST. Kent asked to build the Fleet Doctor end to end as reviewed PRs merged as they're ready, starting now.
- **Owns:** PR sequence, parallel work, review and merge rules, coordination with other lanes, handoffs, risks. Interfaces and file ownership are in [`interfaces.md`](interfaces.md); the design is in [`architecture.md`](architecture.md).

## 1. What Kent decided (Sat ~7:45 PM)

| Question | Answer | Effect |
|---|---|---|
| D23–D26 | Accepted as proposed | Build L1 (advise), L2 (consult) and Teach |
| D27 (where the L3 agent loop runs) | Deferred | L3 is only the typed device-tool contract (`interfaces.md` §11). No socket, lease or relay |
| Contract version rule (strategy §7 q3) | Optional additions don't bump (logged as D28) | `CONTRACT_VERSION` stays `0.1`. The `version.py` docstring and `cloud/CLAUDE.md` rule 6 change to match |
| When PRs merge | Merge everything as it's ready | No hold for the overnight launch. The Doctor ships with `doctor_mode=off` and new tables only, so whatever runs from main is unaffected until someone turns it on |

**This supersedes the phase gates in strategy §2** ("L1 only once every P0 flow is green…"). Kent's call is to build now, with the same default-off safety.

## 2. Principles for the sequence

1. **One foundation PR, then fan out.** PR 2 owns every shared file: settings, tables, ids, the event labels, the job seam, the LLM `complete()`, and empty routers and stubs. Wave-2 PRs then each own disjoint files and don't conflict.
2. **Contracts first and small** (PR 1), because Ishaan builds against them. Every change is additive and optional.
3. **Every stage starts as a labeled stub** (BUILD principle 8). The fake path runs F12 end to end before any real LLM, Coder or sandbox runs.
4. **Defaults stay safe.** The Doctor is off, auto-consult is off, unchecked proposals aren't offered, and safety isn't confirmed. A demo turns these on explicitly, and each event says it's a stub.
5. **Don't touch M8b's files.** M8b (session c9e401) owns `batch._draft_code`, `gate._replay`, `adapters/coder.py` and the generic `services/jobs.py`. The Doctor calls them and changes none of them, except one agreed branch in `_draft_code` (PR 9).

## 3. PRs and waves

```
Wave 0  PR 0a decisions D23–D28 ─┐   PR 0b this plan + interfaces.md ─┐
                                 └───────────────┬────────────────────┘
Wave 1          PR 1 contracts                   PR 2 cloud foundation           (parallel; no file overlap)
                      └───────────────┬──────────────┘
Wave 2  PR 3 L1 ask   PR 4 L2 consult   PR 5 device ref   PR 6 timeline+digest   PR 8 L3 contract   (parallel)
                          │                 └─ its interop F12 test rebases onto PR 3 + PR 4
Wave 3  PR 7 evidence + real draft/check + traces (after M8b)      PR 9 lock-in skip (after M8b)
```

| PR | Branch | Scope (files: see `interfaces.md`) | Done when | Depends on |
|---|---|---|---|---|
| 0a | `docs/fleet-doctor-decisions` | D23–D28 in the decisions log; `version.py` docstring; `cloud/CLAUDE.md` rule 6; contracts README; `OVERNIGHT.md` loses the go/no-go line; status lines in README and strategy | Merged | — |
| 0b | `docs/fleet-doctor-plan` | `implementation-plan.md`, `interfaces.md`, README reading order | Merged | — |
| 1 | `doctor/contracts` | `contracts/doctor.py`; `CheckInResponse.proposal`, `IncidentReport.consult`, `Overlay.proposal_id`; examples; schema; README; APIs doc §2 and §3 | Contract tests pass; `make test` is green | 0a |
| 2 | `doctor/foundation` | settings and `.env.example`; `DoctorConsult` and `DoctorProposal` tables; ids prefixes; `domain/doctor.py`; `services/doctor_jobs.py`; `Llm.complete`; `story.LABELS` and the APIs §5 row; empty routers `api/doctor.py` and `api/proposals.py` mounted; stub `doctor.tick` in `STEPS`; stub `services/proposals.py`; clock fixture | Unit tests for domain, jobs and llm; `make test` is green; behavior is unchanged with the Doctor off | 0b |
| 3 | `doctor/l1-ask` | `services/doctor_ask.py`, the route in `api/doctor.py`, `test_f12a_doctor_ask.py` | F12a passes: advice, 503, 409, 429 plus event, safety filter | 1, 2 |
| 4 | `doctor/l2-consult` | `services/doctor.py` tick and state machine; `services/proposals.py`; `services/doctor_evidence.py` v0; the routes in `api/proposals.py`; check-in pointer in `devices.py`; `test_f12_doctor_consult.py`; F12 in the flows doc (after #21 merges) | F12 cloud side passes, including stale, discard, gave_up, cap and frozen | 1, 2 |
| 5 | `doctor/device-ref` | `tools/fake_device`: client, `doctor.py` (breaker, triggers, applier, mark-good), outbox kind, `consult` scenario, chaos kind, `fake_cloud` routes; unit tests; interop F12 case | Fake device runs F12 end to end through `device_http` | 1; interop test after 3 + 4 |
| 6 | `doctor/timeline` | `Story.ledger`, `LINES`/`STAGE`/`STATUS`, story partial, digest counts and `doctor_by_kind` | Ledger renders on the incident page; digest test | 2 (codes against the event shapes in `interfaces.md` §8) |
| 7 | `doctor/evidence-real` | Trace and code readers; `draft=coder` through M8b's `Jobs` and `Coder.draft(task=…)`; `check=sandbox` through `replay_code_patch` (repro-only patch must fail, full patch must pass); `doctor_consult` recordings and traces export | Tests with a fake sandbox runner; one manual live run if the LLM key works | 4, M8b |
| 8 | `doctor/l3-contract` | `contracts/tools.py` catalog; README; architecture §9 points to it | Contract tests | 1 |
| 9 | `doctor/lock-in` | One branch in `batch._draft_code` for overlays with `raw.proposal_id`; F3 case | A held Doctor overlay becomes a candidate without a Coder call, then `awaiting_canary` | 4, M8b |

**Critical path:** 0b → 2 → 4 → 5's interop test. PR 2 is the bottleneck, so it goes first and stays small.

## 4. How each PR is built, reviewed and merged

1. **Build.** One implementer agent per PR, in its own git worktree, with `interfaces.md` as the contract.
   - It opens a branch from fresh `origin/main` and runs `make testdb` and `uv run pytest`. Each pytest session gets its own throwaway database on `romulus-testdb`.
   - It never runs `make up`, `make down` or `make reset` in the main checkout, which is Kent's demo stack on :8000.
   - It pushes, then opens the PR with `gh pr create`. Commits carry no AI co-author lines.
2. **Review.** A fresh reviewer agent (not the implementer) reads the diff against `interfaces.md`, `cloud/CLAUDE.md` and the architecture doc's safety rules, runs the tests, and reports findings ranked by severity. It checks:
   - correctness;
   - the cloud rules: domain stays pure, every write records an event in the same transaction, services never commit;
   - safety defaults;
   - FR11: no session held during an LLM call;
   - whether it conflicts with M8b's files.
3. **Fix.** The implementer fixes the confirmed findings. The orchestrator (this session) makes the call on any disputed ones.
4. **Merge.** Rebase onto `origin/main`, re-run the full suite, then `gh pr merge --squash --delete-branch`. **Never force-push `main`.**
5. **Record.** The orchestrator updates this plan's tracker (§7) and `features.txt`.

## 5. Coordination

| With | About | Arrangement |
|---|---|---|
| M8b (session c9e401) | `services/jobs.py`, `Coder.draft(task=)`, `batch._draft_code`, the `coder` compose service | They build the generic `Jobs` in our shape. We use a stand-in (`doctor_jobs.py`) until it merges, then a one-line swap. PR 7 and PR 9 wait for M8b. `worker.STEPS` and `story.py` are append-only on both sides |
| #21 (docs, session c9e401) | Build plan and flows doc | They remove the 12:30 AM go/no-go wording. PR 4 adds F12 to the flows doc only after #21 merges |
| Device bridge (session 00ebfa) | `tools/fake_device` client and outbox | They reuse the client and outbox unchanged and message us before touching contracts, device routes or `fake_device` |
| Ishaan | Contract additions; K17 | §6 |

## 6. Handoffs

**To Ishaan, when PR 1 merges** (contracts are additive, so nothing he's built breaks):
1. `CheckInResponse.proposal` (`{proposal_id, incident_id, expires_at}` or null) and `IncidentReport.consult` (`{wanted, trigger, attempts}`) are new and optional. Ignore them until you want them.
2. **L1 is one HTTP call from the harness.** `POST /v1/devices/{id}/doctor/ask` returns ≤300 tokens of hypotheses. On any non-200, skip the rest of that heal's asks (circuit breaker). The reference code is `tools/fake_device/src/fake_device/doctor.py` (`TriggerHook`, `CircuitBreaker`).
3. **L2 adds a proposal applier.** Pull, check the sha, check parked, base and expiry, apply as an overlay with `proposal_id`, run the mark-good timer, report the result. The reference is `ProposalApplier` in the same file.
4. **We still need the K17 safety and visor path list.** Until it arrives, `safety_paths_confirmed=false` and every proposal is discarded.

**To Kent:**
- Flip `SAFETY_PATHS_CONFIRMED` only after the K17 list lands.
- Set `DOCTOR_USD_PER_1K_TOKENS` to the real price.
- Choose the demo settings (§8).

## 7. Tracker

**Status Sat Nov 7, ~11:40 PM PST: every planned PR is merged.** Every PR had an independent review. #39's `batch.py` hook was also reviewed by the cloud lead's reviewer. Each was merged only after its fixes were in. The safety-critical PRs (#32, #44) had a second, focused re-review.

| PR | Status | GitHub | What the review changed |
|---|---|---|---|
| 0a | merged | #23 | D23–D28 logged; D28 = optional additions don't bump |
| 0b | merged | #22 | this plan + `interfaces.md` |
| 1 | merged | #26 | lenient pointer (a bad pointer can't hide `frozen`), `budget_s` clamp, `ProposalPointer.sha256` |
| 2 | merged | #27 | spend sum fails closed on bad `cost_usd`, parser prefers real answers, missing usage is estimated (never $0), boundary safety filter, `forget()` |
| — | merged | #29 | `services/doctor_llm.build_doctor_llm` (shared by PR 3 and 4) |
| 3 | merged | #34 | `doctor.asked` committed before the LLM call, failed calls cost money, device-day cap can't be dodged with random incident ids, deduped cap events |
| 4 | merged | #35 | **serve-time safety gate**: pointer and GET re-check mode, safety confirmation and `touches_safety` per request; a stub check offers only stub drafts; reused overlay ids → 409 |
| 5 | merged | #32 | **crash-safe mark-good** (`recover()` at boot), a failure sticks, recovery only touches our own overlay, monotonic multi-sample timer, retry on 409 |
| 6 | merged | #33 | the gate status wins over a held close; the Doctor never hides a needs-a-person status; spend total finite |
| 7 | merged | #44 | **check soundness**: repro must be a new test file that fails (exit 1, ids in file) then passes; no test tampering; planted `pytest.py`/`sitecustomize`/`.pth` refused; `PYTHONSAFEPATH=1`; device suite on non-test hunks only; held-out `verify/` run once (pass/fail only); flag-only fixes refused; shared coder budget and `coder_retry` |
| 8 | merged | #30 | fullmatch, strict path and id rules, `grant` on act tools (draft, D27 deferred) |
| 9 | merged | #39 | **fail closed**: skip the Coder only for a server-verified proposal (same device, fleet, patch sha, base, overlay, real check); proposal `touches_safety` OR'd in; `source="doctor"` gets the real replay; no early-run loop |
| F12 interop | merged | #42 | the real cloud + unmodified fake device run F12 end to end, plus freeze; story tie-order fix |

### Open follow-ups (not Doctor code; owner in brackets)

| Item | Why it matters | Owner |
|---|---|---|
| **B5 egress (#46)**: the coder container gets only an LLM proxy, with no key inside | Until it merges, **keep `DOCTOR_DRAFT=coder` off on real device text**: the agent sees untrusted log text, and its container has a bridge network and the key | cloud lead (c9e401) |
| K17 safety and visor path list | `SAFETY_PATHS_CONFIRMED` stays false, and every proposal is discarded, until it lands | Ishaan → Kent |
| Real token price | `DOCTOR_USD_PER_1K_TOKENS=0.001` is a placeholder *(inferred)*; the dollar cap is only as good as it | Kent |
| Gate replay hardening + `batch._drafts_this_hour` refund kinds | The planted-runner and fake-summary-line tricks the Doctor now blocks apply to the gate's replay too; zero-token Doctor drafts aren't yet refunded in the batch's count | cloud lead, in progress on `cloud/replay-hardening` |
| L3 live sessions | D27 deferred; `romulus_contracts.tools` is a draft until Ishaan's h3 tool list | Kent + Ishaan |
| MCAP evidence reader | `mcap` isn't a dependency; evidence is labeled "not wired" | later |
| Adopt the device reference on the Pi | `tools/fake_device/src/fake_device/doctor.py` ("How to adopt this on the Pi"); run the applier in a supervisor process | Ishaan |

## 8. Demo settings (fake mode, honest labels)

```
DOCTOR_MODE=fake DOCTOR_OFFER_STUB_CHECKED=true SAFETY_PATHS_CONFIRMED=true
```

This setup runs with `make sim` and the fake device's `consult` scenario (or the `demo` chaos profile's `doctor` kind). The incident page shows the hypothesis ledger, the stub-labeled draft and check, the proposal offered, applied and held, and then lock-in. Say on stage that the check is a stub and the model is fake. With a working LLM key, `DOCTOR_MODE=live` makes the ledger real while the draft stays fake (strategy §3).

## 9. Risks specific to execution

| Risk | Mitigation |
|---|---|
| A Doctor merge breaks the overnight run from main | Default off, new tables only, and each PR must leave the full suite green. No schema change touches existing tables |
| Parallel PRs conflict | PR 2 pre-creates every shared seam. Wave-2 PRs own disjoint files (`interfaces.md` §1) |
| M8b slips | PR 7 and PR 9 wait. L1, L2 (fake draft, stub check), device reference, timeline and digest don't depend on it |
| A reviewer misses a safety bug | Reviewers get the safety checklist (architecture §6). Proposals stay blocked by `safety_paths_confirmed=false` by default |
| Context sprawl in the orchestrator | Implementers and reviewers report short summaries. Details live in PRs and this tracker |
