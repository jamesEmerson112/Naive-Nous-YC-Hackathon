"""Fleet Doctor L1 (advise): the logic behind `POST /v1/devices/{id}/doctor/ask`
(docs/fleet-doctor/interfaces.md §3, §6.6; architecture §4).

The healer asks mid-heal and gets back at most ~300 tokens of hypotheses, each with how to
check it, never a patch. **No session is open during the LLM call (FR11):**
1. a short session checks the device, the mode, `frozen`, the LLM config, the caps and the
   day's spend. A refusal records its event (deduped) and raises; an admitted ask records
   `doctor.asked` and commits **before** the call, so every ask that reaches the LLM counts
   toward the caps even if what follows crashes;
2. the LLM call runs with no session, under `min(DOCTOR_ASK_TIMEOUT_S, budget_s - 2)`;
3. a second short session records `doctor.advised`, or `doctor.declined` when the call (or
   reading its reply) failed in any way, and commits;
4. the advice is returned.

So a slow model never pins a database connection. Two asks racing through step 1 can still
both pass a cap check, but the window is a few milliseconds, not the LLM call *(inferred)*.

This service owns its two sessions and commits each (the FR11 exception to "services never
commit"): the route can't hold one session across the call.

**Spend:** a failed call may still have cost money, so `doctor.declined` carries an estimated
`cost_usd` (the prompt's `len // 4` tokens, `usage_estimated: true`) and the cap sees it.

**Caps:** asks about an incident count against it (DOCTOR_ASKS_PER_INCIDENT). An ask is exempt
from the device's daily cap (DOCTOR_ASKS_PER_DEVICE_DAY) only when its incident is already
reported by this device, so made-up incident ids can't dodge both caps; `doctor.asked` says
which with `counts_toward_device_day`. Refusals write at most one event per device, incident
(or none) and reason per day; later ones still answer, silently.

An ask has no table: it is its events, all with `refs.ask` (and `refs.incident` whenever the
device named one, reported yet or not). The device treats any non-200 as "no answer", so every
refusal is an `ApiError`: 404 unknown_device, 503 doctor_off, 409 fleet_frozen,
502 doctor_unavailable (LLM unconfigured or failed), 429 doctor_cap, 504 doctor_timeout.
"""

import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from romulus_cloud import events
from romulus_cloud.adapters.llm import Llm, LlmReply, LlmTimeout, estimate_tokens
from romulus_cloud.domain.doctor import (
    BLOCKED_OVER_CAP,
    Decision,
    Spend,
    admit_ask,
    ask_prompt,
    cost_usd,
    day_start,
    filter_safety,
    parse_hypotheses,
)
from romulus_cloud.errors import ApiError
from romulus_cloud.ids import new_id
from romulus_cloud.log import log_line
from romulus_cloud.models import Event, Incident
from romulus_cloud.services import fleet
from romulus_cloud.services.devices import get_device
from romulus_cloud.services.doctor_spend import spent_today
from romulus_cloud.settings import Settings
from romulus_contracts import DoctorAdvice, DoctorAsk, Hypothesis

ACTOR = "fleet-doctor"
STAGE = "ask"
QUESTION_CHARS = 200  # of the device's question kept on `doctor.asked`
TRIGGER_CHARS = 40


@dataclass(frozen=True)
class Unconfigured:
    """Stands in for a live LLM whose settings `build_doctor_llm` refused (`why`): the ask
    answers 502 and the timeline says why (api/doctor.get_doctor_llm)."""

    why: str
    model: str = ""


@dataclass(frozen=True)
class _Ask:
    device_id: str
    incident_id: str | None
    refs: dict[str, str]
    now: datetime
    settings: Settings


def ask(
    sessions: sessionmaker,
    device_id: str,
    body: DoctorAsk,
    raw: dict[str, Any],
    now: datetime,
    *,
    settings: Settings,
    llm: Llm | Unconfigured | None,
) -> DoctorAdvice:
    """Answer one ask with hypotheses, or raise `ApiError` (module docstring).

    `llm` is None only when the Doctor is off, and `Unconfigured` when live settings are
    unusable. `raw` is the verbatim body; only its `contract_version` is kept (the evidence
    itself is too big for an event).
    """
    incident_id = str(body.incident_id) if body.incident_id else None
    refs = {"device": device_id, "ask": new_id("ask")}
    if incident_id:
        refs["incident"] = incident_id
    a = _Ask(device_id, incident_id, refs, now, settings)
    system, user = ask_prompt(body.model_dump(mode="json"))
    timeout_s = min(settings.doctor_ask_timeout_s, body.budget_s - 2)
    trigger = body.trigger[:TRIGGER_CHARS]
    asked = {
        "trigger": trigger,
        "question": body.question[:QUESTION_CHARS],
        "budget_s": body.budget_s,
        "timeout_s": timeout_s,
        "contract_version": raw.get("contract_version"),
    }

    # 1. Short session: admit (or refuse) and record `doctor.asked`, committed before the call.
    with sessions() as session:
        incident_kind = _admit(session, a, llm, asked, trigger)
    assert llm is not None and not isinstance(llm, Unconfigured)  # _admit refused otherwise
    kind_data = {"incident_kind": incident_kind} if incident_kind else {}

    # 2. The LLM call, with no session open. Any failure, here or reading the reply, is
    #    "no answer" with a `doctor.declined`, never a 500 with no event.
    started = time.perf_counter()
    try:
        reply = llm.complete(
            system, user, max_tokens=settings.doctor_ask_max_tokens, timeout_s=timeout_s
        )
        hypotheses, advised = _advice(reply, settings)
        failure = None
    except Exception as exc:  # every failure must leave a declined event
        failure = exc
    latency_ms = round((time.perf_counter() - started) * 1000)

    # 3. Short session: record what happened.
    with sessions() as session:
        if failure is None:
            advised |= {"latency_ms": latency_ms, **kind_data}
            _record(session, a, "doctor.advised", _advised_why(advised), advised)
        else:
            _decline(session, a, failure, timeout_s, system + user, llm, latency_ms, kind_data)
        session.commit()

    # 4. Answer.
    if isinstance(failure, LlmTimeout):
        raise ApiError(
            504,
            "doctor_timeout",
            f"The Fleet Doctor's LLM didn't answer within {timeout_s:g} s; carry on without it.",
        )
    if failure is not None:
        raise ApiError(
            502,
            "doctor_unavailable",
            f"The Fleet Doctor's LLM failed ({type(failure).__name__}); carry on without it.",
        )
    return DoctorAdvice(
        ask_id=refs["ask"],
        hypotheses=[Hypothesis(**h) for h in hypotheses],
        model=advised["model"],
    )


def _admit(
    session: Session,
    a: _Ask,
    llm: Llm | Unconfigured | None,
    asked: dict[str, Any],
    trigger: str,
) -> str | None:
    """Step 1, in order: 404 unknown device, 503 off, 409 frozen, 502 LLM unconfigured, then
    the caps and spend (429). Admitted: records `doctor.asked`, commits, and returns the
    incident's kind if this device has reported it."""
    settings = a.settings
    get_device(session, a.device_id)  # 404 unknown_device
    if settings.doctor_mode == "off" or llm is None:
        raise ApiError(
            503,
            "doctor_off",
            "The Fleet Doctor is off (DOCTOR_MODE=off); carry on without advice.",
        )
    if fleet.is_frozen(session, settings.fleet_id):
        raise ApiError(
            409,
            "fleet_frozen",
            "The fleet is frozen (kill switch), so the Fleet Doctor gives no advice.",
        )
    incident = session.get(Incident, a.incident_id) if a.incident_id else None
    # Only an incident this device already reported exempts the ask from the device-day cap.
    owned = incident is not None and incident.device_id == a.device_id
    kind_data = {"incident_kind": incident.kind} if owned else {}

    if isinstance(llm, Unconfigured):
        _record_once(
            session,
            a,
            "doctor.declined",
            f"The Fleet Doctor's live LLM isn't configured ({llm.why}), so asks get no answer.",
            {"reason": "llm unconfigured", "stage": STAGE, "error": llm.why, **kind_data},
            key={"reason": "llm unconfigured"},
        )
        session.commit()
        raise ApiError(
            502, "doctor_unavailable", f"The Fleet Doctor's LLM isn't configured: {llm.why}"
        )

    for_incident = _count_asks(session, a, {"incident": a.incident_id}) if a.incident_id else None
    for_device_today = None if owned else _count_asks(session, a, {"device": a.device_id}, True)
    spend = Spend(spent_today(session, a.now, settings=settings), settings.doctor_daily_usd_cap)
    decision = admit_ask(
        frozen=False,
        asks_for_incident=for_incident,
        ask_cap=settings.doctor_asks_per_incident,
        asks_for_device_today=for_device_today,
        device_day_cap=settings.doctor_asks_per_device_day,
        spend=spend,
        mode=settings.doctor_mode,
    )
    if decision.ok:
        decision = _priced(settings)  # a bad price must never count as free
    if not decision.ok:
        if decision.which == "off":
            raise ApiError(503, "doctor_off", decision.why)
        if decision.which == "frozen":
            raise ApiError(409, "fleet_frozen", decision.why)
        _record_once(
            session,
            a,
            "doctor.blocked_over_cap",
            decision.why,
            {
                "spent_today_usd": spend.spent_today_usd,
                "cap_usd": spend.cap_usd,
                "which": decision.which,
                **kind_data,
            },
            key={"which": decision.which},
        )
        session.commit()
        raise ApiError(429, "doctor_cap", decision.why)

    _record(
        session,
        a,
        "doctor.asked",
        f"{a.device_id}'s healer asked the Fleet Doctor for advice (trigger {trigger}).",
        asked | {"counts_toward_device_day": not owned, **kind_data},
    )
    session.commit()
    return kind_data.get("incident_kind")


def _priced(settings: Settings) -> Decision:
    """Refuse (fail closed, `which="spend"`) when DOCTOR_USD_PER_1K_TOKENS can't price a call,
    before any money is spent rather than after."""
    try:
        cost_usd(0, 0, settings.doctor_usd_per_1k_tokens)
    except ValueError as exc:
        return Decision(False, BLOCKED_OVER_CAP, f"Blocked, can't price the call: {exc}.", "spend")
    return Decision(True, None, "The call can be priced.")


def _today(a: _Ask, kind: str, refs: dict[str, str]):
    """This fleet's `kind` events since local midnight whose refs contain `refs`."""
    return (
        events.find_query(refs, kinds=[kind])
        .where(Event.fleet_id == a.settings.fleet_id)
        .where(Event.ts >= day_start(a.now, a.settings.fleet_timezone))
        .order_by(None)
    )


def _count_asks(session: Session, a: _Ask, refs: dict[str, str], device_day: bool = False) -> int:
    """Asks that reached the LLM (`doctor.asked`) with these refs: all of them for an
    incident; today's that counted toward the device-day cap when `device_day`."""
    if device_day:
        stmt = _today(a, "doctor.asked", refs).where(
            Event.data.contains({"counts_toward_device_day": True})
        )
    else:
        stmt = (
            events.find_query(refs, kinds=["doctor.asked"])
            .where(Event.fleet_id == a.settings.fleet_id)
            .order_by(None)
        )
    return session.scalar(select(func.count()).select_from(stmt.subquery())) or 0


def _record_once(
    session: Session,
    a: _Ask,
    kind: str,
    why: str,
    data: dict[str, Any],
    *,
    key: dict[str, Any],
) -> None:
    """Record a refusal unless today already has one for this device, incident (or none) and
    `key` (its `which` or `reason`), so a device asking in a loop can't flood the timeline."""
    scope = {"device": a.device_id}
    if a.incident_id:
        scope["incident"] = a.incident_id
    stmt = _today(a, kind, scope).where(Event.data.contains(key))
    if not a.incident_id:
        stmt = stmt.where(~Event.refs.has_key("incident"))
    if session.scalar(select(stmt.exists())):
        return
    _record(session, a, kind, why, data)


def _advice(reply: LlmReply, settings: Settings) -> tuple[list[dict], dict[str, Any]]:
    """(the hypotheses to send, the `doctor.advised` data): parsed, safety-filtered, priced."""
    parsed, parsed_ok = parse_hypotheses(reply.text)
    kept, removed = filter_safety(parsed, settings.safety_paths)
    return kept, {
        "hypotheses": kept,
        "removed_for_safety": removed,
        "parsed_ok": parsed_ok,
        "model": reply.model,
        "prompt_tokens": reply.prompt_tokens,
        "completion_tokens": reply.completion_tokens,
        "usage_estimated": reply.usage_estimated,
        "cost_usd": cost_usd(
            reply.prompt_tokens, reply.completion_tokens, settings.doctor_usd_per_1k_tokens
        ),
    }


def _decline(
    session: Session,
    a: _Ask,
    failure: Exception,
    timeout_s: float,
    prompt: str,
    llm: Llm,
    latency_ms: int,
    kind_data: dict[str, Any],
) -> None:
    """`doctor.declined` for a failed call. Only the error's type goes in the event (its
    message goes to the log). A failed call may still have been billed, so it carries the
    prompt's estimated cost; the price was checked in step 1."""
    timed_out = isinstance(failure, LlmTimeout)
    error_type = type(failure).__name__
    log_line("doctor.ask_failed", level="warning", error_type=error_type, error=str(failure)[:300])
    what = f"timed out after {timeout_s:g} s" if timed_out else f"failed ({error_type})"
    tokens = estimate_tokens(prompt)
    _record(
        session,
        a,
        "doctor.declined",
        f"The Fleet Doctor's LLM {what}, so {a.device_id} got no answer.",
        {
            "reason": "llm timeout" if timed_out else "llm error",
            "stage": STAGE,
            "error_type": error_type,
            "model": getattr(llm, "model", ""),
            "latency_ms": latency_ms,
            "prompt_tokens": tokens,
            "completion_tokens": 0,
            "usage_estimated": True,
            "cost_usd": cost_usd(tokens, 0, a.settings.doctor_usd_per_1k_tokens),
            **kind_data,
        },
    )


def _advised_why(data: dict[str, Any]) -> str:
    n = len(data["hypotheses"])
    why = f"The Fleet Doctor gave {n} hypothes{'is' if n == 1 else 'es'} in {data['latency_ms']} ms"
    if data["removed_for_safety"]:
        why += f"; {data['removed_for_safety']} named a safety or visor path and were removed"
    if not data["parsed_ok"]:
        why += "; the reply wasn't JSON, so its prose is the hypothesis"
    return why + "."


def _record(session: Session, a: _Ask, kind: str, why: str, data: dict[str, Any]) -> None:
    events.record(
        session,
        ACTOR,
        "agent",
        kind,
        a.refs,
        why,
        data=data,
        ts=a.now,
        fleet_id=a.settings.fleet_id,
    )
