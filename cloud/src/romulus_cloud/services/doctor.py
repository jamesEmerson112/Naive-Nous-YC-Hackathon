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

**Slow work** (the diagnose LLM call; later the coder and sandbox) runs off the tick as a
job (`services/doctor_jobs`): the tick submits, polls on later ticks and applies the result
in its own transaction, then forgets the key once that transaction commits. A lost job
(worker restart) is resubmitted; after 3 tries the consult gives up.

Every stage change and its event land in the step's one transaction (the worker commits).
"""

import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import partial
from typing import Any

from pydantic import ValidationError
from sqlalchemy import and_, event, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from romulus_cloud import events
from romulus_cloud.adapters.blobstore import build_blobstore
from romulus_cloud.adapters.llm import FAKE_MODEL, Llm, LlmConfigError
from romulus_cloud.domain import doctor as rules
from romulus_cloud.ids import new_id
from romulus_cloud.models import DoctorConsult, DoctorProposal, Incident, Overlay
from romulus_cloud.services import doctor_evidence, fleet, proposals
from romulus_cloud.services.doctor_jobs import Done, Failed, Jobs, Pending, default_jobs
from romulus_cloud.services.doctor_llm import build_doctor_llm
from romulus_cloud.services.doctor_spend import spent_today
from romulus_cloud.settings import Settings, get_settings
from romulus_contracts import IncidentReport, summarize_code_patch, touches_safety
from romulus_contracts.doctor import ConsultRequest

ACTOR = "fleet-doctor"
OPEN, WAITING, CLOSED = "open", "waiting", "closed"
APPLIED = "applied"  # the stage that waits on the device
MAX_JOB_TRIES = 3
M8B = "waits for M8b (PR 7)"
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


@dataclass
class _Tick:
    session: Session
    now: datetime
    settings: Settings
    jobs: Jobs
    llm: Llm | None
    coder: Any
    counts: Counter[str]

    def get_llm(self) -> Llm:
        if self.llm is None:
            self.llm = doctor_llm(self.settings)
        return self.llm


def tick(
    session: Session,
    now: datetime,
    *,
    settings: Settings | None = None,
    jobs: Jobs | None = None,
    llm: Llm | None = None,
    coder: Any = None,
) -> dict[str, int]:
    """One pass over consults → counts of what happened (only non-zero ones).

    `jobs` (`doctor_jobs.Jobs`), `llm` (`adapters.llm.Llm`) and `coder` are injected the way
    `workorders.sync` takes `naive=`; None means the process-wide default. `coder` is unused
    until DOCTOR_DRAFT=coder lands with M8b (PR 7).
    """
    settings = settings or get_settings()
    if settings.doctor_mode not in rules.LIVE_MODES or fleet.is_frozen(session, settings.fleet_id):
        return {}
    t = _Tick(session, now, settings, jobs or default_jobs(settings), llm, coder, Counter())
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
    consult.stage, consult.status, consult.outcome = CLOSED, CLOSED, outcome
    consult.job_key, consult.updated_at = None, t.now
    t.session.flush()
    proposal_id = (consult.raw or {}).get("proposal_id")
    _record(
        t.session,
        t.settings,
        consult,
        "doctor.consult_closed",
        f"Consult closed: {outcome}. {why}",
        {"outcome": outcome, "why": why, "attempt": consult.attempt},
        now=t.now,
        refs={"proposal": proposal_id} if proposal_id else None,
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


def _working(t: _Tick) -> list[DoctorConsult]:
    return list(
        t.session.scalars(
            select(DoctorConsult)
            .where(
                DoctorConsult.fleet_id == t.settings.fleet_id,
                DoctorConsult.status != CLOSED,
                DoctorConsult.stage != APPLIED,
            )
            .order_by(DoctorConsult.created_at, DoctorConsult.id)
            .limit(max(t.settings.doctor_max_open, 0))
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
    store = build_blobstore(t.settings.blob_dir)
    consult.case_file = doctor_evidence.case_file(
        t.session, store, consult, incident, settings=t.settings
    )
    _move(t, consult, "diagnose")


def _forget_after_commit(t: _Tick, key: str) -> None:
    """Drop a job's result once the transaction that applied it commits (a rollback keeps
    it, so the next tick re-applies it without paying again)."""
    jobs = t.jobs
    event.listen(t.session, "after_commit", lambda _session: jobs.forget(key), once=True)


def _record_failed_run(
    t: _Tick, consult: DoctorConsult, stage: str, run: int, error: str, prompt_chars: int
) -> None:
    """A run that failed or was lost still sent its prompt: count it toward today's spend at
    an estimate (`len // 4` prompt tokens, no completion), as `doctor.declined` with
    `stage` and `cost_usd`, so the cap can't be dodged by calls that error out."""
    exc_type = error.split(":", 1)[0].strip() or "Error"
    tokens, cost = rules.failed_run_cost_usd(prompt_chars, t.settings.doctor_usd_per_1k_tokens)
    consult.cost_usd = (consult.cost_usd or 0.0) + cost
    _record(
        t.session,
        t.settings,
        consult,
        "doctor.declined",
        f"The {stage} run {run} failed ({_clip(error, 200)}); its prompt counts toward today's "
        f"spend at an estimated ${cost:.4f}.",
        {
            "reason": f"job failed: {exc_type}",
            "stage": stage,
            "which": "job",
            "error": _clip(error, 500),
            "run": run,
            "prompt_tokens": tokens,
            "completion_tokens": 0,
            "cost_usd": cost,
            "usage_estimated": True,
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
) -> Done | None:
    """Poll `key`; submit it when it was never run, was lost (Missing) or failed. → `Done`,
    or None while pending, or once the consult closed.

    - A failed or lost run is counted toward spend (`_record_failed_run`), then after
      `MAX_JOB_TRIES` runs the consult gives up ("job failed").
    - Spend is checked (`estimate_usd`) before **every** submit, the first included, so a
      day's cap reached between runs blocks the retry (`blocked_over_cap`).
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
    if used:
        error = state.error if isinstance(state, Failed) else "LostJob: the worker lost it"
        _record_failed_run(t, consult, stage, used, error, prompt_chars)
        if used >= MAX_JOB_TRIES:
            _forget_after_commit(t, key)
            _give_up(t, consult, f"job failed: {key} ran {used} times ({error})")
            return None
    spend = _spend(t)
    decision = rules.check_spend(spend, estimate_usd=estimate_usd)
    if not decision.ok:
        _forget_after_commit(t, key)
        _refuse(t, consult, decision, spend, estimate_usd, stage=stage)
        return None
    tries[key] = used + 1
    raw["job_tries"] = tries
    if isinstance(state, Failed):
        raw["job_error"] = state.error
    consult.raw = raw
    consult.job_key, consult.job_started_at, consult.status = key, t.now, WAITING
    t.jobs.submit(key, make())
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
    if mode != "fake":
        _close(
            t,
            consult,
            rules.ADVISED,
            f"doctor_draft={mode} {M8B}; the consult ends with its hypotheses as advice.",
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
    if t.settings.doctor_check != "stub":
        _close(
            t,
            consult,
            rules.ADVISED,
            f"doctor_check={t.settings.doctor_check} {M8B}; nothing is offered, and the "
            "hypotheses stand as advice.",
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
