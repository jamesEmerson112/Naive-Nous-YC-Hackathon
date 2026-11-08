"""Fleet Doctor L1 (advise): the logic behind `POST /v1/devices/{id}/doctor/ask`
(docs/fleet-doctor/interfaces.md §3, §6.6; architecture §4).

The healer asks mid-heal and gets back at most ~300 tokens of hypotheses, each with how to
check it, never a patch. **No session is open during the LLM call (FR11):**
1. a short session reads the device, `frozen`, the caps and the day's spend, then closes
   (a refusal over a cap records `doctor.blocked_over_cap` in it and commits first);
2. the LLM call runs with no session, under `min(DOCTOR_ASK_TIMEOUT_S, budget_s - 2)`;
3. a second short session records `doctor.asked`, then `doctor.advised` (or
   `doctor.declined` when the call failed), and commits;
4. the advice is returned.

So a slow model never pins a database connection. Two asks at the same moment can each pass
the cap check, so a cap can be overshot by one; that's accepted *(inferred)*.

This service owns its two sessions and commits each (the FR11 exception to "services never
commit"): the route can't hold one session across the call.

An ask has no table: it is its events, all with `refs.ask` (and `refs.incident` whenever the
device named one, reported yet or not). Only asks that reached the LLM (`doctor.asked`) count
toward the caps; a refused ask doesn't. The device treats any non-200 as "no answer", so every
refusal is an `ApiError`: 404 unknown_device, 503 doctor_off, 409 fleet_frozen,
429 doctor_cap, 504 doctor_timeout, 502 doctor_unavailable.
"""

import time
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from romulus_cloud import events
from romulus_cloud.adapters.llm import Llm, LlmError, LlmReply, LlmTimeout
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
from romulus_cloud.models import Event, Incident
from romulus_cloud.services import fleet
from romulus_cloud.services.devices import get_device
from romulus_cloud.services.doctor_spend import spent_today
from romulus_cloud.settings import Settings
from romulus_contracts import DoctorAdvice, DoctorAsk, Hypothesis

ACTOR = "fleet-doctor"
STAGE = "ask"
QUESTION_CHARS = 200  # of the device's question kept on `doctor.asked`


def ask(
    sessions: sessionmaker,
    device_id: str,
    body: DoctorAsk,
    raw: dict[str, Any],
    now: datetime,
    *,
    settings: Settings,
    llm: Llm | None,
) -> DoctorAdvice:
    """Answer one ask with hypotheses, or raise `ApiError` (module docstring).

    `llm` is None only when the Doctor is off. `raw` is the verbatim body; only its
    `contract_version` is kept (the evidence itself is too big for an event).
    """
    ask_id = new_id("ask")
    incident_id = str(body.incident_id) if body.incident_id else None
    refs = {"device": device_id, "ask": ask_id}
    if incident_id:
        refs["incident"] = incident_id

    # 1. Short session: may this ask call the LLM?
    with sessions() as session:
        incident_kind = _admit(session, device_id, incident_id, refs, now, settings, llm)
    assert llm is not None  # _admit refuses with 503 otherwise

    # 2. The LLM call, with no session open.
    system, user = ask_prompt(body.model_dump(mode="json"))
    timeout_s = min(settings.doctor_ask_timeout_s, body.budget_s - 2)
    started = time.perf_counter()
    reply: LlmReply | None = None
    failure: LlmError | None = None
    try:
        reply = llm.complete(
            system, user, max_tokens=settings.doctor_ask_max_tokens, timeout_s=timeout_s
        )
    except LlmError as exc:
        failure = exc
    latency_ms = round((time.perf_counter() - started) * 1000)

    # 3. Short session: record what happened.
    kind_data = {"incident_kind": incident_kind} if incident_kind else {}
    trigger = body.trigger[:40]
    asked = {
        "trigger": trigger,
        "question": body.question[:QUESTION_CHARS],
        "budget_s": body.budget_s,
        "timeout_s": timeout_s,
        "contract_version": raw.get("contract_version"),
        **kind_data,
    }
    if reply is not None:
        hypotheses, advised = _advice(reply, settings)
        advised |= {"latency_ms": latency_ms, **kind_data}
    with sessions() as session:
        _record(
            session,
            "doctor.asked",
            refs,
            f"{device_id}'s healer asked the Fleet Doctor for advice (trigger {trigger}).",
            asked,
            now,
            settings,
        )
        if reply is not None:
            _record(session, "doctor.advised", refs, _advised_why(advised), advised, now, settings)
        else:
            timed_out = isinstance(failure, LlmTimeout)
            what = f"timed out after {timeout_s:g} s" if timed_out else "failed"
            _record(
                session,
                "doctor.declined",
                refs,
                f"The Fleet Doctor's LLM {what}, so {device_id} got no answer.",
                {
                    "reason": "llm timeout" if timed_out else "llm error",
                    "stage": STAGE,
                    "error": str(failure)[:300],
                    "model": getattr(llm, "model", ""),
                    "latency_ms": latency_ms,
                    **kind_data,
                },
                now,
                settings,
            )
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
            502, "doctor_unavailable", f"The Fleet Doctor's LLM failed: {str(failure)[:200]}"
        )
    return DoctorAdvice(
        ask_id=ask_id, hypotheses=[Hypothesis(**h) for h in hypotheses], model=advised["model"]
    )


def _admit(
    session: Session,
    device_id: str,
    incident_id: str | None,
    refs: dict[str, str],
    now: datetime,
    settings: Settings,
    llm: Llm | None,
) -> str | None:
    """Step 1: the device exists, the Doctor is on, the fleet isn't frozen, and the caps and
    spend allow one more call. → the incident's kind, if it has been reported. A refusal over
    a cap records `doctor.blocked_over_cap` and commits before raising 429."""
    get_device(session, device_id)  # 404 unknown_device
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
    incident = session.get(Incident, incident_id) if incident_id else None
    incident_kind = incident.kind if incident else None

    # An ask about an incident counts against that incident; one with no incident counts
    # against the device's day (DOCTOR_ASKS_PER_DEVICE_DAY). None: that cap doesn't apply.
    for_incident = for_device_today = None
    if incident_id:
        for_incident = _asks(session, {"incident": incident_id}, settings)
    else:
        since = day_start(now, settings.fleet_timezone)
        for_device_today = _asks(
            session, {"device": device_id}, settings, since=since, without_incident=True
        )
    spend = Spend(spent_today(session, now, settings=settings), settings.doctor_daily_usd_cap)
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
    if decision.ok:
        return incident_kind
    if decision.which == "off":
        raise ApiError(503, "doctor_off", decision.why)
    if decision.which == "frozen":
        raise ApiError(409, "fleet_frozen", decision.why)
    _record(
        session,
        "doctor.blocked_over_cap",
        refs,
        decision.why,
        {
            "spent_today_usd": spend.spent_today_usd,
            "cap_usd": spend.cap_usd,
            "which": decision.which,
            **({"incident_kind": incident_kind} if incident_kind else {}),
        },
        now,
        settings,
    )
    session.commit()
    raise ApiError(429, "doctor_cap", decision.why)


def _priced(settings: Settings) -> Decision:
    """Refuse (fail closed, `which="spend"`) when DOCTOR_USD_PER_1K_TOKENS can't price a call,
    before any money is spent rather than after."""
    try:
        cost_usd(0, 0, settings.doctor_usd_per_1k_tokens)
    except ValueError as exc:
        return Decision(False, BLOCKED_OVER_CAP, f"Blocked, can't price the call: {exc}.", "spend")
    return Decision(True, None, "The call can be priced.")


def _asks(
    session: Session,
    refs: dict[str, str],
    settings: Settings,
    *,
    since: datetime | None = None,
    without_incident: bool = False,
) -> int:
    """How many asks reached the LLM (`doctor.asked`) in this fleet with these refs."""
    stmt = (
        events.find_query(refs, kinds=["doctor.asked"])
        .where(Event.fleet_id == settings.fleet_id)
        .order_by(None)
    )
    if since is not None:
        stmt = stmt.where(Event.ts >= since)
    if without_incident:
        stmt = stmt.where(~Event.refs.has_key("incident"))
    return session.scalar(select(func.count()).select_from(stmt.subquery())) or 0


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


def _advised_why(data: dict[str, Any]) -> str:
    n = len(data["hypotheses"])
    why = f"The Fleet Doctor gave {n} hypothes{'is' if n == 1 else 'es'} in {data['latency_ms']} ms"
    if data["removed_for_safety"]:
        why += f"; {data['removed_for_safety']} named a safety or visor path and were removed"
    if not data["parsed_ok"]:
        why += "; the reply wasn't JSON, so its prose is the hypothesis"
    return why + "."


def _record(
    session: Session,
    kind: str,
    refs: dict[str, str],
    why: str,
    data: dict[str, Any],
    now: datetime,
    settings: Settings,
) -> None:
    events.record(
        session, ACTOR, "agent", kind, refs, why, data=data, ts=now, fleet_id=settings.fleet_id
    )
