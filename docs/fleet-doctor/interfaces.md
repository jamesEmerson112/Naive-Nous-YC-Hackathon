# Fleet Doctor: interfaces and entry points

- **Status:** v1, Sat Nov 7 ~8 PM PST. This is the build contract for the Fleet Doctor PRs ([`implementation-plan.md`](implementation-plan.md)). Each PR implements its section here. A PR that needs to change a name or shape changes this file in the same PR.
- **Owns:** where each piece of code lives, the exact routes, models, tables, settings, events and seams, and which PR owns which file. The *why* is in [`architecture.md`](architecture.md); decisions D23–D28 are in [`../cloud-platform-planning/cloud-platform-decisions.md`](../cloud-platform-planning/cloud-platform-decisions.md).
- **Scope:** L1 (advise), L2 (consult), and Teach. L3 is deferred (D27): only the typed device-tool contract (§11) gets built.
- **Paths:** `cloud/…` means `cloud/src/romulus_cloud/…` unless it starts with `cloud/tests`. `contracts/…` means `packages/contracts/src/romulus_contracts/…`. Line numbers are from `origin/main` at `2486475` and will drift.

---

## 0. What the code survey changed

The architecture doc assumed six things the code doesn't have. Each one is settled here:

| Assumed | Reality on main | Settled as |
|---|---|---|
| Copy `workorders.sync`'s background-job pattern | `sync` makes quick HTTP calls. Nothing in `cloud/` runs work in the background, and `run_tick` runs steps one after another | A `Jobs` seam (§6.2) that matches the generic runner M8b is building (`services/jobs.py`). `doctor.tick` only submits and polls |
| "The D19b dollar-cap pattern" | Count caps on work orders only. Nothing records LLM spend | Every Doctor event that spends money carries `data.cost_usd`. The day's spend is the sum of those events (events are the spine). A pure `check_spend` (§6.1) is modelled on `triage.check_caps` |
| A generic LLM call | `Llm` has only `explain` and `write_skill`. `_chat` is private, has no JSON mode and drops usage | Add `Llm.complete(...) -> LlmReply` (§6.3). No `response_format`; the prompt asks for JSON and the domain parser tolerates prose |
| `safety_paths` is visibly a placeholder | It defaults to `["safety", "visor"]` with no "unconfirmed" flag | New setting `safety_paths_confirmed: bool = False`. While it's false, every proposal is discarded with "safety paths unknown" (D25) |
| `Overlay.proposal_id` column | No such column, and after M7 new columns need a hand-run `ALTER` | No column. The proposal id lives in `overlays.raw["proposal_id"]`. The contract `Overlay` gets an optional `proposal_id` |
| Lock-in skips a second Coder pass | `batch._draft_code` calls `coder.draft` for every held overlay | One `doctor_lockin.proposal_draft(...) or <the Coder>` branch in `_draft_code` and `_await_drafts`, verified against the proposal row (§9) |

Two more facts shape the PRs:
- **`ids.PREFIXES` is a closed set**, so add `cons`, `prop` and `ask`.
- **The story-labels test** (`cloud/tests/unit/domain/test_domain_story_labels.py`) fails if any event kind in the APIs doc §5 table or in `events.record(...)` calls lacks a `story.LABELS` entry. So the §5 table row and every `LABELS` entry land in the same PR (PR 2).

## 1. Map

```
DEVICE SIDE (reference code Ishaan adopts)          CLOUD (Romulus)
tools/fake_device/src/fake_device/                   cloud/src/romulus_cloud/
  client.py   ask_doctor, get_proposal,                api/doctor.py      POST /v1/devices/{id}/doctor/ask        (PR 3)
              report_proposal_result         ──────▶   api/proposals.py   GET  /v1/devices/{id}/proposals/{pid}   (PR 4)
  doctor.py   CircuitBreaker, TriggerHook (L1),                           POST /v1/devices/{id}/proposals/{pid}/result
              ProposalApplier + MarkGoodTimer (L2)     services/devices.py  check_in → CheckInResponse.proposal  (PR 2 stub, PR 4)
  outbox.py   + kind "proposal_result"                 services/doctor_ask.py   L1 handler logic           (PR 3)
  arm.py      calls the applier on check-in             services/doctor.py       consult state machine + tick (PR 2 stub, PR 4)
  scenarios   + "consult"                               services/proposals.py    offer, pointer, result, expiry (PR 2 stub, PR 4)
  fake_cloud  + the 3 routes                            services/doctor_evidence.py  case-file readers     (PR 4 v0, PR 7 full)
                                                        services/doctor_jobs.py  Jobs seam                  (PR 2)
                                                        services/doctor_spend.py spent_today               (PR 2)
packages/contracts/src/romulus_contracts/              domain/doctor.py         pure rules                  (PR 2)
  doctor.py   DoctorAsk … ProposalResult  (PR 1)       adapters/llm.py          + complete()                (PR 2)
  tools.py    L3 device-tool catalog      (PR 8)       models.py                + DoctorConsult, DoctorProposal (PR 2)
  device.py   CheckInResponse.proposal    (PR 1)       domain/story.py          LABELS (PR 2); ledger (PR 6)
  incident.py IncidentReport.consult, Overlay.proposal_id (PR 1)   domain/digest.py  doctor counts (PR 6)
                                                        services/batch.py        _draft_code skip (PR 9)
```

## 2. Contracts (`contracts/doctor.py`, PR 1)

**These are optional, additive changes, so there's no version bump (D28).** Device models extend `DeviceModel` (`extra="allow"`); cloud models extend `CloudModel` (`extra="ignore"`).

```python
# device → cloud (DeviceModel)
class DoctorContext(DeviceModel):
    tier: int | str | None = None
    code_rev: str | None = None
    signals: OpenDict = {}
    log_tail: list[str | dict] = []
    recent_steps: list[str | dict] = []        # last ≤5 healer messages
    files: list[dict] = []                     # [{path, excerpt}]

class DoctorAsk(DeviceModel):
    incident_id: UUID | None = None            # the incident may not be reported yet
    trigger: str                               # "T1".."T7", an open string
    question: str = ""
    context: DoctorContext = DoctorContext()
    budget_s: float = 20                       # the device gives up after this; clamped to [3, 120]
    contract_version: str | None = None

class ConsultRequest(DeviceModel):              # IncidentReport.consult
    wanted: bool = False
    trigger: str | None = None
    attempts: int | None = None                # patch attempts the healer made

class ProposalResult(DeviceModel):
    result: Literal["applied", "rejected", "stale", "failed_verify"]
    overlay_id: str | None = None              # required when result == "applied"
    reason: str = ""
    occurred_at: UtcDatetime | None = None
    contract_version: str | None = None

# cloud → device (CloudModel)
class Hypothesis(CloudModel):
    claim: str
    how_to_check: str = ""

class DoctorAdvice(CloudModel):
    ask_id: str
    hypotheses: list[Hypothesis]               # ≤ ~300 tokens in total (enforced cloud-side)
    model: str = ""

class ProposalPointer(CloudModel):              # CheckInResponse.proposal
    proposal_id: str
    incident_id: UUID
    expires_at: UtcDatetime
    sha256: str | None = None                  # of the exact body GET serves (see §6.5)

class ProposalBase(CloudModel):
    firmware: Version | None = None
    harness: Version | None = None
    code_rev: str
    mcu_build: str | None = None
    overlay_id: str | None = None              # the active overlay this proposal replaces

class Proposal(CloudModel):
    proposal_id: str
    incident_id: UUID
    base: ProposalBase
    code_patch: str                            # unified diff relative to CODE_ROOT (codepatch.py)
    repro_test: str | None = None
    why: str = ""
    checks: dict = {}                          # {"stub": bool, "fails_before": bool|None, "passes_after": bool|None, ...}
    expires_at: UtcDatetime

def proposal_body_and_sha256(p: Proposal) -> tuple[bytes, str]:
    """Dump once with manifest.canonical_json, hash that same dump (the firmware_response pattern)."""
```

**Edits to existing models:**
- `device.CheckInResponse` gets `proposal: ProposalPointer | None = None`. Update `test_contract_models.py:194-207`, which pins the dump's keys. A malformed pointer reads as `None`: a device that can't parse a check-in loses `frozen`.
- `incident.IncidentReport` gets `consult: ConsultRequest | None = None`.
- `incident.Overlay` gets `proposal_id: str | None = None`. **It is provenance only.** A proposal overlay reaches the cloud through `ProposalResult` (`record_result("applied")` creates the row, §6.5), never inside an incident. The device sets `Overlay.proposal_id` on a *later* incident report when the overlay active at that time came from a proposal (for example, the fault recurred under it). The cloud keeps it in `raw`.
- **`ProposalPointer.sha256`** lets the device check the pulled body against the check-in, not only against the same response's `X-Sha256`. The server computes it with `proposal_body_and_sha256` on each request (§6.5).

**Wiring:**
- Export every new model from `__init__.__all__`. Register the four top-level messages (`DoctorAsk`, `ProposalResult`, `DoctorAdvice`, `Proposal`) in `schema.MODELS`; the sub-models appear in their `$defs`, as `Overlay` and `Running` already do.
- Add examples `examples/doctor_ask.json`, `doctor_advice.json`, `proposal.json`, `proposal_result.json` with `MODEL_BY_PREFIX` entries, plus `incident_consult_wanted.json` (an `IncidentReport` with `consult`, a fixture for F12). All five tell one story: the same incident, base `c0de001`.
- Add README rows.

**Leniency (as built in PR 1):** a bad optional field reads as its default instead of failing the payload (`common.fallback`).
- `IncidentReport.consult` accepts `true` as `{"wanted": true}`, and a consult that isn't an object reads as `null`. Inside `ConsultRequest`, a bad field nulls only that field: `{"wanted": true, "trigger": true}` → `wanted=True, trigger=None`. A bad `wanted` reads as `False`, and `attempts` reads anything that isn't a whole number as `null`. A numeric `trigger` reads as a string.
- `CheckInResponse.proposal`: a malformed pointer reads as `None`.
- `DoctorAsk.budget_s` is clamped to [3, 120]; a non-numeric, non-finite or null value reads as 20. `null` text, lists and `context` read as their defaults.
- `ProposalResult`: a numeric `overlay_id` reads as a string (any other non-string reads as `None`), a non-string `reason` reads as `str(...)` (`None` reads as `""`), and a malformed `occurred_at` reads as `None`.
- **The one strict rule:** `applied` with no `overlay_id`, or a blank one, fails, because a lost `applied` means no overlay row and no lock-in.
- `repro_test` is the path or pytest node id of the test inside `code_patch`.

**APIs doc** (`docs/cloud-platform-planning/cloud-platform-apis.md`):
- PR 1 adds the payloads (§2) and the three routes (§3).
- The §5 event row lands in PR 2, because of the labels test.

## 3. Routes

All three are device routes. They take `{device_id}` from the path with no auth, as today. They run `check_contract` and `parse` from `api/device.py`; move those two helpers to `api/deps.py` or import them. Errors are `errors.ApiError(status, code, sentence)`. **The device treats any non-200 as "no answer".**

| Route | Module | Body → response | Errors |
|---|---|---|---|
| `POST /v1/devices/{id}/doctor/ask` | `api/doctor.py` → `services/doctor_ask.ask(...)` | `DoctorAsk` → `200 DoctorAdvice` | `404 unknown_device`; `503 doctor_off` (mode off); `409 fleet_frozen`; `429 doctor_cap` (asks per incident, asks per device per day, or daily spend); `504 doctor_timeout` and `502 doctor_unavailable` (LLM) |
| `GET /v1/devices/{id}/proposals/{pid}` | `api/proposals.py` → `services/proposals.get_for_device` | → `200 Proposal`, with the `X-Sha256` header over the exact body bytes | `404 unknown_proposal` (also when it belongs to another device); `410 proposal_gone` (not `offered`, or expired); `409 fleet_frozen` |
| `POST /v1/devices/{id}/proposals/{pid}/result` | `api/proposals.py` → `services/proposals.record_result` | `ProposalResult` → `204`, or `200` for an identical resend | `404 unknown_proposal`; `409 proposal_result_conflict` (a different result than the one recorded); `422` if `applied` has no `overlay_id` |

**L1 handler rule (FR11):** `/doctor/ask` does **not** use `SessionDep`. It runs in four steps:
1. A short session reads the device, `frozen`, the LLM config, the caps and the spend. A refusal records its event (`doctor.blocked_over_cap`, or `doctor.declined` for an unconfigured LLM; at most one a day per device, incident and reason) and raises. An admitted ask records **`doctor.asked` and commits before the call**, so every ask that reaches the LLM counts toward the caps, even if what follows crashes. The session then closes.
2. The LLM call runs with **no session open**. Its timeout is `min(settings.doctor_ask_timeout_s, body.budget_s - 2)`. The contract has already clamped `budget_s` to [3, 120] (non-numeric, non-finite or null reads as 20), so the timeout is at least 1 s.
3. A second short session records `doctor.advised`, or `doctor.declined` when the call, or reading its reply, failed in any way. It then commits.
4. The handler returns.

Get the sessionmaker from `request.app.state.sessionmaker`. Two asks racing through step 1 can still each pass the cap check and overshoot it by one, but the window is now milliseconds, not the LLM call. That's accepted *(inferred)*.

**Clock:** `cloud/tests/flows/conftest.py`'s `clock` fixture patches `utcnow` in `api.device` and `api.ops`. Add `api.doctor` and `api.proposals` to it (PR 2).

## 4. Tables (`cloud/models.py`, PR 2)

**Both tables are new, so `create_all` makes them on restart and no `ALTER` is needed.** Ids come from `ids.new_id("cons" | "prop")`; add `"cons"`, `"prop"` and `"ask"` to `ids.PREFIXES`.

```python
class DoctorConsult(Base):          # __tablename__ = "doctor_consults"
    id: str  (pk, "cons_…")
    fleet_id, device_id: str, incident_id: str (FK incidents.id, indexed)   # incidents.id is a str
    origin: str                     # "device" | "cloud"
    trigger: str | None
    attempt: int                    # 1-based, per incident
    stage: str                      # admit | gather | diagnose | draft | check | offer | applied | closed
    status: str                     # open | waiting | closed
    outcome: str | None             # see §5.2
    job_key: str | None; job_started_at: datetime | None; retry_at: datetime | None
    case_file: JSONB = {}; ledger: JSONB = []; draft: JSONB = {}; checks: JSONB = {}
    cost_usd: float = 0
    created_at, updated_at, raw: JSONB = {}
    UniqueConstraint(incident_id, attempt)   # a racing tick can't open attempt N twice

class DoctorProposal(Base):         # __tablename__ = "doctor_proposals"
    id: str  (pk, "prop_…")
    fleet_id, device_id: str, consult_id: str (FK), incident_id: str (indexed)
    base: JSONB                     # ProposalBase dump
    code_patch: str; repro_test: str | None; why: str; checks: JSONB
    touches_safety: bool; sha256: str | None
    status: str                     # offered | applied | rejected | stale | failed_verify | expired | held | reverted
    overlay_id: str | None; result_reason: str | None; result_at: datetime | None
    expires_at: datetime; created_at, updated_at, raw: JSONB = {}
```

**Asks get no table.** An ask is its two events (`doctor.asked`, `doctor.advised`), referenced as `refs.ask`.

## 5. Consult state machine (`services/doctor.py`, PR 4)

### 5.1 The tick

```python
def tick(session, now, *, settings=None, jobs=None, llm=None, coder=None) -> dict[str, int]
```

- **Registration:** `worker.STEPS.append(Step("doctor.tick", doctor.tick, every_s=<doctor_tick_s default, 5>))`. PR 2 appends it, with a stub tick that returns `{}`. Importing `worker` never reads `.env`, so `worker.steps_for(settings)` applies settings when the worker starts: it binds `DOCTOR_TICK_S` and leaves `doctor.tick` out entirely while `DOCTOR_MODE=off`. Turning the Doctor on needs a worker restart.
- **Injection:** `jobs`, `llm` and `coder` are injected the same way `workorders.sync` takes `naive=`. Adapters are cached per process.

Each tick does five things, in order:

1. **Off or frozen:** if `doctor_mode == "off"` or the fleet is frozen (`fleet.is_frozen`), return. Consults hold in place and write no event per tick.
2. **Open:** `open_new(session, now)` makes a consult for each incident that has none (attempt 1) and is either:
   - `incidents.raw->'consult'->>'wanted' = 'true'`, giving `origin="device"`; or
   - when `doctor_auto_consult` is on, `resolved = false` and `suspected_cause != 'hardware'`, giving `origin="cloud"`.

   Only incidents with `received_at >= now - doctor_lookback_s` count, so it never backfills history. Each new consult records `doctor.consult_opened`.
3. **Advance:** for each open consult (`with_for_update(skip_locked=True)`, oldest first, at most `doctor_max_open` of them), move it forward by **at most one stage**:

   | Stage | Does | Moves to |
   |---|---|---|
   | `admit` | `domain.doctor.admit(...)`: caps, spend, `running.code_rev` present | `gather`, or closed with `declined` / `blocked_over_cap` / `gave_up` |
   | `gather` | `doctor_evidence.case_file(session, store, consult, incident)` builds a plain dict, all evidence marked untrusted | `diagnose` |
   | `diagnose` | Submits job `diagnose:{id}`, built from the case file and `llm`. When done: ledger saved, `doctor.diagnosed` recorded with `cost_usd` | `draft` |
   | `draft` | `doctor_draft` decides: `none` closes with `advised`; `fake` uses `domain.doctor.fake_draft(case)` (a labeled stub patch); `coder` submits job `draft:{id}`. Records `doctor.drafted` | `check` |
   | `check` | `touches_safety`, or `safety_paths_confirmed` false, closes with `discarded` (`doctor.proposal_discarded`, reason `touches_safety` / `safety_paths_unknown`). Then by `doctor_check`: `stub` records `{"stub": true}`; `sandbox` submits job `check:{id}`. Records `doctor.checked` | `offer`, or closed with `failed_checks` |
   | `offer` | A stub check with `doctor_offer_stub_checked` false closes with `advised`. Otherwise `proposals.offer(...)` makes the proposal and records `doctor.proposal_offered` | `applied` (waiting on the device) |
   | `applied` | Reads the proposal and overlay status: `held` closes with `held`, `reverted` or `failed_verify` closes for a retry, and `expired`/`stale`/`rejected` close as they are | closed |

4. **Expire:** `proposals.expire_due(session, now)` marks `offered` proposals whose `expires_at` has passed as `expired`, and records `doctor.proposal_expired`.
5. **Retry:** an incident whose last consult closed with `failed_checks`, `reverted` or `failed_verify` opens attempt N+1 while the count is under `doctor_consults_per_incident`. Otherwise it records `doctor.gave_up`.

**Jobs:** a job that is `missing` (the worker restarted) is resubmitted. After 3 failed tries, the consult closes with `gave_up` and the reason `"job failed"`.

**Closing:** closing a consult records `doctor.consult_closed` with `data.outcome`.

### 5.2 Outcomes

| Outcome | When | Retried? |
|---|---|---|
| `declined` | Not wired, no `code_rev`, or the fleet doesn't want it | no |
| `blocked_over_cap` | The daily spend cap was hit at admit | no |
| `advised` | Ended with the ledger only: draft `none`, or a stub check that isn't offered | no |
| `discarded` | The patch touches safety, or the safety paths are unknown | no |
| `failed_checks` | The repro test didn't fail before, or didn't pass after, or the device tests failed | yes |
| `held` | The device held the overlay | — (lock-in takes over) |
| `reverted` / `failed_verify` | Device reported | yes |
| `stale` / `rejected` / `expired` | Device moved on, wasn't parked, or never pulled | no |
| `gave_up` | Out of consults, or a job failed 3× | no |

### 5.3 As built (PR 4)

These refine §5.1–§5.2 and §8; where they differ, this section wins.

- **`doctor_max_open` bounds working consults** (stages `admit`…`offer`). Consults in `applied` only wait on the device, so every tick syncs all of them (cheap reads, `SKIP LOCKED`) outside that limit. Otherwise five silent devices would starve every new consult.
- **No event for `admit → gather` or `gather → diagnose`.** §8 has no kind for them, and adding one would need a `story.LABELS` entry. `doctor.consult_opened` comes before them and `doctor.diagnosed` after. Every other transition writes its event in the same transaction.
- **Admit counts what the call may cost.** It passes `estimate_usd = domain.doctor.diagnose_estimate_usd(DOCTOR_DIAGNOSE_MAX_TOKENS, price)` to `check_spend`. That is the largest prompt `diagnose_prompt` builds plus the full completion budget. A bad `DOCTOR_USD_PER_1K_TOKENS` closes the consult `blocked_over_cap` (`which: spend`) before anything is spent. `doctor.blocked_over_cap` adds `estimate_usd` and `stage`.
- **Spend is checked again before every job submit,** the first included. A cap reached between runs closes the consult `blocked_over_cap` with `stage: diagnose` instead of submitting the job.
- **Failed and lost runs cost money too.** Each one records `doctor.declined` with `stage: diagnose`, `reason: "job failed: <ExcType>"`, `run`, and `cost_usd` at `len // 4` prompt tokens with no completion (`usage_estimated: true`). It also adds that cost to `consult.cost_usd`. `doctor.declined` moves no badge in the story.
- **Job tries count submissions.** A `Missing` or `Failed` job is resubmitted until it has run 3 times; the next `Missing` or `Failed` closes the consult `gave_up` (reason `job failed: …`). The tries live in `doctor_consults.raw["job_tries"]`. The key is forgotten in an `after_commit` hook on the step's session, so a rolled-back tick re-applies the result without paying again.
- **A stub check carries only a stub draft.** `offer` needs `checks.stub` → `DOCTOR_OFFER_STUB_CHECKED` **and** `draft.stub`. A real patch (a future coder draft) with only a stub check closes `advised` and is never offered. `proposals.offer` raises on it as well.
- **`applied` sync** (`domain.doctor.applied_outcome`): the overlay counts only if its `raw.proposal_id` is this proposal's id; any other row (a reused or wrong id) is ignored. The proposal's final statuses map to themselves. An `applied` proposal is decided by its overlay: `held`, or any cloud status that only follows held (`locked_in`, `dropped`, `lockin_*`), gives `held`; `reverted` gives `reverted`; `superseded` (a version replaced it before it held) gives `stale`. The tick moves the proposal to `held` or `reverted` in the same transaction as `doctor.consult_closed`.
- **Retry or give up** (`domain.doctor.after_close`): after a close with `failed_checks`, `reverted` or `failed_verify`, the incident gets attempt N+1 while its consult count is under the cap. Otherwise `doctor.gave_up` is recorded on that last consult, which keeps its own outcome. `raw["next"]` (`retry` or `gave_up`) makes this happen once. Only a job failure closes a consult with the outcome `gave_up`.
- **Defense in depth on safety:**
  - `check` discards, and `offer` re-runs the same check before calling `proposals.offer`.
  - `proposals.offer` raises on a safety-touching patch, on unconfirmed paths, or on a real patch with a stub check. It stores the computed `touches_safety` and `raw.draft_stub`.
  - The serve-time gate (§6.5) re-checks everything on each pointer and pull.
- **`consult` shorthand:** `open_new` reads `incidents.raw.consult` with the contract's own leniency (`IncidentReport`), so `"consult": true` counts as wanted, not only `{"wanted": true}`.
- **Extra event data:** `doctor.consult_opened` adds `retry_of` on retries; `doctor.consult_closed` adds `attempt`; `doctor.declined` adds `which`; `doctor.diagnosed` adds `removed_for_safety`, `usage_estimated` and `latency_ms`; `doctor.proposal_discarded` adds `paths`; `doctor.proposal_result` adds `overlay_id` and `late`. `overlay.applied` and `doctor.proposal_result` both carry refs `consult` and `proposal`.
- **`doctor_consults.raw` keys:** `proposal_id`, `job_tries`, `job_error`, `retry_of`, `next`; PR 7 adds `retry_at` and `coder_blips`.

### 5.4 As built (PR 7): real draft, real check, traces

Both stay behind settings whose defaults don't change behavior (`DOCTOR_DRAFT=fake`, `DOCTOR_CHECK=stub`). Where this differs from §5.1–§5.3, this section wins.

- **Draft (`DOCTOR_DRAFT=coder`).** Job `draft:{id}` calls `coder.draft(patch="", incident=<case summary>, code_rev=<base>, files=<package minus verify/>, task=domain.doctor.draft_task(case, ledger, safety_paths=…, test_cmd=CODER_TEST_CMD))`.
  - The task asks for a reproduction test under `tests/` (relative to `CODE_ROOT`) that fails on the base, then the minimal fix, and never touches the safety layer, the visor, existing tests or test config. The case file and the hypotheses go inside the untrusted evidence block; only settings are trusted text.
  - From the `CodeDraft`: `consult.draft = {code_patch, repro_test, why, stub: False, coder_passed, flags, notes, coder_tests}`. `repro_test` is `domain.doctor.find_repro_test(diff)`: the first test file under `tests/` the patch **adds as a new file** (no pre-image). A modified, renamed, copied or deleted file never counts. A patch the contract refuses (mixed `device/` forms, `./device/`) has none.
  - `doctor.drafted` is always recorded once the coder ran, with `cost_usd`: the coder's own counts (its notes' `tokens N in / M out`, else `CodeDraft.tokens`), else `DOCTOR_DRAFT_ESTIMATE_USD` with `usage_estimated: true`. Extra data: `repro_test`, `coder_passed`, `flags`, `prompt_tokens`, `completion_tokens`, `task_chars`, `base_code_rev`.
  - Closes `failed_checks` (retried within the cap), in this order, when:
    - the diff is empty;
    - `coder_runs.patch_refusal` refuses it (format problems, or it touches `verify/`);
    - `domain.doctor.screen_fix` refuses it ("the fix is refused before any run: …", below);
    - the coder's own test run failed (a permanent failure);
    - there is **no repro test**.

    Otherwise → `check`.
  - **`screen_fix`** decides from the patch's own paths (`patch_changes`, read by the contract's parser, never from a git status that a `.gitignore` can hide). It refuses:
    - **tampering:** any change to a test file that exists before the patch, whether edited, deleted or renamed away. A test file is anything under a `tests/` directory, or a `test_*` / `*_test.py` file;
    - **a planted test runner:** an added or changed `pytest.py`, `pytest/` or `_pytest/` directory, `sitecustomize.py`, `usercustomize.py` or `*.pth`, at any depth, in any case;
    - **test config:** an added, changed or deleted `conftest.py`, `pytest.ini`, `.pytest.ini`, `pyproject.toml`, `setup.cfg` or `tox.ini`, reported as `test_harness: <path> (<status>)`;
    - **no device code change** at all;
    - **`flag_only_fix`:** the non-test hunks only add lines, and every added line is blank, a comment, or a module-level assignment or constant (`DOCTOR_FIXED = True`).

    The check stage runs `screen_fix` again before any sandbox run.
  - **No real coder, no burn.** With `CODER_PROVIDER=fake` (the pass-through `FakeCoder`) or a coder its settings can't build (`doctor_code.Unavailable`), the consult closes `advised` with the reason before any submit: no `coder.started`, no spend, no retry.
  - **Provider blips back off (#38's `domain/coder_retry.py`).** A failed job or a failing draft is classified with `coder_retry.classify`. Transient means a rate limit, 429, "at capacity", 5xx, timeout or connection error, no sandbox result, or zero tokens or steps. A transient failure is not a failed job try and doesn't close the consult. It waits in `draft` until `raw.retry_at` (`coder_retry.retry_at`: 2 min, doubling, capped at 30 min), and `raw.coder_blips` lists each reason.
    - Each blip records `doctor.declined` with `reason: coder_unavailable`, `which: coder_unavailable`, `job`, `attempt`, `label`, `no_tokens`, `retry_at`, and `cost_usd` ($0 for a known 0 tokens, else the tokens, else the failed-run prompt estimate).
    - The 5th in a row gives up (`gave_up`, listing every attempt).
    - A permanent failure keeps the old rules: an exception counts as a job try (3, then `gave_up`), and a bad draft closes `failed_checks`.
  - Spend is checked before each submit with `DOCTOR_DRAFT_ESTIMATE_USD`, like diagnose.
  - Without a registered package for the base rev, the consult closes `advised`.
  - With `CODER_PROVIDER=local_agent` and no `docker` CLI in the worker (`code_lockin.coder_blocker`), the consult waits in `draft` and the worker logs why once; an injected coder (tests) is never blocked.
- **The coder budget is shared with the batch.** `CODER_MAX_DRAFTS_PER_HOUR` counts `coder.started` events fleet-wide with `ts > now - 1h`. From that it subtracts job attempts known to have spent exactly 0 tokens, once per job attempt (`reason: coder_unavailable, no_tokens: true`), the refund rule of `batch._drafts_this_hour` (#38).
  - **Copied, not imported:** `doctor_code.coder_budget` runs the same query, because `batch.py` belongs to another PR. It also refunds the Doctor's own zero-token blips (`doctor.declined`). The batch's query reads only `overlay.deferred` and `batch.error`, so it doesn't refund the Doctor's blips yet: stricter, never looser. Adding `doctor.declined` to its kinds is a one-line follow-up for the batch's owner.
  - **Counted both ways:** every Doctor submit records `coder.started` (actor `fleet-doctor`, refs `device`, `incident`, `consult`; `data.source = "doctor"`, `job`, `attempt`, `base_code_rev`), so the batch's count sees the Doctor's drafts and the Doctor's sees the batch's.
  - **Over budget, the consult waits; it doesn't fail.** It stays in `draft` (status `waiting`) with `raw.retry_at`, the time enough of the oldest starts leave the window (an hour from now for a budget of 0). It records `doctor.declined` with `which: coder_budget`, `stage: draft`, `drafts_started`, `budget`, `retry_at` and `cost_usd: 0`. Before `retry_at` the tick writes nothing; after it, the budget is read again. No job try is counted while held.
- **Waiting consults take no slot.** `_working` fills the `DOCTOR_MAX_OPEN` slots only with consults that can act this tick. It leaves out consults with `raw.retry_at > now` (the coder budget, a coder backoff), `draft` consults while the coder is blocked, and `check` consults while the sandbox is blocked (no `docker`; logged once). So a waiting consult never starves a newer one.
  - `raw.retry_at` is a fixed-width UTC ISO stamp (`doctor.stamp`), so SQL compares it as text and never casts.
- **Check (`DOCTOR_CHECK=sandbox`).** The safety discard runs first, as before.
  - **Screened again, from the patch alone:** `screen_fix` (above) refuses before any sandbox run and records `doctor.checked` with the reasons.
  - **Then job `check:{id}`** (`doctor_code.check_job`) runs up to four `coder_runs.replay_code_patch` replays in the coder image with no network, each stopping the check as soon as it fails.
    - The package is passed **minus `verify/`**.
    - Every run gets `PYTHONSAFEPATH=1` (`replay_code_patch(extra_env=…)`), so Python never puts the work dir first on `sys.path` and a planted `pytest.py` can't shadow pytest. The commands themselves stay as configured.

    1. **Fails before:** `domain.doctor.split_patch(patch, repro_test)` (only the repro file's sections) with the fixed `REPRO_CMD = python -I -m pytest -q -rA <repro file>`, never derived from `CODER_TEST_CMD`.
       - It must exit **1** (not 0, 2 collection error, 4 no such file, or 5 no tests), with at least one `FAILED` test id in the repro file.
       - It must fail on behaviour: a failure whose reason is only `ImportError`, `ModuleNotFoundError` or `AttributeError: module …` doesn't count, because a test that merely imports a name the "fix" adds proves nothing.
       - Results come from `domain.doctor.pytest_results`. It reads only the block after the **last** `short test summary info` header, up to its first non-summary line, so `FAILED x` lines a test prints, or prints after the counts, are ignored.
    2. **Passes after:** the full patch with the same `REPRO_CMD`: exit 0, and every one of those test ids listed `PASSED`.
    3. **Device tests:** `CODER_TEST_CMD`, exactly as configured (a compound command like `pytest -q; ruff check .` is never appended to), on the base plus the patch's **non-test hunks only** (`domain.doctor.code_only`: every test-file section is stripped, the repro included, since it already proved itself in steps 1–2). So a repro that monkeypatches the code under test at import can't mask a regression. It must pass, with no `test_harness:`, `verify_scripts:` or `existing_test:` flag.
    4. **Held-out verify** (architecture §6 item 7: once, at the end, pass/fail only). When the base package has `verify/` scripts (`domain.doctor.held_out_files`; #38's `make device-verify` writes them), one more replay runs them on the same code-only patch (`verify_files=…`, no test command).
       - If they fail, the consult closes `failed_checks` with the reason exactly "held-out verify failed".
       - Only the state and the counts (`tests`, `passed_count`, `skipped`, `timed_out`) are kept. The scripts' output never reaches the ledger, a prompt, the trace or event data.
       - With no scripts: `checks.verify = "none available"`.
       - `CODER_VERIFY_DIR` isn't read here.

  `domain.doctor.check_verdict` decides. `consult.checks = {stub: False, fails_before, passes_after, device_tests, repro_test, repro_ids, verify, summary}`, which is what the proposal carries; `verify` is `passed`, `failed`, `none available` or `not run`. `doctor.checked` adds `reason`, `repro_test`, `repro_ids`, `flags`, `before` / `after_repro` / `after` (exit code, test counts, failed ids with reasons, passed ids, a 2,000-character output tail, all redacted), `verify` and `verify_counts`. A pass goes to `offer`; it needs no `DOCTOR_OFFER_STUB_CHECKED`, since the check is real. Anything else closes `failed_checks`.
  - The check calls no model, so it skips the spend check, and a failed or lost run records `doctor.declined` at $0.
  - No repro test inside the patch closes `failed_checks`. An empty `CODER_TEST_CMD` closes `advised`. A worker without `docker` waits (logged once); an injected sandbox is never blocked.
  - **Hidden checks stay hidden.** The `verify/` scripts are in no replay but the last, and nothing from that one beyond pass/fail and counts is kept.
- **Paths are read the contract's way** (#38, B3). `domain.doctor.patch_path` asks `summarize_code_patch` about a one-file header, so there is no private copy of the rules. Paths are relative to `device/`, with or without the prefix, and `./device/`, `..` and absolute paths are refused. `package_rel`, `split_patch`, `find_repro_test`, `tampered_tests` and the `verify/` holdout all use it, and a patch the contract finds any problem in (mixed forms included) yields nothing.
- **Every close writes a trace.** `_close` calls `doctor_trace.write` before `doctor.consult_closed` (§9), and the event carries `refs.recording` and `data.trace_recording`. If the blob store can't take the bytes, the close still happens, with no recording.
- **Jobs:** `services/doctor_jobs.py` is now a thin adapter over `services/jobs.py` (§6.2).

## 6. Seams

### 6.1 `domain/doctor.py` (pure, PR 2)

Imports only stdlib and `romulus_contracts`, and takes `now` as an argument.

```python
@dataclass(frozen=True) class Spend: spent_today_usd: float; cap_usd: float
@dataclass(frozen=True) class Decision: ok: bool; outcome: str | None; why: str; which: str | None = None
    # outcome: declined | blocked_over_cap | gave_up, or None when ok or held (mode off, frozen: close nothing, retry later)
    # which: off | frozen | no_code_rev | spend | asks | consults (the rule that refused; the
    #   `which` of doctor.blocked_over_cap). The ask route maps off → 503, frozen → 409, the rest → 429
def check_spend(spend: Spend, *, estimate_usd: float = 0.0) -> Decision            # fails closed on NaN/negative, like triage.check_caps
def admit(*, frozen: bool, code_rev: str | None, consults_for_incident: int, consult_cap: int,
          spend: Spend, mode: str) -> Decision
def admit_ask(*, frozen: bool, asks_for_incident: int | None, ask_cap: int,
              asks_for_device_today: int | None, device_day_cap: int, spend: Spend, mode: str) -> Decision
    # Counts are of consults/asks *before* this one, so a count equal to its cap refuses.
    # admit_ask: a count of None means its cap doesn't apply and isn't checked at all. An incident
    #   ask passes asks_for_device_today=None (so DOCTOR_ASKS_PER_DEVICE_DAY=0 never blocks it); an
    #   ask with no incident passes asks_for_incident=None. A cap that applies fails closed when bad.
def cost_usd(prompt_tokens: int, completion_tokens: int, usd_per_1k_tokens: float) -> float
    # raises ValueError on a bad price or token count, so a bad price never counts as free
def parse_hypotheses(text: str, *, max_items: int = 5, max_chars: int = 1200) -> tuple[list[dict], bool]
    # → ([{claim, how_to_check}], parsed_ok). Takes the first non-empty hypotheses in a JSON
    #   object/array; an empty answer counts only as an explicit {"hypotheses": []} (a bare [] in
    #   prose never wins over a later real answer). With none, one hypothesis from the prose, with
    #   parsed_ok=False. Trims to the char budget (≈300 tokens).
def filter_safety(hyps: list[dict], safety_paths: Iterable[str]) -> tuple[list[dict], int]
    # drops any naming a safety path: case-insensitive, on [a-z0-9] boundaries ("SAFETY",
    #   "visor/limits.py", "safety_limits.py" match; "supervisor", "advisory" don't). A leading
    #   device/ on an entry is ignored; an empty, "." or "device" entry drops everything.
def ask_prompt(ask: dict) -> tuple[str, str]                                 # (system, user); evidence wrapped as untrusted data
def diagnose_prompt(case: dict) -> tuple[str, str]                          # the whole case file, notes included, inside the block
def fake_draft(case: dict) -> dict  # {code_patch, repro_test, why, stub: True}: adds doctor/FAKE_FIX.md, valid per summarize_code_patch
def day_start(now: datetime, tz: str) -> datetime                           # same calendar day as workorders._day
# PR 4:
def applied_outcome(proposal_status: str, overlay_status: str | None) -> str | None   # how a waiting consult ends (§5.3)
def after_close(outcome: str | None, consults_used: int, consult_cap: int) -> str | None  # "retry" | "gave_up" | None
def estimate_tokens(chars: int) -> int                                      # len // 4, never 0
def diagnose_estimate_usd(max_completion_tokens: int, usd_per_1k_tokens: float) -> float  # largest prompt + budget
def failed_run_cost_usd(prompt_chars: int, usd_per_1k_tokens: float) -> tuple[int, float]  # a failed run's prompt
# admit(..., estimate_usd: float = 0.0) passes the estimate to check_spend
# PR 7:
def patch_path(path: str) -> str | None                  # the contract's reading of one code_patch path (summarize_code_patch); None if refused
def split_patch(patch: str, path: str) -> str            # the file sections touching `path`, or "" (also for a patch the contract refuses)
def find_repro_test(patch: str) -> str | None             # the first NEW file (no pre-image) under tests/ the patch adds
def tampered_tests(patch: str) -> list[str]               # any change to an existing test file: edited, deleted, renamed away
def screen_fix(patch: str) -> list[str]                   # tampering, a planted runner, test config, no code change, flag_only_fix
def patch_changes(patch: str) -> list[tuple[str, str]]    # (A/M/D, path) from the patch itself, not git status
def code_only(patch: str) -> str                          # the patch without any test-file section
def flag_only_fix(patch: str) -> bool                     # code hunks only add module-level assignments or constants
def held_out_files(files) -> dict[str, bytes]             # the package's verify/ scripts, for the final pass/fail run
def is_test_path(path: str) -> bool                       # tests/**/test_*.py or tests/**/*_test.py
def draft_task(case: dict, ledger, *, safety_paths=(), test_cmd="python -m pytest -q") -> str  # repro first, then the fix; evidence fenced
def pytest_results(output: str) -> dict                   # -rA short summary: {passed: [id], failed: {id: reason}, errors: [id]}
def repro_failures(run, repro_test) -> tuple[list[str], str]  # ids in the repro file that FAILED on behaviour with exit 1, or why not
def repro_passes(run, ids) -> str                         # "" when exit 0 and every id is PASSED, else why not
def check_verdict(before, after_repro, after, repro_test, screened=(), verify=None) -> dict  # {passed, fails_before, passes_after, device_tests, verify, reason, summary, flags, repro_ids}
def package_rel(files: Mapping[str, bytes]) -> dict[str, bytes]   # relative to CODE_ROOT (patch_path), verify/ dropped
def withhold_verify(lines) -> tuple[list, int]            # entries naming a verify/ path → VERIFY_WITHHELD; + withhold_verify_text
def named_paths(texts, paths, *, limit=5) -> list[str]    # package files the logs/trace name, first mention first
def code_excerpts(files, paths, *, file_bytes, total_bytes) -> list[dict]
def tail_lines(data: bytes, *, max_lines, max_bytes, partial=False) -> list[str]
def coder_usage(notes: str | None) -> tuple[int, int] | None      # "tokens N in / M out" from CodeDraft.notes
```

**Prompt rule:** device text (logs, traces, files, the question) goes inside a fenced `<evidence untrusted="true">` block. The system prompt says to treat it as data and answer only in the JSON shape (§6 item 6 of the architecture doc).
- The block is JSON with `</` and backticks escaped, so it can't close the block or the fence. Strings, dict keys and lists are capped.
- The whole body stays under `MAX_EVIDENCE_CHARS` (24,000). Over it, the caps tighten step by step, then the text is cut as a last resort, and a trusted note after the block says the evidence was shortened.

### 6.2 `services/doctor_jobs.py` (PR 2): a stand-in for M8b's shared `services/jobs.py`

M8b is building one generic off-tick runner, `services/jobs.py`, in this shape (agreed with the M8b session). Until it merges, `doctor_jobs.py` is a thin local stand-in with the **same shape**. When M8b lands, `doctor_jobs.py` becomes `from romulus_cloud.services.jobs import *`, a one-line swap.

```python
@dataclass(frozen=True) class Pending: ...
@dataclass(frozen=True) class Done:    result: Any
@dataclass(frozen=True) class Failed:  error: str
@dataclass(frozen=True) class Missing: ...                 # unknown key, e.g. after a worker restart → resubmit
class Jobs(Protocol):
    def submit(self, key: str, fn: Callable[[], T]) -> None: ...   # idempotent: a pending or done key is a no-op; a failed key reruns
    def poll(self, key: str) -> Pending | Done | Failed | Missing: ...
    def forget(self, key: str) -> None: ...                         # drop the key (result, or a running job's future) → Missing
class ThreadPoolJobs:   # ThreadPoolExecutor(max_workers=settings.doctor_job_workers), inside the worker process
                        # + shutdown(wait=True) for tests
class InlineJobs:       # runs fn at submit; for tests (`.calls` lists the keys actually run)
# Results never pile up: every submit and poll moves all finished futures out of the running set,
# and finished results are kept only until MAX_FINISHED (256) newer ones push them out (then
# Missing). The tick calls forget(key) once it has applied a result and committed.
def default_jobs(settings) -> Jobs                         # process-wide singleton
```

- A job function gets plain inputs (the case-file dict and an adapter) and returns plain data. **It never opens a DB session.** The tick submits, polls, and applies results in its own transaction.
- Diagnose and draft both use it. `Coder.draft(..., task=...)` (a from-scratch prompt) comes from M8b; the Doctor PRs don't add `task` themselves.
- **As built (PR 7): the swap is done.** `doctor_jobs.py` re-exports `Pending`, `Done`, `Failed`, `Missing`, `Jobs`, `JobState` (= `Status`), `InlineJobs` and `ThreadPoolJobs` from `services/jobs.py`, and `default_jobs(settings)` returns `jobs.default_jobs(max(1, DOCTOR_JOB_WORKERS), "doctor")`. That is the Doctor's own named pool, so its drafts and checks never queue behind the batch's `"coder.draft"` or the gate's `"coder.replay"`, and the reverse. Tests count submitted keys with `InlineJobs.submitted` (the stand-in's `.calls` is gone). Pruning works as the shared module does it (TTLs, not `MAX_FINISHED`). Keys: `diagnose:{id}`, `draft:{id}`, `check:{id}`.

### 6.3 `adapters/llm.py` (PR 2)

```python
@dataclass(frozen=True) class LlmReply: text: str; model: str; prompt_tokens: int; completion_tokens: int
                                        usage_estimated: bool = False   # True: counts are len//4 estimates; put it in event data
class Llm(Protocol):  # existing explain / write_skill, plus:
    def complete(self, system: str, user: str, *, max_tokens: int | None = None, timeout_s: float | None = None) -> LlmReply: ...
```

- **`OpenAICompatLlm.complete`:** one chat call, with per-call `timeout` and `max_tokens` (`client.with_options(timeout=…)`). Usage comes from `resp.usage`. A count it leaves out is estimated as `len // 4` of the prompt or the reply (never 0, so the cost can't read as $0 and the cap can't fail open), with `usage_estimated=True`. Errors raise `LlmError`.
- **`FakeLlm.complete`:** deterministic `FAKE_BANNER` plus prose that echoes the first 80 characters of `user`. Tokens are `len // 4` (`usage_estimated=True`), so caps can be tested.
- **`LlmTimeout(LlmError)`** is raised when the call runs out of time, so the ask route can answer `504 doctor_timeout` rather than `502 doctor_unavailable`. A call with `timeout_s` set isn't retried (it is a hard budget).
- `explain` and `write_skill` stay as they are.

### 6.4 `services/doctor_evidence.py`

```python
def case_file(session, store, consult, incident, *, settings) -> dict
# {"incident": {kind, summary, tier, signals, outcome, running}, "log_tail": [...], "trace_tail": [...],
#  "code": {"rev", "files": [{path, excerpt}]}, "tried": [...], "untrusted": True, "notes": [...]}
```

- **v0 (PR 4):** incident fields plus `log_tail` (from `Incident.raw["log_tail"]`), with a note: "trace, code, mcap: not wired". As built, `incident` also carries `id`, `agent_reasoning` and the raw `consult`, and `tried` lists the healer's actions (`{"by": "healer", "action"}`) and earlier consults on the incident (`{"by": "fleet-doctor", "attempt", "outcome", "proposal_id"}`). The tick passes a `BlobStore` for PR 7; v0 doesn't read it.
- **Full (PR 7):**
  - the trace tail from `agent_trace` recordings (`Recording.kind == "agent_trace"`, read through `BlobStore.get`), last N lines;
  - code at the base rev (`registry.get_code` → `open_package`), the files named in the log or trace, capped by bytes.
- **MCAP stays a labeled "not wired"**, because `mcap` isn't a dependency.
- **As built (PR 7):**
  - **Shape:** `{"incident", "log_tail", "trace_tail": [str], "trace_recordings": [id], "code": {"rev", "files": [{path, excerpt, size_bytes, truncated}], "tree": [path]}, "tried", "untrusted": True, "notes"}`. `notes` says what's missing, e.g. `trace: no agent_trace recording stored for this incident`, `code: code X is not registered (POST /v1/code)`, `code: the log and trace name no file in the package`, and always ends with `mcap: not wired`.
  - **Trace tail:** reads the newest 3 `agent_trace` recordings on the incident whose bytes are stored (a `blobs` row), oldest first. Each is read from its end (seek to `size - DOCTOR_TRACE_TAIL_BYTES`, the partial first line dropped; a store that can't seek is read whole). Then the last `DOCTOR_TRACE_TAIL_LINES` (40) non-blank lines, at most `DOCTOR_TRACE_TAIL_BYTES` (16,000), go over all of them together. A blob that vanished is a note, not an error.
  - **Code:** `doctor_code.package` reads the base package and keys it relative to `CODE_ROOT` with `domain.doctor.package_rel`, which tolerates a `device/` prefix or none and **drops `verify/`** (case-insensitive) along with absolute and `..` names. `domain.doctor.named_paths` picks the files that the log tail, trace tail, healer actions, reasoning or summary name, in order of first mention, at most 5. A token names a file if it is the path, ends in `/<path>` (`/work/x.py`, `device/x.py`, JSON-escaped or Windows separators included), or is a bare file name. `code_excerpts` takes each one's first `DOCTOR_EVIDENCE_FILE_BYTES` (4,000), `DOCTOR_EVIDENCE_CODE_BYTES` (16,000) in all, and skips binaries. `tree` lists the first 40 paths.
  - **Fencing:** everything stays inside the untrusted block of `diagnose_prompt` and `draft_task`, notes included.
  - **Hidden checks:** no `verify/` file content, and no `verify/` path from the package (`tree`, `files`), ever enters the case file.
    - Device text that names a `verify/` path is withheld before it reaches a prompt, the stored case file or the consult's trace (`domain.doctor.withhold_verify`): any log-tail line, trace-tail line, healer action, or line of the healer's reasoning or summary that matches `verify/` (case-insensitive, after a non-word character or at the start; e.g. `verify/x.py`, `device/verify/x.py`, `/work/verify/x.py`) is replaced whole by `[line withheld: it names a held-out verify/ path]`.
    - A note says how many were withheld. The bare word ("verify d1_hand_trace: FAIL") isn't a path and is kept.
    - The coder's `incident` summary comes from the redacted case file only.

### 6.5 `services/proposals.py` (PR 2 stub, PR 4)

```python
def pointer_for(session, device, now, *, settings) -> ProposalPointer | None   # check_in; None if frozen or gated
def offer(session, consult, draft: dict, checks: dict, now, *, settings) -> DoctorProposal
def get_for_device(session, device_id, proposal_id, now, *, settings) -> tuple[bytes, str]   # (body, sha256)
def serve_refusal(p, now, settings) -> str | None   # PR 4: the serve-time gate (below)
def record_result(session, device_id, proposal_id, body: ProposalResult, raw: dict, now, *, safety_paths) -> bool  # True if new
def expire_due(session, now) -> int
```

**Hashes are computed per request, never stored.** `pointer_for` (which fills `ProposalPointer.sha256`) and `get_for_device` (which returns the body and `X-Sha256`) each call `contracts.proposal_body_and_sha256` on the same stored proposal for every request. The dump is canonical, so both give the same bytes and the same hash. Never serve a stored hash next to a re-dumped body.

**`record_result("applied")` creates the overlay row itself** (architecture §5.4). It sets:
- `(device_id, overlay_id)`;
- `incident_id` = the proposal's incident (so `batch.held_overlays` sees it);
- the `base_*` fields from `proposal.base`;
- `code_patch`, `touches_safety`, `status="active"`;
- `raw={"proposal_id", "source": "doctor"}`.

If an `OverlayStatus` arrived first, `incidents.set_overlay_status` will have made a placeholder with `incident_id=None`. Fill that placeholder in and keep its status, the way `_apply_overlay` does (`incidents.py:191-199`). Record `overlay.applied` (actor `device:<id>`, class `agent`) and `doctor.proposal_result`.

**As built (PR 4):**
- **Serve-time safety gate (`serve_refusal`).** The check-in route and the GET route take `SettingsDep`, and `devices.check_in(..., *, settings=None)` passes it on. On **every** request a proposal is served only if all of these hold under that request's settings:
  - it is `offered` and not past `expires_at`;
  - `doctor_mode != "off"` (no settings counts as off);
  - `safety_paths_confirmed` is true;
  - `touches_safety(code_patch, safety_paths)` is false, recomputed now (OR'ed with the stored flag);
  - the check isn't a stub, unless `doctor_offer_stub_checked` is on and the draft was a stub (`raw.draft_stub`).

  Otherwise the pointer is null and the GET answers `410 proposal_gone`. Nothing is written, so turning a setting back on serves the proposal again. `record_result` is **not** gated: a device can always report what it did.
- **The result is recorded once,** in `doctor_proposals.raw["result"]` (`result`, `overlay_id`, the verbatim body). A resend is the same `result` and, for `applied`, the same `overlay_id`; reason text and `occurred_at` may differ between outbox attempts. It answers 200 and writes nothing. Any other result answers `409 proposal_result_conflict`.
- **A late result on an `expired` proposal is stored,** if none was recorded. The device can only apply a proposal it pulled before expiry, so only the report was late (the outbox). Refusing it would lose the row of an overlay the device runs. The event's why says it was late (`data.late`). The consult stays closed `expired`. `late` is `status == expired or expires_at <= now`, so a report that arrives before the tick expires the proposal counts as late too.
- **`doctor.proposal_result` is recorded before `overlay.applied`;** both carry the device's `occurred_at`.
- **A reused overlay id is refused.** On `applied`, the overlay id may already exist for this device with an `incident_id` and a `raw.proposal_id` that is different or missing. That is `409 proposal_result_conflict`, returned before anything is written: the cloud never takes over another incident's overlay. A placeholder (`incident_id` null) is filled as usual.
- `touches_safety` on the overlay is the proposal's flag OR'ed with `touches_safety(code_patch, SAFETY_PATHS)` at result time. `diff` is `{}`.
- `incidents.py` is unchanged. The placeholder fill is a few lines duplicated in `proposals._apply_overlay`.

### 6.6 `services/doctor_ask.py` (PR 3)

```python
def ask(sessions: sessionmaker, device_id: str, body: DoctorAsk, raw: dict, now, *, settings, llm: Llm | None) -> DoctorAdvice   # raises ApiError per §3
```

As built (PR 3):
- **The LLM** comes from the FastAPI dependency `api/doctor.get_doctor_llm(settings)`: `build_doctor_llm`, cached per process for each set of LLM settings. It is `None` while `DOCTOR_MODE=off`. A live LLM whose settings `build_doctor_llm` refuses comes back as `doctor_ask.Unconfigured(why)` and isn't cached. Tests swap it with `app.dependency_overrides[get_doctor_llm]`.
- **Order of checks:** `404 unknown_device`, `503 doctor_off`, `409 fleet_frozen`, then `502 doctor_unavailable` for an unconfigured LLM (`doctor.declined`, reason `llm unconfigured`), then the caps (`admit_ask`), then the price. A `DOCTOR_USD_PER_1K_TOKENS` that `cost_usd` rejects refuses as `429 doctor_cap` (`which="spend"`) before any call, so a bad price never spends.
- **What counts toward a cap:** only asks that reached the LLM (`doctor.asked`, written before the call). Refused asks don't count.
  - **Incident cap:** counts `refs @> {"incident": id}` in this fleet, reported or not.
  - **Device-day cap:** counts this device's `doctor.asked` events since `day_start(now, FLEET_TIMEZONE)` with `data.counts_toward_device_day = true`. An ask is exempt (`false`) only when its `incident_id` is an incident **this device already reported**, so made-up incident ids can't dodge both caps.
- **Refusal events are deduped:** `doctor.blocked_over_cap` (keyed on `which`) and the unconfigured `doctor.declined` (keyed on `reason`) are written at most once per device, incident (or none) and key per `day_start`. Later refusals still answer, but write nothing.
- **Any failure is a decline, never a 500.** Any exception from `llm.complete` or from reading the reply (for example a negative token count in `cost_usd`) is handled like this:
  - It records `doctor.declined` with reason `llm error` and answers `502`. `LlmTimeout` gives reason `llm timeout` and `504` instead.
  - The event records only `error_type`; the error's message goes to the log.
  - **A failed call may still be billed,** so `doctor.declined` carries an estimated `cost_usd`: the system and user prompt at `len // 4` tokens, `completion_tokens: 0`, `usage_estimated: true`. The spend cap counts it.
- **The service owns its two sessions** and commits each one, the FR11 exception to "services never commit".
- **Events** are stamped `ts=now` (the request's time). Beyond §8:
  - `doctor.asked` also carries `budget_s`, `timeout_s`, `contract_version` and `counts_toward_device_day`.
  - `doctor.declined` also carries `error_type` (or `error`, the config reason, when unconfigured), `model`, `latency_ms`, the token counts and `cost_usd`.
  - Every event carries `incident_kind` once this device has reported the incident.

## 7. Settings (`cloud/settings.py`, PR 2)

Each setting also goes in `.env.example` (`test_settings_defaults.py:108` checks this). Tests keep the default `off`; doctor tests opt in with `make_settings(doctor_mode="fake", …)`.

| Setting | Default | Meaning |
|---|---|---|
| `doctor_mode` | `"off"` | `off \| fake \| live`. `off`: the ask returns 503 and the tick does nothing. `fake`: FakeLlm. `live`: the configured LLM (`llm_*`) |
| `doctor_auto_consult` | `False` | The cloud opens consults on unresolved non-hardware incidents (D26) |
| `doctor_draft` | `"fake"` | `none \| fake \| coder` |
| `doctor_check` | `"stub"` | `stub \| sandbox` |
| `doctor_offer_stub_checked` | `False` | Offer proposals whose check is a stub (for demos and the F12 test). The event says so |
| `safety_paths_confirmed` | `False` | Stays false until Ishaan's K17 list replaces `safety_paths`. While false, every proposal is discarded (D25) |
| `doctor_asks_per_incident` | `6` | |
| `doctor_asks_per_device_day` | `30` | For asks with no incident |
| `doctor_consults_per_incident` | `2` | |
| `doctor_daily_usd_cap` | `5.0` | Covers asks and consults; checked before spending |
| `doctor_usd_per_1k_tokens` | `0.001` | Estimate *(inferred)*; replace with the real price |
| `doctor_ask_timeout_s` / `doctor_ask_max_tokens` | `15` / `400` | |
| `doctor_diagnose_timeout_s` / `doctor_diagnose_max_tokens` | `120` / `4000` | |
| `doctor_proposal_ttl_s` | `3600` | |
| `doctor_lookback_s` | `21600` | 6 h; consults never backfill older incidents |
| `doctor_tick_s` / `doctor_max_open` / `doctor_job_workers` | `5` / `5` / `2` | |
| `doctor_draft_estimate_usd` | `0.10` | PR 7. What one coder draft is assumed to cost: checked against the daily cap before each draft, and recorded when the coder reports no tokens. Estimate *(inferred)* |
| `doctor_trace_tail_lines` / `doctor_trace_tail_bytes` | `40` / `16000` | PR 7. The agent-trace tail in the case file |
| `doctor_evidence_file_bytes` / `doctor_evidence_code_bytes` | `4000` / `16000` | PR 7. Code excerpts per file and in total |

PR 7 also reads `coder_provider` (and the `llm_*` endpoint), `coder_image`, `coder_test_cmd`, `coder_replay_timeout_s`, `coder_scratch_dir` and `coder_max_drafts_per_hour`, the same settings the batch and gate use.

## 8. Events (PR 2 adds `LABELS` and the APIs §5 row; the emitters add the rest)

- **Refs:** always `device`, plus `incident` when known. New keys are `ask`, `consult` and `proposal` (document them in the `events.py` docstring). `overlay` always comes with `device`.
- **Actor:** `"fleet-doctor"`, class `"agent"`, except device-reported results (`device:<id>`, class `agent`).
- **`incident_kind`:** every event carries `data.incident_kind` when known, which the digest needs.

| Kind | Extra refs | `data` |
|---|---|---|
| `doctor.asked` | `ask` | `trigger, question (≤200 chars)` |
| `doctor.advised` | `ask` | `hypotheses, removed_for_safety, parsed_ok, model, prompt_tokens, completion_tokens, cost_usd, latency_ms` |
| `doctor.blocked_over_cap` | `ask` or `consult` | `spent_today_usd, cap_usd, which` (`spend \| asks \| consults`) |
| `doctor.declined` | `ask` or `consult` | `reason, stage` |
| `doctor.consult_opened` | `consult` | `origin, trigger, attempt` |
| `doctor.diagnosed` | `consult` | `hypotheses, parsed_ok, model, prompt_tokens, completion_tokens, cost_usd, stub` |
| `doctor.drafted` | `consult` | `paths, has_repro_test, why, stub, cost_usd` |
| `doctor.checked` | `consult` | `passed, stub, fails_before, passes_after, device_tests, touches_safety, summary` |
| `doctor.proposal_discarded` | `consult` | `reason` (`touches_safety \| safety_paths_unknown`) |
| `doctor.proposal_offered` | `consult, proposal` | `expires_at, base, stub_checked` |
| `doctor.proposal_result` | `consult, proposal`, `overlay` when applied | `result, reason` |
| `doctor.proposal_expired` | `consult, proposal` | — |
| `doctor.consult_closed` | `consult`, `proposal` if any | `outcome, why` |
| `doctor.gave_up` | `consult` | `reason, consults_used` |

**Spend today** is `SUM((data->>'cost_usd')::float)` over events whose `kind LIKE 'doctor.%'` and `ts >= day_start(now, fleet_timezone)`. It lives in `services/doctor_spend.spent_today(session, now, *, settings)` (PR 2), scoped to `settings.fleet_id`, and both L1 and L2 use it. Doctor events must therefore pass `fleet_id=settings.fleet_id` to `events.record` (its default is `"default"`).

## 9. Lock-in, timeline, digest, traces

- **Lock-in (PR 9, D24):** `services/doctor_lockin.proposal_draft(session, overlay, now, *, base_rev) -> DoctorDraft | None`. `DoctorDraft` is a `CodeDraft` subclass that carries `proposal_id` and the proposal's `touches_safety`. `base_rev` is the code the batch builds on (the class's current rev). The batch calls it in both lanes as `draft = proposal_draft(...) or <the Coder>`: in `batch._draft_code` (pass-through, `CODER_PROVIDER=fake`) before `tools.coder.draft`, and in `batch._await_drafts` (coding agent, M8b) before polling or submitting the `coder.draft` job.
  - **Fail closed.** `overlays.raw` is device-supplied, so `raw["proposal_id"]` is only a claim. A draft comes back only if a `doctor_proposals` row meets every check:
    - it has that id and the same `fleet_id`;
    - `device_id == overlay.device_id`;
    - `checks.stub` is not set, since a stub check checked nothing;
    - `sha256(overlay.code_patch) == sha256(proposal.code_patch)` (UTF-8 text);
    - `base["code_rev"] == base_rev`, and also equals `overlay.base_code_rev` when that is set ("stale base");
    - `status` is `applied` or `held`;
    - `overlay_id == overlay.id`.
  - **When a check fails,** `proposal_draft` returns None and records a `doctor.declined` (`stage = "lock_in"`, `reason = "lock-in: proposal not verified (…)"`, `proposal_id`; refs `device`, `overlay`, `incident`). The overlay goes through the Coder like any device patch.
  - **Sticky.** Once the overlay has a lock-in decline or a `coder.started`, `proposal_draft` returns None quietly from then on. A proposal that turns valid later never takes over, so it can't orphan a running Coder draft.
  - **Pending.** If every check but the status passes and the proposal is still `offered` and unexpired, the device's result hasn't arrived yet. `batch.run` (`_proposal_pending`) keeps the overlay held with one `overlay.deferred` (`reason = "doctor_pending"`) and decides nothing, so no Coder job is submitted until the proposal is applied (shortcut), refused, or past `expires_at` (declined, then the Coder).
  - **The draft:** `diff = overlay.code_patch`, `passed = True`, `test_output` = the proposal's `checks.summary`, and `notes` naming the proposal and consult, with "no second Coder pass (D24)". There is no Coder call, no job and no `coder.started`, so `CODER_MAX_DRAFTS_PER_HOUR` isn't spent. In the coding-agent lane, `_a_draft_finished` counts a verified proposal as a finished draft, so the batch runs early. The trigger is `doctor_lockin.newly_verified(..., since=batch_last_run_at)`: it fires only when the overlay or its proposal changed after the last run. An overlay that a run left held therefore doesn't rerun the batch every tick; the timer still runs it.
  - **Downstream is unchanged.** In the coding-agent lane, the draft still goes through `patch_refusal`. It gets its own drafts key (`doctor.proposal:<id>`), so it folds alone, and `_lock_in_drafts` builds a `source = "doctor"` code version. `code_lockin.REPLAYED_SOURCES = {"coder", "doctor"}`: the gate gives both the real `replay_code_patch` and doesn't treat them as pass-through. It never trusts the draft's `passed`. In the pass-through lane, the version stays `passthrough`, with the labeled stub replay.
  - **Safety flag.** `touches_safety` is the proposal's flag OR the overlay's OR the patch check, in both lanes.
  - **Code candidates** still stop at `awaiting_canary` (B7).
  - **Records:** the candidate's why names the proposal ("the Fleet Doctor's change … proposal …"). `candidate.created.data` has `source = "doctor"` and `proposal_id` when the candidate is one proposal. A pass-through fold that mixes proposals with device patches has `proposal_ids` instead.
  - **Flow test:** `tests/flows/test_f12b_doctor_lock_in.py`.
- **Timeline (PR 6, as built):**
  - `domain/story.py` adds `Story.ledger -> list[dict]`: hypotheses from `doctor.advised` and `doctor.diagnosed`, newest first, each `{at, source (ask | consult), claim, how_to_check, ask | consult (the id), model, stub}`. `data.hypotheses` that isn't a list adds nothing; an item without a non-blank string claim is skipped; a bare string is a claim; each claim and how_to_check is cut to `LEDGER_TEXT_MAX = 500` characters for display (the event keeps the full text). `Step` gains an optional `doctor` field (the event's data, on `doctor.*` steps only) that the ledger and status read. `to_dict` adds a top-level `ledger` (with `at` as ISO); step dicts keep their shape.
  - `LINES` for all 14 kinds. `STATUS`: `consult_opened` (consulting, wait), `proposal_offered` (fix offered, wait), `gave_up` (bad); a `consult_closed` picks its badge from `data.outcome` via `CONSULT_OUTCOME_STATUS`. Every outcome but `held` is bad, because nothing was applied and kept. `held` sets no status: `overlay.held` already says so, and a close that lands after lock-in mustn't pull the badge back from the gate. An unknown outcome is info. **Precedence:** a Doctor status never covers one that needs a person (`NEEDS_PERSON`: work orders `blocked_over_cap`, `needs_human`, `stopped`, and `candidate.frozen`); a later non-Doctor step still can. L1 kinds don't move the badge. `STAGE`: one `DOCTOR_STAGE = 30` for every doctor kind (after the heal and its evidence, before work orders and lock-in), so doctor events on a tied `ts` keep their write order (a tick can open, refuse and close a consult, or close attempt 1 and open attempt 2, at one `now`). `NOT_IN_CHAINS` gains nothing.
  - `api/web.py` `_story_view` passes the ledger, and `web/templates/partials/story.html` shows a "Fleet Doctor" block only when it isn't empty, labelling `stub` (from `data.stub`) and `fake model` (a model starting with `fake`, e.g. `fake-templated`).
- **Digest (PR 6, as built):** `domain/digest.py` adds `COUNTED_KINDS` entries `doctor_asks` (`doctor.asked`), `doctor_consults` (`doctor.consult_opened`), `doctor_proposals_offered`, `doctor_gave_up`, and a small `COUNTED_OUTCOMES` table for counts that need a `data` match: `doctor_consults_held` (`doctor.consult_closed`, outcome `held`) and `doctor_proposals_applied` (`doctor.proposal_result`, result `applied`; architecture §10 item 4 asks for "applied and held"). `Digest.doctor_by_kind` breaks those six down per `data.incident_kind` (`unknown` when missing), with the `doctor_` prefix dropped in each row. `Digest.doctor_spend_usd` sums `data.cost_usd` over `doctor.*` events by `doctor_spend`'s rules: only numbers count, a negative counts as 0, and a value too big for a float counts as infinity. Where `doctor_spend`'s infinite total makes `check_spend` fail closed, the digest reports a non-finite total as `null` ("spend unknown" in the text), so `GET /v1/digest` never fails on infinity. `render_text` adds one "Fleet Doctor:" line only when a `doctor.*` event is in the window. All of it is in `GET /v1/digest`. Doctor kinds are **not** in `ACTION_KINDS`, so they don't move the no-human share (a follow-up if wanted).
- **Traces (PR 7):** each closed consult writes one `Recording(kind="doctor_consult", format="jsonl")` blob holding the case file, ledger, draft, checks and outcome. `services/datasets._traces_files` (`datasets.py:531`) includes `doctor_consult` next to `agent_trace`.
  - **As built:** `services/doctor_trace.write`, called from `doctor._close` for **every** close (refusals at admit included).
  - **Lines:** one JSON message per line, in the shape `domain.datasets.sharegpt` already reads: `{"role": "system", "name": "consult", ...}` (ids, attempt, origin, trigger, base, times, cost, `retry_of`), `user`/`case_file`, `assistant`/`ledger`, then `assistant`/`draft` and `tool`/`checks` when the consult got that far, and `system`/`outcome` (`outcome`, `why`, `proposal_id`).
  - **No secrets:** the LLM and Naïve keys are scrubbed from the text before it is stored; the export's own redaction runs on top.
  - **Storage:** the bytes go into the blob store under their sha256, with a `blobs` row (`media_type: application/jsonl`, insert-or-nothing), so snapshots include them. The `recordings` row has a fresh UUID, the consult's device and incident, `started_at` = opened, `ended_at` = `received_at` = closed, firmware and harness from `incident.running`, `schema_version: doctor_consult/1`, and `raw = {source: "fleet-doctor", consult, outcome}`. `doctor.consult_closed` names it (`refs.recording`, `data.trace_recording`) in the same transaction.
  - **Export:** `datasets.TRACE_KINDS = ("agent_trace", "doctor_consult")`, and every `traces.jsonl` line now carries `kind`, an additive field.
- **Skills:** nothing new. `batch._write_skills` already runs on every held overlay with an incident, Doctor overlays included.

## 10. Device side (reference code in `tools/fake_device`, PR 5)

This is the code Ishaan copies or imports into the Pi app. It stays out of `visor.Device`.

```python
# client.py (DeviceClient)
def ask_doctor(self, ask: dict, *, timeout_s: float) -> dict | None   # None on any error, non-200 or timeout; direct call, not the outbox;
    # timeout_s bounds the TOTAL wait (the request runs on a daemon thread; httpx.Timeout(timeout_s) per phase too)
def get_proposal(self, proposal_id: str, *, expected_sha256: str | None = None) -> tuple[dict, str]
    # (body, sha256); checks X-Sha256 like the packages, and the pointer's sha256 when given → HashMismatch / InvalidPackage
def report_proposal_result(self, proposal_id: str, result: ProposalResult) -> None   # used by the outbox
# outbox.py: Outbox.add_proposal_result(proposal_id, result), kind "proposal_result" → POST …/proposals/{pid}/result

# doctor.py (as built in PR 5)
@dataclass(frozen=True)
class HealStep:            # command, read=(), changed=(), check="pass"|"fail"|None, build_failed=False
class CircuitBreaker:      # allow(), trip(reason), reset(); per heal
class TriggerHook:         # TriggerHook(client, *, k=2, budget_share=0.7, budget_s=20, max_asks_per_incident=6)
                           # start_heal(incident_id=None); then maybe_ask([]) once (T6, T7), then after each step
                           # detect(steps, *, verify_failures=0, budget_used=0.0, tier=None, skill_matched=True)
                           #   -> the first trigger that holds AND hasn't asked yet this heal, or None
                           # maybe_ask(steps, *, ...same..., question=None, context=None) -> str | None
                           # history: [{"trigger", "ask", "advice"}]
def triggers_holding(steps, *, ...) -> list[str]   # pure: every trigger that holds, in priority order
class ProposalHost(Protocol):   # parked_and_idle, running, active_overlay_id,
                                # snapshot() -> JSON handle that survives a restart, restore(handle),
                                # apply_overlay, verify(proposal) -> (ok, detail), healthy, mark_good
class ProposalApplier:     # ProposalApplier(client, host, outbox, *, state_path=None, mark_good_s=60, safety_paths=(),
                           #                 clock=time.monotonic)
                           # recover(now) -> "good"|"reverted"|"failed_verify"|"stale"|None   # at boot (handle/tick call it too)
                           # handle(pointer, now, *, frozen=False) -> "applied"|"rejected"|"stale"|"failed_verify"|None
                           # tick(now) -> (overlay_id, "good"|"reverted") | None      # one health sample for the watch
class MarkGoodTimer:       # MarkGoodTimer(overlay_id, proposal_id, hold_s=60, replaced_overlay_id=None, clock=time.monotonic)
                           # check(healthy) -> "watching"|"good"|"failed"
def base_mismatch(base, running, active_overlay_id, code_patch) -> str   # "" when it matches
def format_advice(advice, trigger, *, max_tokens=300) -> str | None
```

**How the PR 5 code settles details this section left open:**
- **Asks.**
  - Call `maybe_ask([])` once right after `start_heal`: T6 and T7 fire only before the first step.
  - Each trigger asks at most once per heal, but a later one still can (T4 or T5 after T2).
  - T4 means the same command 3 times *in a row* (whitespace collapsed), or 5 steps in a row that read no new file and change none.
  - The 6-ask cap is per incident; asks with no `incident_id` are capped per heal, so the hook never goes silent.
  - Empty advice injects nothing and doesn't trip the breaker.
- **Applier check order.**
  - Already handled or under watch → no-op. Frozen → no-op.
  - Then: sha mismatch → `rejected`; not parked or busy → `rejected`; base mismatch → `stale`; expired → `rejected`; malformed patch or a `safety_paths` hit → `rejected`.
  - Then snapshot, write the attempt to `doctor.json`, apply, verify plus health (on failure, restore and report `failed_verify`), then watch.
- **Base.** `firmware` and `code_rev` always match. `harness` only when the proposal sets it, `mcu_build` only for a patch under `firmware/`. The active overlay must equal `base.overlay_id`, and both null counts as a match.
- **GET errors.** Only 404 and 410 drop a proposal for good, with no result. 409 (frozen), 401, 429, 5xx, network and any other error retry on a later tick.
- **One proposal at a time.** A second pointer waits until the current watch ends. Handled ids are kept in `doctor.json`, so a restart never applies one twice.
- **Mark-good.** The timer runs on a monotonic clock, injectable for tests; `FakeArm` passes the tick's `now`. It is good once `hold_s` has passed *and* a healthy sample falls in each third of the time since apply (the check right after apply counts as the first). So one sample at ≥60 s isn't enough, and sparse sampling only lengthens the watch. One unhealthy sample means it failed, and it stays failed: later healthy samples never mark it good.
- **After the watch.** Mark-good calls `host.mark_good`, which unparks in the sim. Only then is a replaced overlay reported `superseded`. A failure reports the proposal overlay `reverted`, drops the timer and flags the attempt `failed` *before* restoring the snapshot. A restore that raises is retried on the next tick or at boot, and a new proposal waits until it succeeds. `FakeArm` reports a marked-good proposal overlay `held` `proposal_hold_after_s` later (default 1200 s).
- **Crash safety.**
  - Before `apply_overlay`, `doctor.json` gets `inflight`, written with write-then-rename. It holds the proposal and overlay ids, `started_wall` (for logs; the watch never resumes after a restart), `replaced_overlay_id` and the snapshot handle.
  - It also holds a flag for each stage reached, each set *before* the step it guards: `reported` (`applied` queued), `verify_failed`, `failed` (watch failed, `reverted` queued) and `good`.
  - `recover(now)` runs at boot; `FakeArm` calls it on its first tick, before the check-in. It only restores while the active overlay is ours or the one we replaced, never an overlay someone else installed after the crash. What it does with the attempt:
    - marking good: finish only if `healthy()` still passes, else revert;
    - `failed`: retry the restore;
    - `verify_failed`: restore, then report `failed_verify` (never `applied` + `reverted`);
    - `reported` and still ours: queue `reverted`, then restore;
    - nothing queued yet:
      - still ours: queue `applied` first (never `stale` for an applied overlay), then `reverted`, then restore;
      - the replaced overlay (or none) in force: restore, then report `failed_verify`, so it isn't retried forever;
      - someone else's overlay: leave it alone and report `stale`;
    - ours is gone (already reverted, or replaced by a newer version): nothing left to do.
  - Run the applier in a separate supervisor process (the visor). Inside the app that `apply_overlay` restarts, every proposal would be reverted at boot.
- **Sim.** `SimDevice` gains `consult_parked` (health `degraded`), `task_running`, the knobs `proposal_verify_ok` and `fault_recurs`, and snapshot handles kept on the object (it outlives a simulated restart). It implements `ProposalHost`. `FakeArm` has `doctor` (a `TriggerHook`) and `applier`.

- **New scenario:** `SCENARIOS["consult"]` makes an L1 ask (T2) through `arm.doctor`, parks for the consult, and sends `resolved=False`, `suspected_cause="software"`, `running.code_rev` set, the advice in `agent.reasoning`, and `consult={"wanted": true, "trigger": "T2", "attempts": 2}`. At `hold_after_s` the arm unparks unless a fix is under watch.
- **Chaos:** a `doctor` chaos kind (`Rates.doctor_every_s`): 0 in `overnight`, 1800 in `demo`. It lands on non-canary arms and uses its own random stream, so the overnight schedule is unchanged.
- **`fake_cloud.py`:** the three routes are in `ROUTES`, with the real cloud's errors.
  - The ask returns 409 while frozen.
  - The GET returns 409 while frozen, and 410 once a result is recorded or the proposal has expired by `FakeCloud.clock`.
  - `FakeCloud.offer(device_id, proposal=None)` makes check-ins carry the pointer until a result or expiry.
  - `canned_proposal(now=None, ttl_s=3600)` expires relative to `now`, and `CANNED_ADVICE` mirrors the contracts example.

## 11. L3 contract only (`contracts/tools.py`, PR 8; D27 deferred)

These typed models define the device tool service. There's no route, socket or lease.

```python
class ToolSpec(CloudModel): name: str; tier: Literal["read", "act"]; grant: str | None  # "act:*" on act tools only; params: dict  # JSON Schema
                            returns: dict; summary: str
class ToolCall(CloudModel): call_id: str; name: str; args: dict; lease_id: str | None = None
class ToolResult(DeviceModel): call_id: str; ok: bool; result: Any = None; error: str = ""
TOOL_CATALOG: tuple[ToolSpec, ...]
# read: read_logs, read_audit, grab_frame, stack_dump, list_devices, read_file, run_health, run_tests_scratch
# act:  apply_overlay, park, set_mode, rollback, build_and_flash
```

There's no raw shell, ever (architecture §9). The catalog is a draft until Ishaan's h3 tool list (handoff c).

## 12. Tests: one flow per behavior

| Test | PR | Proves |
|---|---|---|
| `cloud/tests/unit/domain/test_domain_doctor.py` | 2 | caps fail closed, parsing, safety filter, fake draft passes `summarize_code_patch`, day start |
| `cloud/tests/unit/services/test_services_doctor_jobs.py` | 2 | inline/thread submit, poll, missing |
| `cloud/tests/unit/adapters/test_adapter_llm.py` (+cases) | 2 | `complete` fake and live (httpx mock), usage, timeout |
| `cloud/tests/flows/test_f12a_doctor_ask.py` | 3 | L1: ask → advice; mode off → 503; frozen → 409; cap → 429 plus `doctor.blocked_over_cap`; safety filter; events on the story |
| `cloud/tests/flows/test_f12_doctor_consult.py` | 4 | L2 cloud side: consult.wanted → admit … offer → pointer on check-in → GET with sha → result `applied` → overlay row → held → `consult_closed: held`; stale, discard, gave_up, cap, frozen. Also: held-before-applied placeholder, expiry and a late result, no `code_rev`, lookback, auto-consult, defaults, pending/lost/failed jobs, route errors |
| `cloud/tests/unit/domain/test_domain_doctor_consult.py` | 4 | `applied_outcome` and `after_close` (retry, give up, bad counts fail closed) |
| `tools/fake_device/tests/test_doctor.py` | 5 | breaker, triggers, applier base/expiry/parked checks, mark-good revert |
| `cloud/tests/flows/test_f12d_interop_fake_device.py` | 5 | the unmodified fake device runs F12 end to end through `device_http` against the real cloud: L1 ask → consult → offer → pull with sha → apply → mark-good → `held` → `consult_closed: held`, the story in order; frozen hides the pointer until unfrozen |
| `cloud/tests/unit/domain/test_domain_story_doctor.py` | 6 | ledger order, sources and robustness; lines, labels, status by outcome, tie order |
| `cloud/tests/unit/domain/test_digest_doctor.py` (+ `test_services_digest.py` case) | 6 | digest counts in total and per incident kind, spend ignoring bad values, unchanged text with no Doctor events |
| `cloud/tests/flows/test_f2_timeline_page.py` (+case) | 6 | the incident page shows the "Fleet Doctor" block with stub and fake labels; the story JSON has `ledger` |
| `cloud/tests/flows/test_f3_lock_in.py` (+case) | 9 | a held Doctor overlay becomes a candidate without a Coder call → `awaiting_canary` |
| `cloud/tests/unit/domain/test_domain_doctor_real.py` | 7 | `split_patch` (sections, prefixes, renames, header look-alikes in hunks, no-newline markers, mixed forms refused), `find_repro_test` (new files only; modified, renamed, copied never), `tampered_tests` (any edit to an existing test), `screen_fix` (planted runners at any depth, test config, flag-only, no code change), `flag_only_fix`, `code_only`, `patch_changes`, `held_out_files`, `patch_path`, `draft_task` (order, safety paths, fencing, bounds), `check_verdict` / `repro_failures` / `repro_passes` (exit 1 only, ids in the repro file, missing-name failures, same ids pass, verify states), `pytest_results` (only after the last summary header, stops at the counts), `withhold_verify`, `package_rel`, `named_paths`, `code_excerpts`, `tail_lines`, `coder_usage` |
| `cloud/tests/unit/services/test_services_doctor_evidence.py` | 7 | case file with an in-memory blob store and a seeded code package: trace tail (this incident's stored traces only, newest last, byte caps, read from the end, no-seek store, vanished blob), code excerpts and caps, `verify/` never present, unregistered or missing rev, a package without `device/` |
| `cloud/tests/unit/services/test_services_doctor_jobs.py` (reworked) | 7 | the adapter re-exports the shared types; `default_jobs` is the `"doctor"` pool, apart from the coder pools |
| `cloud/tests/flows/test_f12c_doctor_real.py` | 7 | scripted coder + real `Sandbox` on `FakeDocker` (host pytest on the toy device): repro fails then passes, device tests pass on the code change, held-out verify passes → offered with the stub opt-in off (`PYTHONSAFEPATH=1` on every run; `verify/` only in the last replay; no verify output kept); no verify scripts → `verify: none available`. These close `failed_checks`:
- in the sandbox: repro passes on the base; the fix fails; the reviewer's `DOCTOR_FIXED` probes (module-level import → exit 2; import or attribute in the body); a printed fake `FAILED` line (item 5); a repro that monkeypatches `step` at import to mask a broken `release_requested` (item 3); a partial fix that passes everything but verify → exactly "held-out verify failed" (item 4);
- before any sandbox run (`screen_fix`): edits or deletes an existing test; weakening `test_gripper.py` plus breaking release (item 1); a planted `pytest.py` / `tests/pytest.py` / `sitecustomize.py` / `*.pth` / `_pytest/` (item 2); a `.gitignore`-hidden `conftest.py` (item 6); a flag-only fix, in place or as a new file (item 7);
- at draft: no repro, empty, own tests failed, touches `verify/`.

Also: a compound `CODER_TEST_CMD` is passed verbatim; the shared coder budget makes the consult wait, then draft; a waiting consult never starves a new one (`max_open=1`, budget 0); no real coder → `advised`, nothing spent; a 429 blip backs off, is refunded and retries; 5 transient errors → `gave_up`; a closed consult's `doctor_consult` recording is scrubbed and appears in `traces_jsonl` |
