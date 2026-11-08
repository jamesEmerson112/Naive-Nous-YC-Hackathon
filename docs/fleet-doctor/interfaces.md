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
| Lock-in skips a second Coder pass | `batch._draft_code` calls `coder.draft` for every held overlay | A one-branch change in `_draft_code`, coordinated with M8b (§9) |

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
    budget_s: float = 20                       # the device gives up after this
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
- `device.CheckInResponse` gets `proposal: ProposalPointer | None = None`. Update `test_contract_models.py:194-207`, which pins the dump's keys.
- `incident.IncidentReport` gets `consult: ConsultRequest | None = None`.
- `incident.Overlay` gets `proposal_id: str | None = None`.

**Wiring:**
- Register every new model in `schema.MODELS` and `__init__.__all__`.
- Add examples `examples/doctor_ask.json`, `doctor_advice.json`, `proposal.json`, `proposal_result.json` with `MODEL_BY_PREFIX` entries.
- Add README rows.

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
1. A short session reads the device, `frozen`, the caps and the spend, then closes.
2. The LLM call runs with **no session open**. Its timeout is `min(settings.doctor_ask_timeout_s, body.budget_s - 2)`.
3. A second short session records `doctor.asked` and `doctor.advised`, or `doctor.blocked_over_cap`, then commits.
4. The handler returns.

Get the sessionmaker from `request.app.state.sessionmaker`. Two asks at the same moment can each pass the cap check, so the cap can be overshot by one. That's accepted *(inferred)*.

**Clock:** `cloud/tests/flows/conftest.py`'s `clock` fixture patches `utcnow` in `api.device` and `api.ops`. Add `api.doctor` and `api.proposals` to it (PR 2).

## 4. Tables (`cloud/models.py`, PR 2)

**Both tables are new, so `create_all` makes them on restart and no `ALTER` is needed.** Ids come from `ids.new_id("cons" | "prop")`; add `"cons"`, `"prop"` and `"ask"` to `ids.PREFIXES`.

```python
class DoctorConsult(Base):          # __tablename__ = "doctor_consults"
    id: str  (pk, "cons_…")
    fleet_id, device_id: str, incident_id: UUID (FK incidents.id, indexed)
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

class DoctorProposal(Base):         # __tablename__ = "doctor_proposals"
    id: str  (pk, "prop_…")
    fleet_id, device_id: str, consult_id: str (FK), incident_id: UUID
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

- **Registration:** `worker.STEPS.append(Step("doctor.tick", doctor.tick, every_s=settings.doctor_tick_s))`. PR 2 appends it, with a stub tick that returns `{}`.
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
   | `admit` | `domain.doctor.admit(...)`: caps, spend, `running.code_rev` present, safety confirmed | `gather`, or closed with `declined` / `blocked_over_cap` / `gave_up` |
   | `gather` | `doctor_evidence.case_file(session, store, consult, incident)` builds a plain dict, all evidence marked untrusted | `diagnose` |
   | `diagnose` | Submits job `diagnose:{id}`, built from the case file and `llm`. When done: ledger saved, `doctor.diagnosed` recorded with `cost_usd` | `draft` |
   | `draft` | `doctor_draft` decides: `none` closes with `advised`; `fake` uses `domain.doctor.fake_draft(case)` (a labeled stub patch); `coder` submits job `draft:{id}`. Records `doctor.drafted` | `check` |
   | `check` | `touches_safety` closes with `discarded`. Then by `doctor_check`: `stub` records `{"stub": true}`; `sandbox` submits job `check:{id}`. Records `doctor.checked` | `offer`, or closed with `failed_checks` |
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

## 6. Seams

### 6.1 `domain/doctor.py` (pure, PR 2)

Imports only stdlib and `romulus_contracts`, and takes `now` as an argument.

```python
@dataclass(frozen=True) class Spend: spent_today_usd: float; cap_usd: float
@dataclass(frozen=True) class Decision: ok: bool; outcome: str | None; why: str     # outcome: declined | blocked_over_cap | gave_up
def check_spend(spend: Spend, *, estimate_usd: float = 0.0) -> Decision            # fails closed on NaN/negative, like triage.check_caps
def admit(*, frozen: bool, code_rev: str | None, consults_for_incident: int, consult_cap: int,
          spend: Spend, mode: str) -> Decision
def admit_ask(*, frozen: bool, asks_for_incident: int, ask_cap: int, asks_for_device_today: int,
              device_day_cap: int, spend: Spend, mode: str) -> Decision
def cost_usd(prompt_tokens: int, completion_tokens: int, usd_per_1k_tokens: float) -> float
def parse_hypotheses(text: str, *, max_items: int = 5, max_chars: int = 1200) -> tuple[list[dict], bool]
    # → ([{claim, how_to_check}], parsed_ok). Takes the first JSON object/array; if there's none, one
    #   hypothesis from the prose, with parsed_ok=False. Trims to the char budget (≈300 tokens).
def filter_safety(hyps: list[dict], safety_paths: Iterable[str]) -> tuple[list[dict], int]   # drops any naming a safety path
def ask_prompt(ask: dict) -> tuple[str, str]                                 # (system, user); evidence wrapped as untrusted data
def diagnose_prompt(case: dict) -> tuple[str, str]
def fake_draft(case: dict) -> dict  # {code_patch, repro_test, why, stub: True}: adds doctor/FAKE_FIX.md, valid per summarize_code_patch
def day_start(now: datetime, tz: str) -> datetime                           # same calendar day as workorders._day
```

**Prompt rule:** device text (logs, traces, files, the question) goes inside a fenced `<evidence untrusted="true">` block. The system prompt says to treat it as data and answer only in the JSON shape (§6 item 6 of the architecture doc).

### 6.2 `services/doctor_jobs.py` (PR 2): a stand-in for M8b's shared `services/jobs.py`

M8b is building one generic off-tick runner, `services/jobs.py`, in this shape (agreed with the M8b session). Until it merges, `doctor_jobs.py` is a thin local stand-in with the **same shape**. When M8b lands, `doctor_jobs.py` becomes `from romulus_cloud.services.jobs import *`, a one-line swap.

```python
@dataclass(frozen=True) class Pending: ...
@dataclass(frozen=True) class Done:    result: Any
@dataclass(frozen=True) class Failed:  error: str
@dataclass(frozen=True) class Missing: ...                 # unknown key, e.g. after a worker restart → resubmit
class Jobs(Protocol):
    def submit(self, key: str, fn: Callable[[], T]) -> None: ...   # idempotent: a pending or done key is a no-op
    def poll(self, key: str) -> Pending | Done | Failed | Missing: ...
class ThreadPoolJobs:   # ThreadPoolExecutor(max_workers=settings.doctor_job_workers), inside the worker process
class InlineJobs:       # runs fn at submit; for tests
def default_jobs(settings) -> Jobs                         # process-wide singleton
```

- A job function gets plain inputs (the case-file dict and an adapter) and returns plain data. **It never opens a DB session.** The tick submits, polls, and applies results in its own transaction.
- Diagnose and draft both use it. `Coder.draft(..., task=...)` (a from-scratch prompt) comes from M8b; the Doctor PRs don't add `task` themselves.

### 6.3 `adapters/llm.py` (PR 2)

```python
@dataclass(frozen=True) class LlmReply: text: str; model: str; prompt_tokens: int; completion_tokens: int
class Llm(Protocol):  # existing explain / write_skill, plus:
    def complete(self, system: str, user: str, *, max_tokens: int | None = None, timeout_s: float | None = None) -> LlmReply: ...
```

- **`OpenAICompatLlm.complete`:** one chat call, with per-call `timeout` and `max_tokens` (`client.with_options(timeout=…)`). Usage comes from `resp.usage` (0 if it's missing). Errors raise `LlmError`.
- **`FakeLlm.complete`:** deterministic `FAKE_BANNER` plus prose that echoes the first 80 characters of `user`. Tokens are `len // 4`, so caps can be tested.
- `explain` and `write_skill` stay as they are.

### 6.4 `services/doctor_evidence.py`

```python
def case_file(session, store, consult, incident, *, settings) -> dict
# {"incident": {kind, summary, tier, signals, outcome, running}, "log_tail": [...], "trace_tail": [...],
#  "code": {"rev", "files": [{path, excerpt}]}, "tried": [...], "untrusted": True, "notes": [...]}
```

- **v0 (PR 4):** incident fields plus `log_tail` (from `Incident.raw["log_tail"]`), with a note: "trace, code, mcap: not wired".
- **Full (PR 7):**
  - the trace tail from `agent_trace` recordings (`Recording.kind == "agent_trace"`, read through `BlobStore.get`), last N lines;
  - code at the base rev (`registry.get_code` → `open_package`), the files named in the log or trace, capped by bytes.
- **MCAP stays a labeled "not wired"**, because `mcap` isn't a dependency.

### 6.5 `services/proposals.py` (PR 2 stub, PR 4)

```python
def pointer_for(session, device, now) -> ProposalPointer | None        # called at devices.py:324; None if frozen
def offer(session, consult, draft: dict, checks: dict, now, *, settings) -> DoctorProposal
def get_for_device(session, device_id, proposal_id, now) -> tuple[bytes, str]   # (body, sha256)
def record_result(session, device_id, proposal_id, body: ProposalResult, raw: dict, now, *, safety_paths) -> bool  # True if new
def expire_due(session, now) -> int
```

**`record_result("applied")` creates the overlay row itself** (architecture §5.4). It sets:
- `(device_id, overlay_id)`;
- `incident_id` = the proposal's incident (so `batch.held_overlays` sees it);
- the `base_*` fields from `proposal.base`;
- `code_patch`, `touches_safety`, `status="active"`;
- `raw={"proposal_id", "source": "doctor"}`.

If an `OverlayStatus` arrived first, `incidents.set_overlay_status` will have made a placeholder with `incident_id=None`. Fill that placeholder in and keep its status, the way `_apply_overlay` does (`incidents.py:191-199`). Record `overlay.applied` (actor `device:<id>`, class `agent`) and `doctor.proposal_result`.

### 6.6 `services/doctor_ask.py` (PR 3)

```python
def ask(sessions: sessionmaker, device_id: str, body: DoctorAsk, raw: dict, now, *, settings, llm) -> DoctorAdvice   # raises ApiError per §3
```

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

**Spend today** is `SUM((data->>'cost_usd')::float)` over events whose `kind LIKE 'doctor.%'` and `ts >= day_start(now, fleet_timezone)`. It lives in `services/doctor_ask.spent_today(session, now, settings)`, and both L1 and L2 use it.

## 9. Lock-in, timeline, digest, traces

- **Lock-in (PR 9):** in `services/batch._draft_code` (`batch.py:656`), an overlay with `raw.get("proposal_id")` skips `tools.coder.draft`. Its draft is `CodeDraft(diff=ov.code_patch, test_output="checked by the Fleet Doctor (consult …)", passed=True, notes=…)`, and the candidate records `data.proposal_id`. **M8b owns `_draft_code`**, so this lands after M8b, or as a one-line hook M8b leaves for it (coordinate with the M8b session). Candidates still stop at `awaiting_canary` (B7).
- **Timeline (PR 6):**
  - `domain/story.py` adds `Story.ledger -> list[dict]`: hypotheses from `doctor.advised` and `doctor.diagnosed`, newest first, each with `source` (`ask \| consult`) and `at`.
  - It also adds `LINES`/`STAGE`/`STATUS` entries for the doctor kinds.
  - `api/web.py` `_story_view` passes the ledger, and `web/templates/partials/story.html` shows a "Fleet Doctor" block.
- **Digest (PR 6):** `domain/digest.py` adds `COUNTED_KINDS` entries for `doctor.asked`, `doctor.consult_opened`, `doctor.proposal_offered`, `doctor.consult_closed` with outcome `held`, and `doctor.gave_up`. It also adds a `doctor_by_kind: dict[str, dict[str, int]]` breakdown keyed on `data.incident_kind`, and a `render_text` line.
- **Traces (PR 7):** each closed consult writes one `Recording(kind="doctor_consult", format="jsonl")` blob holding the case file, ledger, draft, checks and outcome. `services/datasets._traces_files` (`datasets.py:531`) includes `doctor_consult` next to `agent_trace`.
- **Skills:** nothing new. `batch._write_skills` already runs on every held overlay with an incident, Doctor overlays included.

## 10. Device side (reference code in `tools/fake_device`, PR 5)

This is the code Ishaan copies or imports into the Pi app. It stays out of `visor.Device`.

```python
# client.py (DeviceClient)
def ask_doctor(self, ask: dict, *, timeout_s: float) -> dict | None   # None on any error or timeout; direct call, not the outbox
def get_proposal(self, proposal_id: str) -> tuple[dict, str]          # (body, sha256); checks X-Sha256 like pull_firmware
# outbox.py: new kind "proposal_result" → POST …/proposals/{pid}/result

# doctor.py
class CircuitBreaker:      # per heal: after the first failed or timed-out ask, every later ask is skipped
class TriggerHook:         # detect(steps, *, verify_failures, budget_used, tier, skill_matched) -> str | None  (T2, T4, T5, T6, T7)
                           # maybe_ask(...) -> str | None  (the ≤300-token text to inject, or None)
class ProposalApplier:     # on check-in pointer: pull → sha check → parked? → base matches? → not expired? →
                           # apply overlay (proposal_id) → verify/health → MarkGoodTimer(N=60 s) → queue the result
class MarkGoodTimer:       # health must hold N seconds or the overlay reverts (RAUC mark-good)
```

- **New scenario:** `SCENARIOS["consult"]` sends `resolved=False`, `suspected_cause="software"`, `running.code_rev` set, and `consult={"wanted": true, "trigger": "T2", "attempts": 2}`.
- **Chaos:** a `doctor` chaos kind with rate 0 in `overnight` and > 0 in `demo`.
- **`fake_cloud.py`:** add the three routes to `ROUTES`.

## 11. L3 contract only (`contracts/tools.py`, PR 8; D27 deferred)

These typed models define the device tool service. There's no route, socket or lease.

```python
class ToolSpec(CloudModel): name: str; tier: Literal["read", "act"]; params: dict  # JSON Schema
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
| `cloud/tests/flows/test_f12_doctor_consult.py` | 4 | L2 cloud side: consult.wanted → admit … offer → pointer on check-in → GET with sha → result `applied` → overlay row → held → `consult_closed: held`; stale, discard, gave_up, cap, frozen |
| `tools/fake_device/tests/test_doctor.py` | 5 | breaker, triggers, applier base/expiry/parked checks, mark-good revert |
| `cloud/tests/flows/test_interop_fake_device.py` (+case) | 5 | the fake device runs F12 end to end through `device_http` |
| `cloud/tests/unit/domain/test_domain_story_doctor.py` | 6 | ledger, labels, digest counts per incident kind |
| `cloud/tests/flows/test_f3_lock_in.py` (+case) | 9 | a held Doctor overlay becomes a candidate without a Coder call → `awaiting_canary` |
