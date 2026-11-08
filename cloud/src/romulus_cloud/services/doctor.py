"""Fleet Doctor consults: the L2 state machine, one worker step (interfaces.md §5).

`tick` runs every DOCTOR_TICK_S (`worker.steps_for` leaves it out while DOCTOR_MODE=off):

1. **Off or frozen:** return. Consults hold where they are and nothing is written (D11).
2. **Open** (`open_new`): one consult (attempt 1) per recent incident that has none and
   either asked for one (`consult.wanted`, origin `device`) or, with DOCTOR_AUTO_CONSULT on,
   is unresolved and not hardware (origin `cloud`). Only incidents received within
   DOCTOR_LOOKBACK_S count, so it never backfills history.
3. **Advance:** up to DOCTOR_MAX_OPEN working consults, oldest first, rows locked with
   `SKIP LOCKED`, each **at most one stage**:
   admit → gather → diagnose → draft → check → offer → applied → closed.
   Consults in `applied` only wait on the device (cheap reads), so they're synced every
   tick outside that limit: a silent device can't starve new consults.
4. **Expire** offered proposals past `expires_at` (`proposals.expire_due`).
5. **Follow up:** a consult closed `failed_checks`, `reverted` or `failed_verify` opens
   attempt N+1 while the incident has consults left, else records `doctor.gave_up`.

**Safety (D24, D25).** The Doctor has no write path to a device: it offers, the device
pulls and applies. A draft that touches a safety path, or any draft while
SAFETY_PATHS_CONFIRMED is false, is discarded (checked at `check` and again at `offer`, and
`proposals.offer` refuses as a last line). A stub check is offered only with
DOCTOR_OFFER_STUB_CHECKED on, and every event says stub. Spend is checked at admit
(`domain.doctor.admit`, fails closed).

**Slow work** (the diagnose LLM call, the coder's draft, the sandbox check) runs off the
tick as a job (`services/doctor_jobs`, over M8b's `services/jobs.py`): the tick submits,
polls on later ticks and applies the result in its own transaction, then forgets the key
once that transaction commits. A lost job (worker restart) is resubmitted; after 3 tries
the consult gives up.

**Real draft and check (PR 7, behind DOCTOR_DRAFT=coder and DOCTOR_CHECK=sandbox; the
defaults stay fake and stub):**
- `draft` asks the coder (`doctor_code.draft_job`) for a reproduction test under `tests/`
  and the minimal fix (`domain.doctor.draft_task`), on the base package without `verify/`.
  Each submit records `coder.started` (`data.source = "doctor"`) and counts against
  CODER_MAX_DRAFTS_PER_HOUR with the batch's drafts; with the budget used up the consult
  waits in `draft` until `raw.retry_at` (one `doctor.declined`, `which: coder_budget`),
  it doesn't fail. A provider blip (`domain.coder_retry`) backs off the same way and a
  zero-token one is refunded to the budget; the 5th in a row gives up. No real coder
  (`FakeCoder`, `Unavailable`) closes `advised` before anything is spent. A draft with no
  test file, an empty or refused diff, or a failing own test run closes `failed_checks`.
- `check` fails a patch that tampers with existing tests, then proves in the coder image
  with no network that the repro tests fail on behaviour on the base (exit 1, ids in the
  repro file), that the same ids pass with the fix, and that the device tests pass
  (`doctor_code.check_job`), then runs the held-out verify scripts. A base with none closes
  `advised` unless DOCTOR_OFFER_UNVERIFIED (then the check says "offered unverified").
  Anything else closes `failed_checks` (retried within the cap).
- Waiting consults (`raw.retry_at` ahead, or blocked on `docker`) take no
  DOCTOR_MAX_OPEN slot.
- Every close writes the consult's trace as a `doctor_consult` recording
  (`doctor_trace.write`) and names it in `doctor.consult_closed` (`refs.recording`).

Every stage change and its event land in the step's one transaction (the worker commits).
"""

import logging
import shutil
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from functools import partial
from typing import Any

from pydantic import ValidationError
from sqlalchemy import and_, event, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from romulus_cloud import events
from romulus_cloud.adapters.blobstore import BlobStore, build_blobstore
from romulus_cloud.adapters.coder import DEFAULT_IMAGE, CodeDraft, Coder, FakeCoder, Sandbox, redact
from romulus_cloud.adapters.llm import FAKE_MODEL, Llm, LlmConfigError
from romulus_cloud.domain import coder_retry
from romulus_cloud.domain import doctor as rules
from romulus_cloud.ids import ensure_utc, new_id
from romulus_cloud.models import DoctorConsult, DoctorProposal, Incident, Overlay
from romulus_cloud.services import (
    code_lockin,
    doctor_code,
    doctor_evidence,
    doctor_trace,
    fleet,
    proposals,
)
from romulus_cloud.services.code_lockin import test_summary
from romulus_cloud.services.coder_runs import patch_refusal
from romulus_cloud.services.doctor_jobs import Done, Failed, Jobs, Pending, default_jobs
from romulus_cloud.services.doctor_llm import build_doctor_llm
from romulus_cloud.services.doctor_spend import spent_today
from romulus_cloud.settings import Settings, get_settings
from romulus_contracts import IncidentReport, summarize_code_patch, touches_safety
from romulus_contracts.doctor import ConsultRequest

log = logging.getLogger(__name__)

ACTOR = "fleet-doctor"
OPEN, WAITING, CLOSED = "open", "waiting", "closed"
APPLIED = "applied"  # the stage that waits on the device
MAX_JOB_TRIES = 3
OUTPUT_KEYS = (
    "patch_error",
    "exit_code",
    "timed_out",
    "tests_summary",
    "sealed",
    "failed",
    "passed_ids",
    "output_tail",
)
REFUSAL_KINDS = {
    rules.DECLINED: "doctor.declined",
    rules.BLOCKED_OVER_CAP: "doctor.blocked_over_cap",
    rules.GAVE_UP: "doctor.gave_up",
}
DISCARD_WHY = {
    "touches_safety": (
        "The drafted fix touches the safety layer or visor (or its file list can't be "
        "trusted), so it is discarded: never offered, never held for a person (D25)."
    ),
    "safety_paths_unknown": (
        "SAFETY_PATHS_CONFIRMED is false: until Ishaan's K17 list replaces the placeholder "
        "safety paths, every Fleet Doctor fix is discarded (D25)."
    ),
}

_llms: dict[tuple[Any, ...], Llm] = {}
_coders: dict[tuple[Any, ...], Coder] = {}
_warned: set[str] = set()


def doctor_llm(settings: Settings) -> Llm:
    """One Doctor LLM per config for the process (`build_doctor_llm`)."""
    key = (
        settings.doctor_mode,
        settings.llm_provider,
        settings.llm_base_url,
        settings.llm_model,
        settings.llm_api_key.get_secret_value(),
    )
    if key not in _llms:
        _llms[key] = build_doctor_llm(settings)
    return _llms[key]


def doctor_coder(settings: Settings) -> Coder:
    """One Doctor coder per config for the process (`doctor_code.build_doctor_coder`)."""
    key = (
        settings.coder_provider,
        settings.coder_agent,
        settings.coder_image,
        settings.llm_base_url,
        settings.llm_model,
        settings.llm_api_key.get_secret_value(),
        settings.coder_test_cmd,
        settings.coder_step_limit,
        settings.coder_time_limit_s,
        settings.coder_scratch_dir,
    )
    if key not in _coders:
        _coders[key] = doctor_code.build_doctor_coder(settings)
    return _coders[key]


def _warn_once(why: str) -> None:
    """A consult waits on something only an operator can fix: say so once per process."""
    if why not in _warned:
        _warned.add(why)
        log.error("fleet doctor waiting: %s", why)


@dataclass
class _Tick:
    session: Session
    now: datetime
    settings: Settings
    jobs: Jobs
    llm: Llm | None
    coder: Coder | None
    counts: Counter[str]
    sandbox: Sandbox | None = None
    store: BlobStore | None = None
    injected: frozenset[str] = field(default_factory=frozenset)

    def get_llm(self) -> Llm:
        if self.llm is None:
            self.llm = doctor_llm(self.settings)
        return self.llm

    def get_coder(self) -> Coder:
        if self.coder is None:
            self.coder = doctor_coder(self.settings)
        return self.coder

    def get_sandbox(self) -> Sandbox:
        if self.sandbox is None:
            self.sandbox = Sandbox(image=self.settings.coder_image or DEFAULT_IMAGE)
        return self.sandbox

    def get_store(self) -> BlobStore:
        if self.store is None:
            self.store = build_blobstore(self.settings.blob_dir)
        return self.store

    def coder_blocked(self) -> str | None:
        """Why this process can't run the configured coder (`code_lockin.coder_blocker`);
        never for an injected one."""
        return None if "coder" in self.injected else code_lockin.coder_blocker(self.settings)

    def sandbox_blocked(self) -> str | None:
        """Why this process can't run the sandbox check (no `docker` CLI); never for an
        injected sandbox."""
        if "sandbox" in self.injected or shutil.which(self.get_sandbox().docker):
            return None
        return (
            "DOCTOR_CHECK=sandbox, but this worker has no `docker` CLI to run the coder image; "
            "run the worker on the host (`make worker-host`) or set DOCTOR_CHECK=stub"
        )


def tick(
    session: Session,
    now: datetime,
    *,
    settings: Settings | None = None,
    jobs: Jobs | None = None,
    llm: Llm | None = None,
    coder: Coder | None = None,
    sandbox: Sandbox | None = None,
) -> dict[str, int]:
    """One pass over consults → counts of what happened (only non-zero ones).

    `jobs` (`doctor_jobs.Jobs`), `llm` (`adapters.llm.Llm`), `coder` (`adapters.coder.Coder`,
    for DOCTOR_DRAFT=coder) and `sandbox` (`adapters.coder.Sandbox`, for
    DOCTOR_CHECK=sandbox) are injected the way `workorders.sync` takes `naive=`; None means
    the process-wide default built from settings.
    """
    settings = settings or get_settings()
    if settings.doctor_mode not in rules.LIVE_MODES or fleet.is_frozen(session, settings.fleet_id):
        return {}
    injected = frozenset(
        name for name, value in (("coder", coder), ("sandbox", sandbox)) if value is not None
    )
    t = _Tick(
        session,
        now,
        settings,
        jobs or default_jobs(settings),
        llm,
        coder,
        Counter(),
        sandbox=sandbox,
        injected=injected,
    )
    t.counts["opened"] += open_new(session, now, settings=settings)
    for consult in _working(t):
        before = consult.stage
        _advance(t, consult)
        t.counts["advanced"] += consult.stage != before
    for consult in _waiting_on_device(t):
        _sync_applied(t, consult)
    t.counts["expired"] += proposals.expire_due(session, now)
    _follow_up(t)
    return {k: v for k, v in t.counts.items() if v}


# --- events and transitions ---------------------------------------------------------------


def _record(
    session: Session,
    settings: Settings,
    consult: DoctorConsult,
    kind: str,
    why: str,
    data: dict[str, Any] | None = None,
    *,
    now: datetime,
    refs: dict[str, Any] | None = None,
) -> None:
    """One `doctor.*` event about a consult: refs device, incident, consult (+ `refs`);
    `data.incident_kind` always; `fleet_id=settings.fleet_id` (spend is summed per fleet)."""
    incident = session.get(Incident, consult.incident_id)
    events.record(
        session,
        ACTOR,
        "agent",
        kind,
        {
            "device": consult.device_id,
            "incident": consult.incident_id,
            "consult": consult.id,
            **(refs or {}),
        },
        why,
        data={**(data or {}), "incident_kind": incident.kind if incident else None},
        ts=now,
        fleet_id=settings.fleet_id,
    )


def _move(t: _Tick, consult: DoctorConsult, stage: str, status: str = OPEN) -> None:
    consult.stage, consult.status, consult.updated_at = stage, status, t.now
    t.session.flush()


def _close(t: _Tick, consult: DoctorConsult, outcome: str, why: str) -> None:
    """Close with `outcome`, store the consult's trace (`doctor_trace.write`, a
    `doctor_consult` recording) and record `doctor.consult_closed` naming both."""
    consult.stage, consult.status, consult.outcome = CLOSED, CLOSED, outcome
    consult.job_key, consult.updated_at = None, t.now
    t.session.flush()
    incident = t.session.get(Incident, consult.incident_id)
    trace = doctor_trace.write(
        t.session, t.get_store(), consult, incident, outcome, why, t.now, settings=t.settings
    )
    proposal_id = (consult.raw or {}).get("proposal_id")
    refs: dict[str, Any] = {"proposal": proposal_id} if proposal_id else {}
    if trace is not None:
        refs["recording"] = trace.id
    _record(
        t.session,
        t.settings,
        consult,
        "doctor.consult_closed",
        f"Consult closed: {outcome}. {why}",
        {
            "outcome": outcome,
            "why": why,
            "attempt": consult.attempt,
            "trace_recording": trace.id if trace is not None else None,
        },
        now=t.now,
        refs=refs or None,
    )
    t.counts["closed"] += 1


def _consults_used(session: Session, incident_id: str) -> int:
    return (
        session.scalar(
            select(func.count())
            .select_from(DoctorConsult)
            .where(DoctorConsult.incident_id == incident_id)
        )
        or 0
    )


def _give_up(t: _Tick, consult: DoctorConsult, reason: str) -> None:
    used = _consults_used(t.session, consult.incident_id)
    _record(
        t.session,
        t.settings,
        consult,
        "doctor.gave_up",
        f"The Fleet Doctor gave up: {reason}.",
        {"reason": reason, "consults_used": used},
        now=t.now,
    )
    _close(t, consult, rules.GAVE_UP, f"Gave up: {reason}.")


def _refuse(
    t: _Tick,
    consult: DoctorConsult,
    d: rules.Decision,
    spend: rules.Spend,
    estimate_usd: float | None,
    stage: str = "admit",
) -> None:
    """A spend or admit check said no for good: its event (`doctor.declined` /
    `blocked_over_cap` / `gave_up`), then the close."""
    assert d.outcome is not None
    data: dict[str, Any] = {
        rules.DECLINED: {"reason": d.why, "stage": stage, "which": d.which},
        rules.BLOCKED_OVER_CAP: {
            "spent_today_usd": spend.spent_today_usd,
            "cap_usd": spend.cap_usd,
            "estimate_usd": estimate_usd,
            "which": d.which,
            "stage": stage,
        },
        rules.GAVE_UP: {
            "reason": d.why,
            "consults_used": _consults_used(t.session, consult.incident_id),
        },
    }[d.outcome]
    _record(t.session, t.settings, consult, REFUSAL_KINDS[d.outcome], d.why, data, now=t.now)
    _close(t, consult, d.outcome, d.why)


# --- 2. open --------------------------------------------------------------------------------


def _consult_request(incident: Incident) -> ConsultRequest | None:
    """The incident's `consult`, read with the contract's own leniency (`true` is shorthand
    for `{"wanted": true}`, a bad field reads as its default)."""
    raw = incident.raw if isinstance(incident.raw, dict) else {}
    if "consult" not in raw:
        return None
    try:
        report = IncidentReport.model_validate(
            {
                "incident_id": incident.id,
                "occurred_at": incident.occurred_at,
                "kind": incident.kind,
                "consult": raw["consult"],
            }
        )
    except ValidationError:
        return None
    return report.consult


def _auto_ok(incident: Incident) -> bool:
    return incident.resolved is False and (incident.suspected_cause or "unknown").lower() != (
        "hardware"
    )


def _insert(
    session: Session,
    incident: Incident,
    *,
    origin: str,
    trigger: str | None,
    attempt: int,
    now: datetime,
    settings: Settings,
    raw: dict[str, Any] | None = None,
) -> DoctorConsult | None:
    """Insert attempt `attempt`, or None if it exists (the unique (incident, attempt)
    constraint: a racing tick can't open it twice)."""
    consult_id = session.execute(
        pg_insert(DoctorConsult)
        .values(
            id=new_id("cons"),
            fleet_id=settings.fleet_id,
            device_id=incident.device_id,
            incident_id=incident.id,
            origin=origin,
            trigger=trigger,
            attempt=attempt,
            stage="admit",
            status=OPEN,
            case_file={},
            ledger=[],
            draft={},
            checks={},
            cost_usd=0.0,
            created_at=now,
            updated_at=now,
            raw=raw or {},
        )
        .on_conflict_do_nothing(constraint="uq_doctor_consults_incident_attempt")
        .returning(DoctorConsult.id)
    ).scalar()
    return session.get(DoctorConsult, consult_id) if consult_id else None


def open_new(session: Session, now: datetime, *, settings: Settings) -> int:
    """Open attempt 1 for each recent incident that wants a consult and has none. → how many."""
    since = now - timedelta(seconds=settings.doctor_lookback_s)
    asked = func.jsonb_typeof(Incident.raw["consult"]).in_(["object", "boolean"])
    wants = asked
    if settings.doctor_auto_consult:
        cause = func.lower(func.coalesce(Incident.suspected_cause, "unknown"))
        wants = or_(asked, and_(Incident.resolved.is_(False), cause != "hardware"))
    has_consult = select(DoctorConsult.id).where(DoctorConsult.incident_id == Incident.id)
    rows = session.scalars(
        select(Incident)
        .where(
            Incident.fleet_id == settings.fleet_id,
            Incident.received_at >= since,
            wants,
            ~has_consult.exists(),
        )
        .order_by(Incident.received_at, Incident.id)
    ).all()
    opened = 0
    for incident in rows:
        request = _consult_request(incident)
        if request is not None and request.wanted:
            origin, trigger = "device", request.trigger
            why = (
                f"{incident.device_id}'s healer asked the Fleet Doctor to consult on "
                f"{incident.kind} (trigger {trigger or 'not given'})."
            )
        elif settings.doctor_auto_consult and _auto_ok(incident):
            origin, trigger = "cloud", None
            why = (
                f"The Fleet Doctor opened a consult on its own: {incident.device_id}'s "
                f"{incident.kind} is unresolved and not a hardware fault (DOCTOR_AUTO_CONSULT)."
            )
        else:
            continue
        consult = _insert(
            session,
            incident,
            origin=origin,
            trigger=trigger,
            attempt=1,
            now=now,
            settings=settings,
        )
        if consult is None:
            continue
        _record(
            session,
            settings,
            consult,
            "doctor.consult_opened",
            why,
            {"origin": origin, "trigger": trigger, "attempt": 1},
            now=now,
        )
        opened += 1
    return opened


# --- 3. advance -----------------------------------------------------------------------------


def stamp(when: datetime) -> str:
    """A fixed-width UTC ISO timestamp for `consult.raw` (`retry_at`), so SQL can compare it
    as text against `stamp(now)` (`_working`) and nothing has to be cast."""
    return ensure_utc(when).isoformat(timespec="microseconds")


def _working(t: _Tick) -> list[DoctorConsult]:
    """Up to DOCTOR_MAX_OPEN consults that can do something this tick, oldest first. A
    consult waiting out `raw.retry_at` (the coder budget, a coder backoff) or parked behind
    a blocker only an operator can fix (no `docker` for the coder or the sandbox) takes no
    slot, so it can never starve newer consults."""
    s = t.settings
    retry_at = DoctorConsult.raw["retry_at"].astext
    conditions = [
        DoctorConsult.fleet_id == s.fleet_id,
        DoctorConsult.status != CLOSED,
        DoctorConsult.stage != APPLIED,
        or_(retry_at.is_(None), retry_at <= stamp(t.now)),
    ]
    for stage, mode, blocked in (
        ("draft", s.doctor_draft == "coder", t.coder_blocked),
        ("check", s.doctor_check == "sandbox", t.sandbox_blocked),
    ):
        why = blocked() if mode else None
        if why:
            _warn_once(why)
            conditions.append(DoctorConsult.stage != stage)
    return list(
        t.session.scalars(
            select(DoctorConsult)
            .where(*conditions)
            .order_by(DoctorConsult.created_at, DoctorConsult.id)
            .limit(max(s.doctor_max_open, 0))
            .with_for_update(skip_locked=True)
        )
    )


def _waiting_on_device(t: _Tick) -> list[DoctorConsult]:
    return list(
        t.session.scalars(
            select(DoctorConsult)
            .where(
                DoctorConsult.fleet_id == t.settings.fleet_id,
                DoctorConsult.status != CLOSED,
                DoctorConsult.stage == APPLIED,
            )
            .order_by(DoctorConsult.created_at, DoctorConsult.id)
            .with_for_update(skip_locked=True)
        )
    )


def _advance(t: _Tick, consult: DoctorConsult) -> None:
    incident = t.session.get(Incident, consult.incident_id)
    assert incident is not None  # foreign key
    stage = STAGES.get(consult.stage)
    if stage is not None:
        stage(t, consult, incident)


def _spend(t: _Tick) -> rules.Spend:
    s = t.settings
    return rules.Spend(spent_today(t.session, t.now, settings=s), s.doctor_daily_usd_cap)


def _diagnose_estimate(s: Settings) -> float | rules.Decision:
    """The most a diagnose call may cost, or a refusal when the price or token budget is bad
    (a bad price must never count as free: fails closed)."""
    try:
        return rules.diagnose_estimate_usd(s.doctor_diagnose_max_tokens, s.doctor_usd_per_1k_tokens)
    except ValueError as exc:
        return rules.Decision(
            False,
            rules.BLOCKED_OVER_CAP,
            f"Blocked, can't price the Fleet Doctor's calls: {exc}.",
            "spend",
        )


def _admit(t: _Tick, consult: DoctorConsult, incident: Incident) -> None:
    s = t.settings
    spend = _spend(t)
    estimate = _diagnose_estimate(s)
    decision = rules.admit(
        frozen=False,  # the tick returned already if the fleet is frozen
        code_rev=(incident.running or {}).get("code_rev"),
        consults_for_incident=consult.attempt - 1,
        consult_cap=s.doctor_consults_per_incident,
        spend=spend,
        mode=s.doctor_mode,
        estimate_usd=estimate if isinstance(estimate, float) else 0.0,
    )
    if decision.ok and isinstance(estimate, rules.Decision):
        decision = estimate
    if decision.ok:
        _move(t, consult, "gather")
    elif decision.outcome is not None:  # None: held for now (off, frozen); never here
        _refuse(t, consult, decision, spend, estimate if isinstance(estimate, float) else None)


def _gather(t: _Tick, consult: DoctorConsult, incident: Incident) -> None:
    consult.case_file = doctor_evidence.case_file(
        t.session, t.get_store(), consult, incident, settings=t.settings
    )
    _move(t, consult, "diagnose")


def _forget_after_commit(t: _Tick, key: str) -> None:
    """Drop a job's result once the transaction that applied it commits (a rollback keeps
    it, so the next tick re-applies it without paying again)."""
    jobs = t.jobs
    event.listen(t.session, "after_commit", lambda _session: jobs.forget(key), once=True)


def _record_failed_run(
    t: _Tick,
    consult: DoctorConsult,
    stage: str,
    run: int,
    error: str,
    prompt_chars: int,
    billable: bool = True,
) -> None:
    """A run that failed or was lost still sent its prompt: count it toward today's spend at
    an estimate (`len // 4` prompt tokens, no completion), as `doctor.declined` with
    `stage` and `cost_usd`, so the cap can't be dodged by calls that error out. A run that
    calls no model (`billable=False`, the sandbox check) is recorded at $0."""
    exc_type = error.split(":", 1)[0].strip() or "Error"
    error = redact(error, doctor_trace.secrets(t.settings))
    if billable:
        tokens, cost = rules.failed_run_cost_usd(prompt_chars, t.settings.doctor_usd_per_1k_tokens)
        spend = f"its prompt counts toward today's spend at an estimated ${cost:.4f}"
    else:
        tokens, cost = 0, 0.0
        spend = "it called no model, so it costs nothing"
    consult.cost_usd = (consult.cost_usd or 0.0) + cost
    _record(
        t.session,
        t.settings,
        consult,
        "doctor.declined",
        f"The {stage} run {run} failed ({_clip(error, 200)}); {spend}.",
        {
            "reason": f"job failed: {exc_type}",
            "stage": stage,
            "which": "job",
            "error": _clip(error, 500),
            "run": run,
            "prompt_tokens": tokens,
            "completion_tokens": 0,
            "cost_usd": cost,
            "usage_estimated": billable,
        },
        now=t.now,
    )


def _run_job(
    t: _Tick,
    consult: DoctorConsult,
    key: str,
    make: Callable[[], Callable[[], Any]],
    *,
    stage: str,
    prompt_chars: int,
    estimate_usd: float,
    billable: bool = True,
    hold: Callable[[], bool] | None = None,
    on_submit: Callable[[], None] | None = None,
) -> Done | None:
    """Poll `key`; submit it when it was never run, was lost (Missing) or failed. → `Done`,
    or None while pending, held, or once the consult closed.

    - A failed or lost run is counted toward spend (`_record_failed_run`), then after
      `MAX_JOB_TRIES` runs the consult gives up ("job failed").
    - Spend is checked (`estimate_usd`) before **every** submit, the first included, so a
      day's cap reached between runs blocks the retry (`blocked_over_cap`). A job that
      calls no model (`billable=False`) skips the spend check and costs $0 when it fails.
    - `hold()` is asked right before a (re)submit; True keeps the consult waiting without
      submitting or counting a try (the coder budget). `on_submit()` runs after each submit.
    """
    state = t.jobs.poll(key)
    if isinstance(state, Done):
        return state
    if isinstance(state, Pending):
        consult.status = WAITING
        return None
    raw = dict(consult.raw or {})
    tries = dict(raw.get("job_tries") or {})
    used = int(tries.get(key, 0))
    error = ""
    if used:
        error = state.error if isinstance(state, Failed) else "LostJob: the worker lost it"
        if used >= MAX_JOB_TRIES:
            _record_failed_run(t, consult, stage, used, error, prompt_chars, billable)
            _forget_after_commit(t, key)
            _give_up(t, consult, f"job failed: {key} ran {used} times ({error})")
            return None
    if hold is not None and hold():
        return None  # nothing counted: the same state is read again once the hold lifts
    if used:
        _record_failed_run(t, consult, stage, used, error, prompt_chars, billable)
    if billable:
        spend = _spend(t)
        decision = rules.check_spend(spend, estimate_usd=estimate_usd)
        if not decision.ok:
            _forget_after_commit(t, key)
            _refuse(t, consult, decision, spend, estimate_usd, stage=stage)
            return None
    tries[key] = used + 1
    raw["job_tries"] = tries
    if isinstance(state, Failed):
        raw["job_error"] = redact(state.error, doctor_trace.secrets(t.settings))
    consult.raw = raw
    consult.job_key, consult.job_started_at, consult.status = key, t.now, WAITING
    t.jobs.submit(key, make())
    if on_submit is not None:
        on_submit()
    t.session.flush()
    state = t.jobs.poll(key)
    return state if isinstance(state, Done) else None


def _diagnose_job(llm: Llm, system: str, user: str, max_tokens: int, timeout_s: float) -> dict:
    """Off the tick: one LLM call → plain data. Never touches the database."""
    start = time.perf_counter()
    reply = llm.complete(system, user, max_tokens=max_tokens, timeout_s=timeout_s)
    return {
        "text": reply.text,
        "model": reply.model,
        "prompt_tokens": reply.prompt_tokens,
        "completion_tokens": reply.completion_tokens,
        "usage_estimated": reply.usage_estimated,
        "latency_ms": round((time.perf_counter() - start) * 1000, 1),
    }


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _tokens(result: dict[str, Any], prompt_chars: int) -> tuple[int, int, bool]:
    """(prompt, completion, estimated): a count that isn't a whole number ≥ 0 is replaced by
    a `len // 4` estimate (never 0), so a cost can't read as $0."""
    estimated = bool(result.get("usage_estimated"))
    counts = []
    for name, chars in (("prompt_tokens", prompt_chars), ("completion_tokens", None)):
        value = result.get(name)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            text_len = chars if chars is not None else len(str(result.get("text") or ""))
            value, estimated = rules.estimate_tokens(text_len), True
        counts.append(value)
    return counts[0], counts[1], estimated


def _diagnose(t: _Tick, consult: DoctorConsult, incident: Incident) -> None:
    s, key = t.settings, f"diagnose:{consult.id}"
    system, user = rules.diagnose_prompt(consult.case_file or {})

    def make() -> Callable[[], Any]:
        return partial(
            _diagnose_job,
            t.get_llm(),
            system,
            user,
            s.doctor_diagnose_max_tokens,
            s.doctor_diagnose_timeout_s,
        )

    estimate = _diagnose_estimate(s)
    if isinstance(estimate, rules.Decision):
        _refuse(t, consult, estimate, _spend(t), None, stage="diagnose")
        return
    try:
        done = _run_job(
            t,
            consult,
            key,
            make,
            stage="diagnose",
            prompt_chars=len(system) + len(user),
            estimate_usd=estimate,
        )
    except LlmConfigError as exc:
        _give_up(t, consult, f"the Doctor has no LLM: {exc}")
        return
    if done is None:
        return
    result = done.result if isinstance(done.result, dict) else {}
    hyps, parsed_ok = rules.parse_hypotheses(str(result.get("text") or ""))
    hyps, removed = rules.filter_safety(hyps, s.safety_paths)
    prompt_tokens, completion_tokens, estimated = _tokens(result, len(system) + len(user))
    cost = rules.cost_usd(prompt_tokens, completion_tokens, s.doctor_usd_per_1k_tokens)
    model = str(result.get("model") or "")
    stub = s.doctor_mode != "live" or model == FAKE_MODEL
    consult.ledger = hyps
    consult.cost_usd = (consult.cost_usd or 0.0) + cost
    consult.job_key = None
    why = f"The Fleet Doctor diagnosed {incident.kind}: {len(hyps)} hypotheses"
    if hyps:
        why += f", most likely: {_clip(hyps[0]['claim'], 160)}"
    if stub:
        why += " (stub: FakeLlm, no model was called)"
    if removed:
        why += f"; {removed} named a safety path and were dropped"
    if not parsed_ok:
        why += "; the answer wasn't JSON, so its prose is kept as one hypothesis"
    _record(
        t.session,
        s,
        consult,
        "doctor.diagnosed",
        why + ".",
        {
            "hypotheses": hyps,
            "parsed_ok": parsed_ok,
            "removed_for_safety": removed,
            "model": model,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "cost_usd": cost,
            "usage_estimated": estimated,
            "latency_ms": result.get("latency_ms"),
            "stub": stub,
        },
        now=t.now,
    )
    _move(t, consult, "draft")
    _forget_after_commit(t, key)


def _draft(t: _Tick, consult: DoctorConsult, incident: Incident) -> None:
    mode = t.settings.doctor_draft
    if mode == "none":
        _close(
            t,
            consult,
            rules.ADVISED,
            "DOCTOR_DRAFT=none: the consult ends with its hypotheses as advice; no fix drafted.",
        )
        return
    if mode == "coder":
        _draft_coder(t, consult, incident)
        return
    if mode != "fake":
        _close(
            t,
            consult,
            rules.ADVISED,
            f"DOCTOR_DRAFT={mode} is not a draft mode; the consult ends with its hypotheses "
            "as advice.",
        )
        return
    draft = rules.fake_draft({**(consult.case_file or {}), "hypotheses": consult.ledger or []})
    consult.draft = draft
    patch = draft.get("code_patch")
    paths = list(summarize_code_patch(patch).paths) if patch else []
    stub = bool(draft.get("stub"))
    why = f"The Fleet Doctor drafted a fix touching {', '.join(paths) or 'no files'}"
    if stub:
        why += " (stub: DOCTOR_DRAFT=fake adds a note file and changes no behavior)"
    _record(
        t.session,
        t.settings,
        consult,
        "doctor.drafted",
        why + ".",
        {
            "paths": paths,
            "has_repro_test": bool(draft.get("repro_test")),
            "why": draft.get("why") or "",
            "stub": stub,
            "cost_usd": 0.0,
        },
        now=t.now,
    )
    _move(t, consult, "check")


def _when(value: Any) -> datetime | None:
    """An ISO timestamp from `consult.raw`, or None if it isn't one."""
    try:
        return ensure_utc(datetime.fromisoformat(value)) if isinstance(value, str) else None
    except ValueError:
        return None


def _case_summary(case: dict[str, Any], incident: Incident) -> dict[str, Any]:
    """What the coder gets as `incident` (its notes name the id; the task carries the rest).
    Text comes from the case file only, where lines naming `verify/` paths are withheld."""
    facts = case.get("incident") if isinstance(case.get("incident"), dict) else {}
    return {
        "id": incident.id,
        "device_id": incident.device_id,
        "kind": facts.get("kind") or incident.kind,
        "summary": facts.get("summary") or "",
        "explanation": facts.get("agent_reasoning") or "",
    }


def _no_real_coder(t: _Tick) -> str | None:
    """Why the configured coder can't write a fix at all, or None: the pass-through
    `FakeCoder` (CODER_PROVIDER=fake) returns the empty patch it's given, and an
    `Unavailable` coder (settings it can't be built from) fails every run."""
    coder = t.get_coder()
    if isinstance(coder, FakeCoder):
        return (
            "CODER_PROVIDER=fake: the pass-through coder returns the patch it is given and "
            "can't write a reproduction test or a fix"
        )
    if isinstance(coder, doctor_code.Unavailable):
        return f"the coder can't be built ({coder.why})"
    return None


def _blip_cost(t: _Tick, tokens: int | None, prompt_chars: int) -> float:
    """What a transient coder failure cost: $0 when it is known to have spent no tokens, its
    tokens when known, else its prompt at the failed-run estimate."""
    price = t.settings.doctor_usd_per_1k_tokens
    if tokens == 0:
        return 0.0
    if isinstance(tokens, int) and not isinstance(tokens, bool) and tokens > 0:
        return rules.cost_usd(tokens, 0, price)
    return rules.failed_run_cost_usd(prompt_chars, price)[1]


def _coder_blip(
    t: _Tick,
    consult: DoctorConsult,
    key: str,
    outcome: coder_retry.CoderOutcome,
    why: str,
    cost: float,
) -> None:
    """A transient coder failure (`domain.coder_retry`: a rate limit, 429, at capacity, 5xx,
    timeout, no sandbox result, zero tokens or steps). It is not a failed job try and
    doesn't close the consult: it waits in `draft` until `raw.retry_at` (2 min doubling to
    30 min), and the 5th in a row gives up, listing each one. Each records `doctor.declined`
    (`reason: coder_unavailable`, `job`, `attempt`, `no_tokens`): an attempt known to have
    spent 0 tokens is refunded to the hourly coder budget (`doctor_code.coder_budget`)."""
    s = t.settings
    verdict = coder_retry.classify(outcome)
    raw = dict(consult.raw or {})
    blips = [*list(raw.get("coder_blips") or []), _clip(why, 300)]
    attempt = len(blips)
    tries = dict(raw.get("job_tries") or {})
    tries.pop(key, None)  # the next submit is a fresh try, not a failed one
    raw["job_tries"], raw["coder_blips"] = tries, blips
    when = coder_retry.retry_at(attempt, t.now)
    if when is not None:
        raw["retry_at"] = stamp(when)
    consult.raw, consult.job_key = raw, None
    consult.cost_usd = (consult.cost_usd or 0.0) + cost
    later = (
        f"the draft is retried after {when.isoformat()} (attempt {attempt} of "
        f"{coder_retry.MAX_TRANSIENT}); nothing failed"
        if when is not None
        else "that was the last try"
    )
    _record(
        t.session,
        s,
        consult,
        "doctor.declined",
        f"The coding agent was unavailable ({verdict.label}); {later}. {_clip(why, 300)}.",
        {
            "reason": doctor_code.CODER_UNAVAILABLE,
            "stage": "draft",
            "which": "coder_unavailable",
            "job": key,
            "attempt": attempt,
            "label": verdict.label,
            "detail": _clip(why, 500),
            "no_tokens": outcome.tokens == 0,  # a known zero only: None may have spent some
            "retry_at": stamp(when) if when is not None else None,
            "cost_usd": cost,
        },
        now=t.now,
    )
    _forget_after_commit(t, key)
    if when is None:
        listed = "; ".join(f"attempt {i}: {r}" for i, r in enumerate(blips, 1))
        reason = f"the coding agent was unavailable {attempt} times in a row ({listed})"
        _give_up(t, consult, reason)
    else:
        consult.status = WAITING
        t.session.flush()


def _coder_budget_hold(t: _Tick, consult: DoctorConsult) -> bool:
    """True (wait) when CODER_MAX_DRAFTS_PER_HOUR is used up, fleet-wide, by the batch's and
    the Doctor's drafts (`doctor_code.coder_budget`). The consult stays in `draft` with
    `raw.retry_at`, when the budget next has room, and records `doctor.declined` (`which:
    coder_budget`, $0) each time it starts waiting; nothing fails."""
    s = t.settings
    budget = s.coder_max_drafts_per_hour
    started, frees_at = doctor_code.coder_budget(t.session, s.fleet_id, t.now, budget)
    if frees_at is None:
        return False
    consult.raw = {**(consult.raw or {}), "retry_at": stamp(frees_at)}
    consult.status = WAITING
    _record(
        t.session,
        s,
        consult,
        "doctor.declined",
        f"The coder budget is used up: {started} drafts counted in the last hour, fleet-wide "
        f"(CODER_MAX_DRAFTS_PER_HOUR={budget}, shared with the batch). The consult waits in "
        f"draft and tries again at {frees_at.isoformat()}; nothing failed.",
        {
            "reason": "coder budget",
            "stage": "draft",
            "which": "coder_budget",
            "drafts_started": started,
            "budget": budget,
            "retry_at": stamp(frees_at),
            "cost_usd": 0.0,
        },
        now=t.now,
    )
    t.session.flush()
    return True


def _draft_coder(t: _Tick, consult: DoctorConsult, incident: Incident) -> None:
    """DOCTOR_DRAFT=coder: job `draft:{id}` asks the coder for a reproduction test and the
    minimal fix (`domain.doctor.draft_task`) on the base package without `verify/`."""
    s = t.settings
    retry_at = _when((consult.raw or {}).get("retry_at"))
    if retry_at is not None and t.now < retry_at:
        return  # waiting on the coder budget or a backoff; it was said when the wait began
    missing = _no_real_coder(t)
    if missing:
        _close(
            t,
            consult,
            rules.ADVISED,
            f"DOCTOR_DRAFT=coder, but {missing}. No draft is started and nothing is spent; "
            "the hypotheses stand as advice.",
        )
        return
    blocked = t.coder_blocked()
    if blocked:
        _warn_once(blocked)
        return
    key = f"draft:{consult.id}"
    secrets = doctor_trace.secrets(s)
    case = consult.case_file or {}
    base = (incident.running or {}).get("code_rev")
    task = rules.draft_task(
        case,
        consult.ledger or [],
        safety_paths=s.safety_paths,
        test_cmd=s.coder_test_cmd or rules.DEFAULT_TEST_CMD,
    )
    summary = _case_summary(case, incident)
    state = t.jobs.poll(key)
    if isinstance(state, Failed):  # a provider blip is a backoff, not a failed try
        failed = coder_retry.CoderOutcome(False, error=redact(state.error, secrets))
        if coder_retry.classify(failed).transient:
            why = f"the coding agent could not run ({failed.error})"
            _coder_blip(t, consult, key, failed, why, _blip_cost(t, None, len(task)))
            return

    def make() -> Callable[[], Any]:
        files = doctor_code.coder_files(
            doctor_code.package(t.session, t.get_store(), base, s.fleet_id)
        )
        return partial(doctor_code.draft_job, t.get_coder(), files, summary, str(base), task)

    def on_submit() -> None:
        consult.raw = {k: v for k, v in (consult.raw or {}).items() if k != "retry_at"}
        events.record(
            t.session,
            ACTOR,
            "agent",
            "coder.started",  # doctor_code.CODER_STARTED, spelled out for the label scan
            {"device": consult.device_id, "incident": consult.incident_id, "consult": consult.id},
            f"The Fleet Doctor's coding agent started drafting a reproduction test and a fix "
            f"for consult {consult.id} on code {base} (CODER_PROVIDER={s.coder_provider}, a job "
            "off the tick); it counts against CODER_MAX_DRAFTS_PER_HOUR with the batch's.",
            data={
                "job": key,
                "attempt": len((consult.raw or {}).get("coder_blips") or []) + 1,
                "base_code_rev": base,
                "source": "doctor",
                "incident_kind": incident.kind,
            },
            ts=t.now,
            fleet_id=s.fleet_id,
        )

    try:
        done = _run_job(
            t,
            consult,
            key,
            make,
            stage="draft",
            prompt_chars=len(task),
            estimate_usd=s.doctor_draft_estimate_usd,
            hold=lambda: _coder_budget_hold(t, consult),
            on_submit=on_submit,
        )
    except doctor_code.NoPackage as exc:
        _close(
            t,
            consult,
            rules.ADVISED,
            f"The coder has no base tree: {exc}. The hypotheses stand as advice.",
        )
        return
    if done is None:
        return
    draft = done.result
    if isinstance(draft, CodeDraft) and not draft.passed:
        outcome = doctor_code.outcome(draft, secrets)
        if coder_retry.classify(outcome).transient:
            why = f"the coding agent stopped ({outcome.exit_status or 'no exit status'}"
            why += f": {outcome.error})" if outcome.error else ")"
            _coder_blip(t, consult, key, outcome, why, _blip_cost(t, draft.tokens, len(task)))
            return
    _apply_draft(t, consult, incident, draft, len(task))
    _forget_after_commit(t, key)


def _draft_cost(t: _Tick, draft: CodeDraft) -> tuple[int | None, int | None, float, bool]:
    """(prompt tokens, completion tokens, cost, estimated): the coder's own token counts
    (its notes' `tokens N in / M out`, else `CodeDraft.tokens`, the total), else
    DOCTOR_DRAFT_ESTIMATE_USD (never $0 by accident)."""
    s = t.settings
    usage = rules.coder_usage(draft.notes)
    if usage is None and isinstance(draft.tokens, int) and not isinstance(draft.tokens, bool):
        usage = (draft.tokens, 0)
    if usage is not None:
        try:
            return usage[0], usage[1], rules.cost_usd(*usage, s.doctor_usd_per_1k_tokens), False
        except ValueError:
            pass
    # The spend check before the submit refused a bad estimate, so this is a real amount.
    return None, None, max(float(s.doctor_draft_estimate_usd), 0.0), True


def _apply_draft(
    t: _Tick, consult: DoctorConsult, incident: Incident, result: Any, task_chars: int
) -> None:
    """The coder's `CodeDraft` → `consult.draft` `{code_patch, repro_test, why, stub: False}`
    and `doctor.drafted` (with its cost); then `check`, or `failed_checks` when the draft
    can't be checked (no change, a refused diff, its own tests failing, no repro test)."""
    s = t.settings
    secrets = doctor_trace.secrets(s)
    if not isinstance(result, CodeDraft):
        kind = type(result).__name__
        result = CodeDraft("", "", False, f"the coder returned a {kind}, not a CodeDraft")
    diff = redact(result.diff or "", secrets)
    has_diff = bool(diff.strip())
    paths = list(summarize_code_patch(diff).paths) if has_diff else []
    repro = rules.find_repro_test(diff) if has_diff else None
    prompt_tokens, completion_tokens, cost, estimated = _draft_cost(t, result)
    hyps = consult.ledger or []
    top = hyps[0].get("claim") if hyps and isinstance(hyps[0], dict) else None
    why = (
        f"Drafted by the coding agent for the top hypothesis: {_clip(str(top), 300)}"
        if top
        else "Drafted by the coding agent from the case file."
    )
    tests = test_summary(redact(result.test_output or "", secrets))
    consult.draft = {
        "code_patch": diff if has_diff else None,
        "repro_test": repro,
        "why": why,
        "stub": False,
        "coder_passed": bool(result.passed),
        "flags": [redact(f, secrets) for f in result.flags],
        "notes": _clip(redact(result.notes or "", secrets), 2000),
        "coder_tests": tests,
    }
    consult.cost_usd = (consult.cost_usd or 0.0) + cost
    consult.job_key = None
    refused = patch_refusal(diff) if has_diff else None
    screened = rules.screen_fix(diff) if has_diff and not refused else []
    if not has_diff:
        failure = "the coding agent changed nothing"
    elif refused:
        failure = f"the coding agent's diff is refused: {refused}"
    elif screened:
        failure = f"the fix is refused before any run: {'; '.join(screened)}"
    elif not result.passed:
        failure = f"the coding agent's own test run did not pass ({tests})"
    elif repro is None:
        failure = (
            f"no repro test: the draft adds no test file under {rules.REPRO_DIR}/, so nothing "
            "can show the fault before the fix"
        )
    else:
        failure = None
    where = ", ".join(paths) or "no files"
    test_part = f" with reproduction test {repro}" if repro else ", without a reproduction test"
    estimate_note = " (cost estimated: the coder reported no tokens)" if estimated else ""
    _record(
        t.session,
        s,
        consult,
        "doctor.drafted",
        f"The Fleet Doctor's coding agent drafted a fix touching {where}{test_part}; "
        f"${cost:.4f}{estimate_note}.",
        {
            "paths": paths,
            "has_repro_test": bool(repro),
            "repro_test": repro,
            "why": why,
            "stub": False,
            "coder_passed": bool(result.passed),
            "flags": consult.draft["flags"],
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "cost_usd": cost,
            "usage_estimated": estimated,
            "task_chars": task_chars,
            "base_code_rev": (incident.running or {}).get("code_rev"),
        },
        now=t.now,
    )
    if failure:
        _close(t, consult, rules.FAILED_CHECKS, f"The draft can't be checked: {failure}.")
    else:
        _move(t, consult, "check")


def _safety_refusal(patch: str | None, settings: Settings) -> str | None:
    """Why a patch may not be offered (D25), or None. Fails closed: `touches_safety` is True
    for any patch whose file list can't be trusted."""
    if touches_safety(patch, settings.safety_paths):
        return "touches_safety"
    if not settings.safety_paths_confirmed:
        return "safety_paths_unknown"
    return None


def _discard(t: _Tick, consult: DoctorConsult, reason: str) -> None:
    patch = (consult.draft or {}).get("code_patch")
    paths = list(summarize_code_patch(patch).paths) if patch else []
    why = DISCARD_WHY[reason]
    _record(
        t.session,
        t.settings,
        consult,
        "doctor.proposal_discarded",
        why,
        {"reason": reason, "paths": paths},
        now=t.now,
    )
    _close(t, consult, rules.DISCARDED, why)


def _check(t: _Tick, consult: DoctorConsult, incident: Incident) -> None:
    patch = (consult.draft or {}).get("code_patch")
    if not isinstance(patch, str) or not patch.strip():
        _close(t, consult, rules.ADVISED, "The draft has no patch; the hypotheses stand as advice.")
        return
    refusal = _safety_refusal(patch, t.settings)
    if refusal:
        _discard(t, consult, refusal)
        return
    if t.settings.doctor_check == "sandbox":
        _check_sandbox(t, consult, incident, patch)
        return
    if t.settings.doctor_check != "stub":
        _close(
            t,
            consult,
            rules.ADVISED,
            f"DOCTOR_CHECK={t.settings.doctor_check} is not a check mode; nothing is offered, "
            "and the hypotheses stand as advice.",
        )
        return
    consult.checks = {"stub": True}
    summary = "check: stub, passed by default"
    _record(
        t.session,
        t.settings,
        consult,
        "doctor.checked",
        f"{summary} (DOCTOR_CHECK=stub): no sandbox ran a repro test or the device tests.",
        {
            "passed": True,
            "stub": True,
            "fails_before": None,
            "passes_after": None,
            "device_tests": None,
            "touches_safety": False,
            "summary": summary,
        },
        now=t.now,
    )
    _move(t, consult, "offer")


def _run_view(run: Any) -> dict[str, Any] | None:
    """A replay's outputs for the `doctor.checked` event (tails only, already redacted)."""
    return {k: run.get(k) for k in OUTPUT_KEYS} if isinstance(run, dict) else None


def _apply_verdict(
    t: _Tick, consult: DoctorConsult, repro: str, verdict: dict[str, Any], result: dict[str, Any]
) -> None:
    """`consult.checks` and `doctor.checked` from a verdict; then `offer` or `failed_checks`."""
    consult.checks = {
        "stub": False,
        "fails_before": verdict["fails_before"],
        "passes_after": verdict["passes_after"],
        "device_tests": verdict["device_tests"],
        "repro_test": repro,
        "repro_ids": verdict["repro_ids"],
        "verify": verdict["verify"],
        "summary": verdict["summary"],
    }
    consult.job_key = None
    held = result.get("verify") if isinstance(result.get("verify"), dict) else None
    _record(
        t.session,
        t.settings,
        consult,
        "doctor.checked",
        f"check: {'passed' if verdict['passed'] else 'failed'} in the sandbox. "
        f"{verdict['reason'] or verdict['summary']}.",
        {
            "passed": verdict["passed"],
            "stub": False,
            "fails_before": verdict["fails_before"],
            "passes_after": verdict["passes_after"],
            "device_tests": verdict["device_tests"],
            "touches_safety": False,
            "summary": verdict["summary"],
            "reason": verdict["reason"],
            "repro_test": repro,
            "repro_ids": verdict["repro_ids"],
            "flags": verdict["flags"],
            "before": _run_view(result.get("before")),
            "after_repro": _run_view(result.get("after_repro")),
            "after": _run_view(result.get("after")),
            # The held-out run: its state and counts only, never its output.
            "verify": verdict["verify"],
            "verify_counts": (
                {k: held.get(k) for k in ("tests", "passed_count", "skipped", "timed_out")}
                if held and held.get("available")
                else None
            ),
        },
        now=t.now,
    )
    if verdict["passed"]:
        _move(t, consult, "offer")
    elif verdict["outcome"] == rules.ADVISED:  # no verify/ scripts and no opt-in: no retry
        _close(
            t,
            consult,
            rules.ADVISED,
            f"The fix passed the repro and device-test checks, but nothing is offered: "
            f"{verdict['reason']}. The hypotheses stand as advice.",
        )
    else:
        _close(t, consult, rules.FAILED_CHECKS, f"The fix failed its checks: {verdict['reason']}.")


def _check_sandbox(t: _Tick, consult: DoctorConsult, incident: Incident, patch: str) -> None:
    """DOCTOR_CHECK=sandbox: the patch may not tamper with existing tests
    (`domain.doctor.tampered_tests`, before any sandbox run); then job `check:{id}`
    (`doctor_code.check_job`) proves in the coder image, with no network, that the repro
    tests fail on behaviour on the base, that the same test ids pass with the fix, and that
    the device tests pass (`domain.doctor.check_verdict`), then runs the held-out verify
    scripts. Pass → `offer`; a base with no verify scripts → `advised` unless
    DOCTOR_OFFER_UNVERIFIED; anything else → `failed_checks` (retried within the cap)."""
    s = t.settings
    repro = (consult.draft or {}).get("repro_test")
    if (
        not isinstance(repro, str)
        or not rules.is_test_path(repro)
        or not rules.split_patch(patch, repro).strip()
    ):
        _close(
            t,
            consult,
            rules.FAILED_CHECKS,
            "The fix can't be checked: no repro test inside its patch, so nothing can show "
            "the fault before the fix.",
        )
        return
    if not s.coder_test_cmd.strip():
        _close(
            t,
            consult,
            rules.ADVISED,
            "CODER_TEST_CMD is empty, so the sandbox can't run the repro test or the device "
            "tests; nothing is offered, and the hypotheses stand as advice.",
        )
        return
    screened = rules.screen_fix(patch)
    if screened:  # decided from the patch alone (again: a draft is screened too)
        verdict = rules.check_verdict(None, None, None, repro, screened)
        _apply_verdict(t, consult, repro, verdict, {})
        return
    blocked = t.sandbox_blocked()
    if blocked:
        _warn_once(blocked)
        return
    key = f"check:{consult.id}"
    base = (incident.running or {}).get("code_rev")

    def make() -> Callable[[], Any]:
        code, held_out = doctor_code.package_parts(t.session, t.get_store(), base, s.fleet_id)
        return partial(
            doctor_code.check_job,
            t.get_sandbox(),
            doctor_code.coder_files(code),
            patch,
            repro,
            test_cmd=s.coder_test_cmd,
            timeout_s=s.coder_replay_timeout_s,
            scratch_dir=s.coder_scratch_dir or None,
            secrets=doctor_trace.secrets(s),
            verify_files=held_out,
        )

    try:
        done = _run_job(
            t, consult, key, make, stage="check", prompt_chars=0, estimate_usd=0.0, billable=False
        )
    except doctor_code.NoPackage as exc:
        _close(
            t,
            consult,
            rules.ADVISED,
            f"The sandbox has no base tree: {exc}. Nothing is offered, and the hypotheses "
            "stand as advice.",
        )
        return
    if done is None:
        return
    result = done.result if isinstance(done.result, dict) else {}
    verdict = rules.check_verdict(
        result.get("before"),
        result.get("after_repro"),
        result.get("after"),
        repro,
        verify=result.get("verify"),
        offer_unverified=s.doctor_offer_unverified,
    )
    _apply_verdict(t, consult, repro, verdict, result)
    _forget_after_commit(t, key)


def _offer(t: _Tick, consult: DoctorConsult, incident: Incident) -> None:
    checks, draft = consult.checks or {}, consult.draft or {}
    # A stub check may only carry a stub draft: a real patch is never offered unchecked.
    if checks.get("stub") and not (t.settings.doctor_offer_stub_checked and draft.get("stub")):
        why = (
            "The fix's check is a stub and DOCTOR_OFFER_STUB_CHECKED is off"
            if not t.settings.doctor_offer_stub_checked
            else "The fix's check is a stub but its patch is real, and a real patch is never "
            "offered on a stub check"
        )
        _close(
            t,
            consult,
            rules.ADVISED,
            f"{why}, so nothing is offered; the hypotheses stand as advice.",
        )
        return
    refusal = _safety_refusal(draft.get("code_patch"), t.settings)  # again, as it's offered
    if refusal:
        _discard(t, consult, refusal)
        return
    p = proposals.offer(t.session, consult, draft, checks, t.now, settings=t.settings)
    consult.raw = {**(consult.raw or {}), "proposal_id": p.id}
    _move(t, consult, APPLIED, WAITING)


STAGES: dict[str, Callable[[_Tick, DoctorConsult, Incident], None]] = {
    "admit": _admit,
    "gather": _gather,
    "diagnose": _diagnose,
    "draft": _draft,
    "check": _check,
    "offer": _offer,
}


def _applied_why(p: DoctorProposal, overlay: Overlay | None, outcome: str) -> str:
    reason = (overlay.status_reason if overlay else None) or p.result_reason or "no reason given"
    device, ov = p.device_id, p.overlay_id
    return {
        rules.HELD: f"{device} kept the Fleet Doctor's fix as overlay {ov}; lock-in takes it "
        "from here.",
        rules.REVERTED: f"Overlay {ov} with the Fleet Doctor's fix reverted on {device}: {reason}.",
        rules.FAILED_VERIFY: f"{device} applied the fix, but it failed verify and rolled back: "
        f"{reason}.",
        rules.STALE: f"{device} moved on: the fix's base no longer matches what it runs "
        f"({reason}).",
        rules.REJECTED: f"{device} rejected the fix: {reason}.",
        rules.EXPIRED: f"{device} never applied the fix before it expired.",
    }[outcome]


def _sync_applied(t: _Tick, consult: DoctorConsult) -> None:
    """A consult whose proposal is out: close it once the proposal or its overlay says how
    it ended (`domain.doctor.applied_outcome`)."""
    proposal_id = (consult.raw or {}).get("proposal_id")
    p = t.session.get(DoctorProposal, proposal_id) if proposal_id else None
    if p is None:
        _give_up(t, consult, "its proposal is missing")
        return
    overlay = t.session.get(Overlay, (p.device_id, p.overlay_id)) if p.overlay_id else None
    if overlay is not None and (overlay.raw or {}).get("proposal_id") != p.id:
        overlay = None  # not this proposal's overlay: its status says nothing about the fix
    outcome = rules.applied_outcome(p.status, overlay.status if overlay else None)
    if outcome is None:
        return
    if p.status == "applied" and outcome in (rules.HELD, rules.REVERTED):
        p.status, p.updated_at = outcome, t.now
    _close(t, consult, outcome, _applied_why(p, overlay, outcome))


# --- 5. follow up -----------------------------------------------------------------------------


def _follow_up(t: _Tick) -> None:
    """Retry or give up after a consult closed `failed_checks`, `reverted` or
    `failed_verify` (`domain.doctor.after_close`). `raw.next` marks it done, once."""
    s = t.settings
    rows = t.session.scalars(
        select(DoctorConsult)
        .where(
            DoctorConsult.fleet_id == s.fleet_id,
            DoctorConsult.status == CLOSED,
            DoctorConsult.outcome.in_(sorted(rules.RETRY_OUTCOMES)),
            DoctorConsult.raw["next"].astext.is_(None),
        )
        .order_by(DoctorConsult.updated_at, DoctorConsult.id)
        .with_for_update(skip_locked=True)
    ).all()
    for c in rows:
        used = _consults_used(t.session, c.incident_id)
        nxt = rules.after_close(c.outcome, used, s.doctor_consults_per_incident)
        c.raw = {**(c.raw or {}), "next": nxt}
        t.session.flush()
        if nxt == "retry":
            incident = t.session.get(Incident, c.incident_id)
            assert incident is not None
            new = _insert(
                t.session,
                incident,
                origin=c.origin,
                trigger=c.trigger,
                attempt=used + 1,
                now=t.now,
                settings=s,
                raw={"retry_of": c.id},
            )
            if new is None:
                continue
            _record(
                t.session,
                s,
                new,
                "doctor.consult_opened",
                f"Attempt {new.attempt}: the last consult ended {c.outcome}, and the incident "
                f"has used {used} of its {s.doctor_consults_per_incident} consults.",
                {
                    "origin": new.origin,
                    "trigger": new.trigger,
                    "attempt": new.attempt,
                    "retry_of": c.id,
                },
                now=t.now,
            )
            t.counts["retried"] += 1
        elif nxt == rules.GAVE_UP:
            reason = (
                f"the last consult ended {c.outcome} and the incident has used {used} of its "
                f"{s.doctor_consults_per_incident} consults"
            )
            _record(
                t.session,
                s,
                c,
                "doctor.gave_up",
                f"The Fleet Doctor gave up: {reason}.",
                {"reason": reason, "consults_used": used},
                now=t.now,
            )
            t.counts["gave_up"] += 1
