"""Work orders: a hardware fault becomes a Naïve board card (flow F6; naive-integration
§3.1–3.4; D11, D12, D19, D19b, K13, K16).

- `triage_new` (every worker tick): each unresolved hardware incident with no work order
  gets one. `domain.triage` decides it and checks our spend caps, since Naïve can't (K16).
- `sync` (every 15 s): posts `pending` orders as cards (`Idempotency-Key` = the work-order
  id), then reads each open card by id (Naïve has no card webhooks) and acts only when the
  card's `updated_at` changed (§3.2), mapping its status and note with `card_outcome`.
- `resolve`: a person approves or denies an order that waits for one (D19b).

Statuses (`work_orders.status`):
    pending           queued to post (the outbox, naive-integration §3.1)
    blocked_over_cap  over a spend cap; never posted until a person approves (D19b)
    needs_human       no part to order, Naïve refused the card, or the card came back wrong
    posted → in_progress → done | stopped | needs_human   (card status table, §3.2)
    denied            a person closed it
Every change records an event with `refs.incident`, `refs.work_order` and `refs.status`
(the digest's convention, `domain/digest.py`), so the incident's story shows it.

**One request per key.** Naïve binds an Idempotency-Key to the body it first saw, so the
card (title, body, assignee, board) is rendered once, on the first attempt, frozen in
`raw.request`, and sent unchanged on every retry and after an approve. A settings or
parts.yaml change never alters a card that may already exist.

**Mode guard.** Each order records the `NAIVE_MODE` it was created in (`raw.naive_mode`).
An order is only posted, and its card only polled, in that same mode, so switching to live
never posts the fake backlog (it goes to `needs_human`).

**Kill switch (D11).** While the fleet is frozen, sync posts nothing (one `post_held`
event per order) but keeps polling cards already out.

**Caps count** the orders created today that went, or are about to go, to Naïve: those
with a card, plus `pending` ones. Blocked, denied and card-less `needs_human` orders don't
count. "Today" is the calendar day in `FLEET_TIMEZONE` (default UTC).

**Phone (K13):** the card's phone comes from `WORK_ORDER_PHONE`, never from parts.yaml. In
live mode a card is not rendered while it is still the placeholder.

`raw` holds: `naive_mode`, `request`, the retry note (`post_attempts`, `retry_at`,
`error`), `held_frozen`, and `card` (the last card snapshot). Rows are locked
(`FOR UPDATE`) by `sync` and `resolve`, so a person's decision can't race a post. Nothing
here commits; the worker step or the route does.
"""

from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from sqlalchemy import exists, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from romulus_cloud import events
from romulus_cloud.adapters.naive import NaiveClient, NaiveError, build_naive
from romulus_cloud.config import load_parts
from romulus_cloud.domain.story import STAGE
from romulus_cloud.domain.triage import (
    Caps,
    PartsCatalog,
    WorkOrderSpec,
    card_outcome,
    check_caps,
    needs_work_order,
    render_card,
)
from romulus_cloud.errors import ApiError
from romulus_cloud.ids import new_id
from romulus_cloud.log import log_line
from romulus_cloud.models import Device, FleetState, Incident, WorkOrder
from romulus_cloud.settings import Settings, get_settings
from romulus_contracts import IncidentReport, Outcome

ACTOR = "worker:workorders"
PLACEHOLDER_PHONE = "TEAMMATE_PHONE"
NO_PART = "(none)"
"""`work_orders.part` when parts.yaml has no part for the fault kind."""

WAITING = ("blocked_over_cap", "needs_human", "stopped")
CLOSED = ("done", "denied")
OPEN_CARD = ("posted", "in_progress", "needs_human")
"""Statuses whose card is still read: a card Naïve parked (`blocked`) can be unblocked."""

RETRY_BASE_S = 15
RETRY_MAX_S = 600
MAX_POST_ATTEMPTS = 10
"""After this many retryable failures in a row, the order goes to a person."""

_clients: dict[tuple[Any, ...], NaiveClient] = {}


def naive_client(settings: Settings) -> NaiveClient:
    """One client per Naïve config for the process. FakeNaive keeps its cards in memory,
    so the worker must reuse it across ticks (a restart forgets them)."""
    key = (
        settings.naive_mode,
        settings.naive_board_id,
        settings.naive_base_url,
        settings.naive_version,
        settings.naive_api_key.get_secret_value(),
    )
    if key not in _clients:
        _clients[key] = build_naive(
            settings.naive_mode,
            api_key=settings.naive_api_key.get_secret_value() or None,
            board_id=settings.naive_board_id or None,
            base_url=settings.naive_base_url,
            version=settings.naive_version,
        )
    return _clients[key]


def caps(settings: Settings) -> Caps:
    return Caps(
        order_max_usd=settings.order_max_usd,
        orders_per_day_max=settings.orders_per_day_max,
        orders_per_robot_day_max=settings.orders_per_robot_day_max,
    )


def _raw(order: WorkOrder) -> dict[str, Any]:
    return dict(order.raw) if isinstance(order.raw, dict) else {}


def _note(order: WorkOrder, **changes: Any) -> None:
    """Merge `changes` into `raw` (a None value removes the key). JSONB needs a new dict."""
    raw = _raw(order)
    for key, value in changes.items():
        if value is None:
            raw.pop(key, None)
        else:
            raw[key] = value
    order.raw = raw


def _card(order: WorkOrder) -> dict[str, Any]:
    card = _raw(order).get("card")
    return card if isinstance(card, dict) else {}


def view(o: WorkOrder) -> dict[str, Any]:
    card, raw = _card(o), _raw(o)
    return {
        "id": o.id,
        "incident_id": o.incident_id,
        "device_id": o.device_id,
        "part": o.part,
        "max_usd": o.max_usd,
        "status": o.status,
        "naive_mode": raw.get("naive_mode"),
        "naive_card_id": o.naive_card_id,
        "card_status": card.get("status"),
        "card_note": card.get("note"),
        "post_attempts": raw.get("post_attempts", 0),
        "result": o.result,
        "created_at": o.created_at.isoformat() if o.created_at else None,
        "updated_at": o.updated_at.isoformat() if o.updated_at else None,
    }


def list_orders(
    session: Session, *, status: str | None = None, limit: int = 100, fleet_id: str = "default"
) -> list[WorkOrder]:
    stmt = select(WorkOrder).where(WorkOrder.fleet_id == fleet_id)
    if status:
        stmt = stmt.where(WorkOrder.status == status)
    return list(session.scalars(stmt.order_by(WorkOrder.created_at.desc()).limit(limit)))


def get_order(session: Session, order_id: str, *, lock: bool = False) -> WorkOrder:
    order = session.get(WorkOrder, order_id, with_for_update=lock, populate_existing=lock)
    if order is None:
        raise ApiError(404, "unknown_work_order", f"Work order {order_id} does not exist.")
    return order


def history(session: Session, order_id: str) -> list[dict[str, Any]]:
    """The order's events, oldest first; same-tick events in loop order (`story.STAGE`)."""
    found = events.find(session, {"work_order": order_id})
    found.sort(key=lambda e: (e.ts, STAGE.get(e.kind, 50)))
    return [
        {"at": e.ts.isoformat(), "actor": e.actor, "kind": e.kind, "refs": e.refs, "why": e.why}
        for e in found
    ]


# --- triage ----------------------------------------------------------------------------


def _report(incident: Incident) -> IncidentReport:
    """The fields triage reads, from the incident's columns (the healer's outcome
    `category` lives only in the raw body)."""
    raw = incident.raw if isinstance(incident.raw, dict) else {}
    raw_outcome = raw.get("outcome") if isinstance(raw.get("outcome"), dict) else {}
    category = raw_outcome.get("category")
    return IncidentReport(
        incident_id=incident.id,
        occurred_at=incident.occurred_at,
        kind=incident.kind,
        summary=incident.summary or "",
        outcome=Outcome(
            resolved=bool(incident.resolved),
            resolving_action=incident.resolving_action,
            suspected_cause=incident.suspected_cause or "unknown",
            category=category if isinstance(category, str) else None,
        ),
    )


def _day(now: datetime, tz: str) -> tuple[datetime, datetime]:
    local = now.astimezone(ZoneInfo(tz))
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=1)


def counts_today(
    session: Session, now: datetime, device_id: str, settings: Settings
) -> tuple[int, int]:
    """(orders today fleet-wide, orders today for `device_id`) that count against the caps."""
    start, end = _day(now, settings.fleet_timezone)
    base = (
        select(func.count())
        .select_from(WorkOrder)
        .where(
            WorkOrder.fleet_id == settings.fleet_id,
            WorkOrder.created_at >= start,
            WorkOrder.created_at < end,
            or_(WorkOrder.naive_card_id.isnot(None), WorkOrder.status == "pending"),
        )
    )
    fleet = session.scalar(base) or 0
    robot = session.scalar(base.where(WorkOrder.device_id == device_id)) or 0
    return fleet, robot


def triage_new(
    session: Session,
    now: datetime,
    *,
    settings: Settings | None = None,
    catalog: PartsCatalog | None = None,
) -> list[WorkOrder]:
    """Open a work order for each unresolved hardware incident that has none. Safe to repeat:
    `incident_id` is unique, so a second run (or a racing one) inserts nothing."""
    settings = settings or get_settings()
    catalog = catalog if catalog is not None else load_parts()
    category = func.lower(Incident.raw["outcome"]["category"].astext)
    stmt = (
        select(Incident)
        .where(
            Incident.fleet_id == settings.fleet_id,
            Incident.resolved.isnot(True),
            or_(func.lower(Incident.suspected_cause) == "hardware", category == "park"),
            ~exists().where(WorkOrder.incident_id == Incident.id),
        )
        .order_by(Incident.occurred_at, Incident.id)
    )
    created = []
    for incident in session.scalars(stmt).all():
        spec = needs_work_order(_report(incident), catalog, device_id=incident.device_id)
        if spec is None:
            continue
        fleet_n, robot_n = counts_today(session, now, incident.device_id, settings)
        decision = check_caps(spec, fleet_n, robot_n, caps(settings))
        status = "pending" if decision.ok else decision.status
        order = _insert(session, spec, status, now, settings)
        if order is None:
            continue
        why = decision.why if decision.status == "needs_human" else f"{spec.why}. {decision.why}"
        if status == "blocked_over_cap":
            why += " Not posted to Naïve; it waits for a person (D19b)."
        _record(
            session,
            order,
            "workorder.created",
            status,
            why,
            now,
            data={
                "part": order.part,
                "part_name": spec.part.name if spec.part else None,
                "quantity": spec.quantity,
                "est_cost_usd": spec.est_cost_usd,
                "max_usd": spec.max_usd,
                "orders_today": fleet_n,
                "orders_for_robot_today": robot_n,
                "naive_mode": settings.naive_mode,
            },
        )
        created.append(order)
    return created


def _insert(
    session: Session, spec: WorkOrderSpec, status: str, now: datetime, settings: Settings
) -> WorkOrder | None:
    order_id = session.execute(
        pg_insert(WorkOrder)
        .values(
            id=new_id("wo"),
            fleet_id=settings.fleet_id,
            incident_id=spec.incident_id,
            device_id=spec.device_id,
            part=spec.part.id if spec.part else NO_PART,
            max_usd=spec.max_usd,
            status=status,
            created_at=now,
            updated_at=now,
            raw={"naive_mode": settings.naive_mode},
        )
        .on_conflict_do_nothing(index_elements=[WorkOrder.incident_id])
        .returning(WorkOrder.id)
    ).scalar()
    return session.get(WorkOrder, order_id) if order_id else None


def _record(
    session: Session,
    order: WorkOrder,
    kind: str,
    status: str,
    why: str,
    now: datetime,
    *,
    actor: str = ACTOR,
    actor_class: Literal["agent", "automation", "human"] = "automation",
    before: str | None = None,
    data: dict[str, Any] | None = None,
) -> None:
    refs: dict[str, Any] = {
        "incident": order.incident_id,
        "device": order.device_id,
        "work_order": order.id,
        "status": status,
    }
    if before is not None:
        refs["from"], refs["to"] = before, status
    events.record(
        session, actor, actor_class, kind, refs, why, data=data, ts=now, fleet_id=order.fleet_id
    )


def _set_status(
    session: Session,
    order: WorkOrder,
    status: str,
    why: str,
    now: datetime,
    *,
    kind: str = "workorder.status_changed",
    actor: str = ACTOR,
    actor_class: Literal["agent", "automation", "human"] = "automation",
    data: dict[str, Any] | None = None,
) -> None:
    before = order.status
    order.status = status
    order.updated_at = now
    session.flush()
    _record(
        session,
        order,
        kind,
        status,
        why,
        now,
        actor=actor,
        actor_class=actor_class,
        before=before,
        data=data,
    )


# --- sync with Naïve -------------------------------------------------------------------


def _frozen(session: Session, fleet_id: str) -> bool:
    state = session.get(FleetState, fleet_id)
    return bool(state and state.frozen)


def sync(
    session: Session,
    now: datetime,
    *,
    settings: Settings | None = None,
    naive: NaiveClient | None = None,
    catalog: PartsCatalog | None = None,
) -> dict[str, int]:
    """Post pending orders (unless the fleet is frozen), then read every open card once.
    Rows are locked with `SKIP LOCKED`: one a person is resolving waits for the next run."""
    settings = settings or get_settings()
    naive = naive or naive_client(settings)
    catalog = catalog if catalog is not None else load_parts()
    base = select(WorkOrder).where(WorkOrder.fleet_id == settings.fleet_id)
    order_by = (WorkOrder.created_at, WorkOrder.id)
    pending = session.scalars(
        base.where(WorkOrder.status == "pending")
        .order_by(*order_by)
        .with_for_update(skip_locked=True)
    ).all()
    posted = 0
    if _frozen(session, settings.fleet_id):
        for order in pending:
            _hold(session, order, now)
    else:
        for order in pending:
            posted += _post(session, order, now, settings, naive, catalog)
    open_cards = session.scalars(
        base.where(WorkOrder.status.in_(OPEN_CARD), WorkOrder.naive_card_id.isnot(None))
        .order_by(*order_by)
        .with_for_update(skip_locked=True)
    ).all()
    mine = [o for o in open_cards if _raw(o).get("naive_mode") == settings.naive_mode]
    changed = sum(_poll(session, order, now, naive) for order in mine)
    return {"pending": len(pending), "posted": posted, "polled": len(mine), "changed": changed}


def _hold(session: Session, order: WorkOrder, now: datetime) -> None:
    """The kill switch is on: don't post. One event per order, not one per tick."""
    if _raw(order).get("held_frozen"):
        return
    _note(order, held_frozen=True)
    order.updated_at = now
    session.flush()
    _record(
        session,
        order,
        "workorder.post_held",
        "pending",
        "Held: fleet frozen. Not posted to Naïve until the fleet is unfrozen (D11).",
        now,
    )


def _card_spec(session: Session, order: WorkOrder, catalog: PartsCatalog) -> WorkOrderSpec | str:
    """The spec to render the card from, or why it can't be rendered."""
    incident = session.get(Incident, order.incident_id)
    if incident is None:
        return f"incident {order.incident_id} is missing"
    spec = needs_work_order(_report(incident), catalog, device_id=order.device_id)
    if spec is None or spec.part is None or spec.part.id != order.part:
        return (
            f"parts.yaml no longer maps {incident.kind} to part {order.part}; "
            "a person must pick the part"
        )
    return replace(spec, max_usd=order.max_usd)


def _request(
    session: Session,
    order: WorkOrder,
    settings: Settings,
    naive: NaiveClient,
    catalog: PartsCatalog,
) -> dict[str, Any] | str:
    """The frozen card request, rendering it on the first attempt; or why it can't be sent."""
    board = getattr(naive, "board_id", None)
    frozen = _raw(order).get("request")
    if isinstance(frozen, dict):
        if frozen.get("board_id") != board:
            return (
                f"it was first sent to board {frozen.get('board_id')} and the board is now "
                f"{board}; a card may exist there under Idempotency-Key {order.id}"
            )
        return frozen
    phone = settings.work_order_phone.strip()
    if settings.naive_mode == "live" and phone in ("", PLACEHOLDER_PHONE):
        return (
            "WORK_ORDER_PHONE is still the placeholder. Set it to a teammate's phone "
            "(never a business, K13), restart the worker, then approve the order"
        )
    spec = _card_spec(session, order, catalog)
    if isinstance(spec, str):
        return spec
    device = session.get(Device, order.device_id)
    robot = f"{device.device_class}, {device.kind}" if device else None
    text = render_card(
        spec, order.id, deliver_to=settings.work_order_deliver_to, phone=phone, robot=robot
    )
    request = {
        "title": text.title,
        "body": text.body,
        "assignee": settings.naive_ops_agent or None,
        "board_id": board,
    }
    _note(order, request=request)
    return request


def _post(
    session: Session,
    order: WorkOrder,
    now: datetime,
    settings: Settings,
    naive: NaiveClient,
    catalog: PartsCatalog,
) -> int:
    raw = _raw(order)
    retry_at = raw.get("retry_at")
    if retry_at and now < datetime.fromisoformat(retry_at):
        return 0
    created_mode = raw.get("naive_mode", "unknown")
    if created_mode != settings.naive_mode:
        why = f"Not posted: created in {created_mode} mode; not posted {settings.naive_mode}."
        _set_status(session, order, "needs_human", why, now)
        return 0
    request = _request(session, order, settings, naive, catalog)
    if isinstance(request, str):
        _set_status(session, order, "needs_human", f"Not posted: {request}.", now)
        return 0
    _note(order, held_frozen=None)
    try:
        card = naive.create_card(
            request["title"], request["body"], request["assignee"], idempotency_key=order.id
        )
        if not isinstance(card, dict) or not card.get("id"):
            raise NaiveError("Naïve answered without a card id", code="invalid_response")
    except NaiveError as exc:
        _post_failed(session, order, now, exc)
        return 0
    order.naive_card_id = str(card["id"])
    order.naive_updated_at = card.get("updated_at")
    _note(order, card=card, post_attempts=None, retry_at=None, error=None)
    _set_status(
        session,
        order,
        "posted",
        f"Posted to the Naïve board for {request['assignee'] or 'no assignee'} "
        f"(Idempotency-Key {order.id}): {request['title']}",
        now,
        kind="workorder.card_posted",
        data={
            "card_id": order.naive_card_id,
            "assignee": request["assignee"],
            "title": request["title"],
            "body": request["body"],
        },
    )
    return 1


def _post_failed(session: Session, order: WorkOrder, now: datetime, exc: NaiveError) -> None:
    attempts = int(_raw(order).get("post_attempts") or 0) + 1
    detail = str(exc)
    if exc.retryable and attempts < MAX_POST_ATTEMPTS:
        wait_s = min(RETRY_BASE_S * 2 ** (attempts - 1), RETRY_MAX_S)
        retry_at = now + timedelta(seconds=wait_s)
        _note(order, post_attempts=attempts, retry_at=retry_at.isoformat(), error=detail)
        order.updated_at = now
        session.flush()
        _record(
            session,
            order,
            "workorder.post_retried",
            "pending",
            f"Posting to Naïve failed ({detail}); still queued, attempt {attempts} of "
            f"{MAX_POST_ATTEMPTS}, next try in {wait_s} s with the same Idempotency-Key "
            "and the same card.",
            now,
            data={"attempts": attempts, "retry_at": retry_at.isoformat(), "code": exc.code},
        )
        return
    _note(order, post_attempts=attempts, retry_at=None, error=detail)
    board = (_raw(order).get("request") or {}).get("board_id")
    why = (
        f"Naïve refused the card ({detail}); a person must look."
        if not exc.retryable
        else f"Posting to Naïve failed {attempts} times in a row ({detail}). A card may "
        f"already exist on board {board} under Idempotency-Key {order.id}: check the board, "
        "then approve (it resends the same card) or deny."
    )
    _set_status(session, order, "needs_human", why, now, data={"code": exc.code})


def _poll(session: Session, order: WorkOrder, now: datetime, naive: NaiveClient) -> int:
    """Read one card and act only if Naïve changed it (`updated_at`, §3.2). Returns 1 on a
    recorded change."""
    assert order.naive_card_id is not None
    if _card(order).get("status") == "done":
        return 0  # Naïve's `done` is final: nothing more will change on the card
    try:
        card = naive.get_card(order.naive_card_id)
        stamp = card.get("updated_at")
        if stamp is not None and stamp == order.naive_updated_at:
            return 0
        status = str(card.get("status") or "")
        last_comment = ""
        if status not in ("todo", "doing", "done"):
            comments = naive.list_comments(order.naive_card_id)
            last_comment = str(comments[-1].get("body") or "") if comments else ""
    except NaiveError as exc:
        if exc.retryable:
            log_line("workorders.poll_failed", work_order=order.id, error=str(exc))
            return 0
        if order.status == "needs_human":
            return 0
        _set_status(
            session, order, "needs_human", f"Can't read card {order.naive_card_id}: {exc}", now
        )
        return 1
    outcome = card_outcome(status, card.get("note"), last_comment, max_usd=order.max_usd)
    _note(order, card=card)
    order.naive_updated_at = card.get("updated_at")
    if outcome.status in ("done", "stopped", "needs_human"):
        order.result = outcome.why
    session_id = card.get("woken_session_id")
    note = str(card.get("note") or "").strip().split("\n", 1)[0] or None
    data = {
        "card_status": status,
        "note": note,
        "order_ref": outcome.order_ref,
        "cost_usd": outcome.cost_usd,
    }
    who = {
        "actor": f"naive:{session_id}" if session_id else ACTOR,
        "actor_class": "agent" if session_id else "automation",
    }
    if outcome.status != order.status:
        _set_status(session, order, outcome.status, outcome.why, now, data=data, **who)
    else:
        order.updated_at = now
        session.flush()
        why = f"Card {order.naive_card_id} is {status or 'unknown'} on Naïve: {outcome.why}"
        _record(session, order, "workorder.card_updated", order.status, why, now, data=data, **who)
    return 1


# --- a person resolves an order (D19b) --------------------------------------------------


def resolve(
    session: Session,
    order_id: str,
    action: Literal["approve", "deny"],
    by: str,
    why: str,
    now: datetime,
    *,
    naive_mode: str = "fake",
) -> WorkOrder:
    """A person's decision on an order waiting for one. The row is locked first.

    - `deny`: only an order waiting for a person whose card (if any) is finished on Naïve,
      or a card-less `pending` order. It becomes `denied`.
    - `approve` with no card: back to `pending`; the frozen card is resent with the same
      Idempotency-Key. Refused for an order created in another `NAIVE_MODE`.
    - `approve` with a card: accepts its outcome as `done`, only if Naïve's card is `done`.
    """
    order = get_order(session, order_id, lock=True)
    actor = f"human:{by}"
    card, raw = _card(order), _raw(order)
    card_open = order.naive_card_id is not None and card.get("status") != "done"
    if order.status in CLOSED:
        raise ApiError(
            409, "work_order_closed", f"Work order {order_id} is already {order.status}."
        )
    if action == "deny":
        if card_open:
            raise ApiError(
                409,
                "card_open",
                f"Card {order.naive_card_id} is still {card.get('status') or 'open'} on Naïve; "
                "close it on the board first, then deny.",
            )
        if order.status not in WAITING and order.status != "pending":
            raise ApiError(
                409,
                "not_waiting",
                f"Work order {order_id} is {order.status}; only an order waiting for a person "
                "or a queued one can be denied.",
            )
        say = f"{by} denied it: {why}."
        if raw.get("post_attempts") and not order.naive_card_id:
            say += (
                f" An earlier post failed, so a card may exist under Idempotency-Key "
                f"{order.id}; check the board."
            )
        order.result = f"Denied by {by}: {why}"
        _set_status(session, order, "denied", say, now, actor=actor, actor_class="human")
        return order
    if order.status not in WAITING:
        raise ApiError(
            409,
            "not_waiting",
            f"Work order {order_id} is {order.status}, not waiting for a person.",
        )
    if order.naive_card_id:
        if card_open:
            raise ApiError(
                409,
                "card_open",
                f"Card {order.naive_card_id} is {card.get('status') or 'unreadable'} on Naïve, "
                "not done; it stays needs_human until the card finishes.",
            )
        order.result = f"Accepted by {by}: {why}"
        _set_status(
            session,
            order,
            "done",
            f"{by} accepted card {order.naive_card_id}'s outcome: {why}",
            now,
            actor=actor,
            actor_class="human",
        )
        return order
    if order.part == NO_PART:
        raise ApiError(
            409,
            "no_part",
            f"Work order {order_id} has no part to order; add one to parts.yaml for a new "
            "incident, or deny this order.",
        )
    created_mode = raw.get("naive_mode", "unknown")
    if created_mode != naive_mode:
        raise ApiError(
            409,
            "wrong_mode",
            f"Work order {order_id} was created in {created_mode} mode and Naïve is now "
            f"{naive_mode}; deny it instead.",
        )
    # Keep the frozen request; clear only the retry note, so the next sync resends it now.
    _note(order, post_attempts=None, retry_at=None, error=None)
    _set_status(
        session,
        order,
        "pending",
        f"{by} approved it: {why}. It will be sent with Idempotency-Key {order.id}.",
        now,
        actor=actor,
        actor_class="human",
    )
    return order
