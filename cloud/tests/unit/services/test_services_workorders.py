"""services/workorders.py: triage, cap counting, posting, polling, resolving. No network:
FakeNaive, or HttpNaive on an `httpx.MockTransport`."""

import json
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from pydantic import SecretStr, ValidationError
from sqlalchemy import delete, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from romulus_cloud import events
from romulus_cloud.adapters.naive import FakeNaive, HttpNaive, NaiveError
from romulus_cloud.domain.triage import parse_catalog
from romulus_cloud.errors import ApiError
from romulus_cloud.models import Device, Event, FleetState, Incident, WorkOrder
from romulus_cloud.services import devices, incidents, workorders
from romulus_contracts import IncidentReport, Outcome

T0 = datetime(2026, 11, 8, 8, 30, tzinfo=UTC)  # 00:30 in San Francisco
CATALOG = parse_catalog(
    {
        "defaults": {"supplier": "Test desk (placeholder)", "phone": "TEAMMATE_PHONE"},
        "parts": [
            {
                "id": "servo",
                "name": "STS3215 servo",
                "fault_kinds": ["servo_absent", "gripper_stall"],
                "typical_usd": 18,
                "max_usd": 40,
            }
        ],
    }
)


@pytest.fixture
def arm(db_session):
    devices.register(db_session, "arm-9", actor="human:test", actor_class="human")
    db_session.flush()
    return "arm-9"


def _incident(session, device_id, *, kind="servo_absent", cause="hardware", category="park",
              resolved=False, at=T0 - timedelta(minutes=5)):  # fmt: skip
    body = IncidentReport(
        incident_id=uuid.uuid4(),
        occurred_at=at,
        kind=kind,
        summary="Wrist servo id 5 stopped answering",
        outcome=Outcome(resolved=resolved, suspected_cause=cause, category=category),
    )
    raw = body.model_dump(mode="json")
    incident, _ = incidents.report(session, device_id, body, raw, at, safety_paths=[])
    return incident.id


def _order(session, incident_id) -> WorkOrder:
    return session.query(WorkOrder).filter_by(incident_id=incident_id).one()


def _triage(session, settings, now=T0):
    return workorders.triage_new(session, now, settings=settings, catalog=CATALOG)


def _sync(session, settings, naive, now=T0):
    return workorders.sync(session, now, settings=settings, naive=naive, catalog=CATALOG)


class AlwaysFails(FakeNaive):
    def __init__(self, status, code=None):
        super().__init__()
        self.error = (status, code)
        self.creates = 0

    def create_card(self, title, body, assignee, idempotency_key):
        self.creates += 1
        raise NaiveError("nope", status=self.error[0], code=self.error[1])


# --- triage ---------------------------------------------------------------------------------


def test_triage_picks_only_unresolved_hardware_incidents(db_session, settings, arm):
    hw = _incident(db_session, arm)
    park_only = _incident(db_session, arm, cause="unknown", category="park", kind="gripper_stall")
    _incident(db_session, arm, cause="software", category="fix-code")
    _incident(db_session, arm, resolved=True)
    made = _triage(db_session, settings.model_copy(update={"orders_per_robot_day_max": 5}))
    assert sorted(o.incident_id for o in made) == sorted([hw, park_only])
    assert _triage(db_session, settings) == []  # a second run opens nothing


def test_no_catalogued_part_needs_a_person_and_cannot_be_approved(db_session, settings, arm):
    incident_id = _incident(db_session, arm, kind="motor_fire")
    (order,) = _triage(db_session, settings)
    assert order.status == "needs_human" and order.part == workorders.NO_PART
    with pytest.raises(ApiError) as err:
        workorders.resolve(db_session, order.id, "approve", "kent", "go", T0)
    assert err.value.error == "no_part"
    workorders.resolve(db_session, order.id, "deny", "kent", "replaced by hand", T0)
    assert _order(db_session, incident_id).status == "denied"


def test_caps_count_the_day_in_the_fleet_timezone(db_session, settings, arm):
    _incident(db_session, arm)
    (order,) = _triage(db_session, settings, now=T0 - timedelta(hours=1))  # 23:30 SF, 07:30 UTC
    utc = settings.model_copy(update={"fleet_timezone": "UTC"})
    sf = settings.model_copy(update={"fleet_timezone": "America/Los_Angeles"})
    assert workorders.counts_today(db_session, T0, arm, utc) == (1, 1)
    assert workorders.counts_today(db_session, T0, arm, sf) == (0, 0)  # yesterday in SF
    order.status = "blocked_over_cap"
    db_session.flush()
    assert workorders.counts_today(db_session, T0, arm, utc) == (0, 0)  # never posted


# --- posting ---------------------------------------------------------------------------------


def test_retryable_errors_back_off_then_go_to_a_person(db_session, settings, arm):
    incident_id = _incident(db_session, arm)
    _triage(db_session, settings)
    naive = AlwaysFails(503)
    now = T0
    _sync(db_session, settings, naive, now)
    order = _order(db_session, incident_id)
    assert order.status == "pending" and order.raw["post_attempts"] == 1
    _sync(db_session, settings, naive, now + timedelta(seconds=10))  # inside the 15 s backoff
    assert naive.creates == 1
    for _ in range(workorders.MAX_POST_ATTEMPTS - 1):
        now = datetime.fromisoformat(order.raw["retry_at"])
        _sync(db_session, settings, naive, now)
    assert naive.creates == workorders.MAX_POST_ATTEMPTS
    assert order.status == "needs_human"
    last_why = workorders.history(db_session, order.id)[-1]["why"]
    assert f"failed {workorders.MAX_POST_ATTEMPTS} times in a row" in last_why
    retried = events.find(db_session, {"work_order": order.id}, kinds=["workorder.post_retried"])
    assert len(retried) == workorders.MAX_POST_ATTEMPTS - 1


def test_a_permanent_error_needs_a_person_and_approve_resends_the_same_card(
    db_session, settings, arm
):
    incident_id = _incident(db_session, arm)
    _triage(db_session, settings)
    _sync(db_session, settings, AlwaysFails(400, "invalid_request"))
    order = _order(db_session, incident_id)
    assert order.status == "needs_human" and order.naive_card_id is None
    frozen = order.raw["request"]
    workorders.resolve(db_session, order.id, "approve", "kent", "Naïve fixed", T0)
    assert order.status == "pending" and "post_attempts" not in order.raw
    assert order.raw["request"] == frozen  # approve keeps the frozen card
    naive = FakeNaive()
    changed = settings.model_copy(update={"work_order_phone": "+1 someone else"})
    _sync(db_session, changed, naive, T0 + timedelta(seconds=1))
    (card,) = naive.cards.values()
    assert card["body"] == frozen["body"] and "+1 someone else" not in card["body"]


class TimesOutAfterCreating(FakeNaive):
    """The first create reaches Naïve, then the client times out (no status)."""

    def __init__(self):
        super().__init__(polls_to_done=99)
        self.timeouts = 1

    def create_card(self, title, body, assignee, idempotency_key):
        card = super().create_card(title, body, assignee, idempotency_key)
        if self.timeouts:
            self.timeouts -= 1
            raise NaiveError("Naïve POST timed out")
        return card


def test_a_retry_after_a_settings_change_links_the_existing_card(db_session, settings, arm):
    """Review finding 1: the retry must carry the first body, or Naïve answers 409 forever."""
    incident_id = _incident(db_session, arm)
    _triage(db_session, settings)
    naive = TimesOutAfterCreating()
    _sync(db_session, settings, naive)
    order = _order(db_session, incident_id)
    assert order.status == "pending" and len(naive.cards) == 1  # the card exists on Naïve

    edited = settings.model_copy(
        update={
            "work_order_phone": "+1 new teammate",
            "work_order_deliver_to": "somewhere else",
            "naive_ops_agent": "fleet-ops-2",
        }
    )
    _sync(db_session, edited, naive, datetime.fromisoformat(order.raw["retry_at"]))
    (card,) = naive.cards.values()
    assert order.naive_card_id == card["id"] and order.status in ("posted", "in_progress")
    assert card["assignee"] == "fleet-ops" and "new teammate" not in card["body"]


def test_a_changed_board_is_not_resent_blindly(db_session, settings, arm):
    incident_id = _incident(db_session, arm)
    _triage(db_session, settings)
    _sync(db_session, settings, AlwaysFails(503))
    other = FakeNaive(board_id="agt_other")
    order = _order(db_session, incident_id)
    _sync(db_session, settings, other, datetime.fromisoformat(order.raw["retry_at"]))
    assert order.status == "needs_human" and other.cards == {}
    last_why = workorders.history(db_session, order.id)[-1]["why"]
    assert "first sent to board agt_fake" in last_why


def test_live_mode_refuses_the_placeholder_phone(db_session, settings, arm):
    live = settings.model_copy(update={"naive_mode": "live"})  # the client is still fake
    incident_id = _incident(db_session, arm)
    _triage(db_session, live)
    naive = FakeNaive()
    _sync(db_session, live, naive)
    order = _order(db_session, incident_id)
    assert order.status == "needs_human" and naive.cards == {}
    assert "request" not in order.raw  # nothing was frozen with the placeholder
    workorders.resolve(db_session, order.id, "approve", "kent", "phone set", T0, naive_mode="live")
    with_phone = live.model_copy(update={"work_order_phone": "+1 teammate"})
    _sync(db_session, with_phone, naive, T0 + timedelta(seconds=1))
    (card,) = naive.cards.values()
    assert "· +1 teammate" in card["body"]


def test_live_never_posts_an_order_created_in_fake_mode(db_session, settings, arm):
    """Decision (c): the fake backlog goes to a person instead of out to Naïve."""
    incident_id = _incident(db_session, arm)
    _triage(db_session, settings)  # fake mode
    live = settings.model_copy(update={"naive_mode": "live", "work_order_phone": "+1 mate"})
    naive = FakeNaive()
    _sync(db_session, live, naive)
    order = _order(db_session, incident_id)
    assert order.status == "needs_human" and naive.cards == {}
    last_why = workorders.history(db_session, order.id)[-1]["why"]
    assert "created in fake mode; not posted live" in last_why
    with pytest.raises(ApiError) as err:
        workorders.resolve(db_session, order.id, "approve", "kent", "go", T0, naive_mode="live")
    assert err.value.error == "wrong_mode"
    workorders.resolve(db_session, order.id, "deny", "kent", "fake backlog", T0, naive_mode="live")
    assert order.status == "denied"


def test_a_frozen_fleet_holds_new_cards_but_keeps_polling(db_session, settings, arm):
    """Decision (a): the kill switch stops posting, not reading."""
    out = _incident(db_session, arm)
    _triage(db_session, settings)
    naive = FakeNaive(polls_to_done=3)
    _sync(db_session, settings, naive)  # one card out before the freeze
    db_session.add(FleetState(fleet_id="default", frozen=True, frozen_by="kent"))
    db_session.flush()
    held = _incident(db_session, arm, kind="gripper_stall", at=T0)
    _triage(db_session, settings.model_copy(update={"orders_per_robot_day_max": 5}))
    for i in (1, 2, 3):
        _sync(db_session, settings, naive, T0 + timedelta(seconds=15 * i))
    assert len(naive.cards) == 1
    assert _order(db_session, out).status == "done"  # still polled while frozen
    order = _order(db_session, held)
    assert order.status == "pending"
    held_events = events.find(db_session, {"work_order": order.id}, kinds=["workorder.post_held"])
    assert len(held_events) == 1 and held_events[0].why.startswith("Held: fleet frozen")

    db_session.get(FleetState, "default").frozen = False
    _sync(db_session, settings, naive, T0 + timedelta(seconds=60))
    assert order.status in ("posted", "in_progress") and len(naive.cards) == 2
    assert "held_frozen" not in order.raw


def test_http_naive_gets_the_idempotency_key_and_polls_by_id(db_session, settings, arm):
    incident_id = _incident(db_session, arm)
    _triage(db_session, settings)
    seen = []
    card = {"id": "crd_1", "status": "todo", "note": None, "updated_at": "t1"}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.method == "POST":
            return httpx.Response(201, json=card)
        assert request.url.path == "/v1/boards/agt_ceo/cards/crd_1"
        return httpx.Response(200, json=card)

    http = httpx.Client(transport=httpx.MockTransport(handler))
    naive = HttpNaive("sk_test", "agt_ceo", http_client=http)
    s = settings.model_copy(update={"naive_api_key": SecretStr("sk_test")})
    _sync(db_session, s, naive)
    order = _order(db_session, incident_id)
    post = seen[0]
    assert post.url.path == "/v1/boards/agt_ceo/cards"
    assert post.headers["Idempotency-Key"] == order.id
    assert post.headers["Vetta-Version"] == "2026-09-30"
    assert json.loads(post.content)["assignee"] == "fleet-ops"
    # The card hasn't changed since it was posted (same updated_at): nothing to act on.
    assert order.status == "posted" and order.naive_card_id == "crd_1"

    card.update(status="done", note="RESULT: ordered ORDER_REF=PO-9 COST_USD=$21.50\nthanks")
    card["updated_at"] = "t2"
    _sync(db_session, s, naive, T0 + timedelta(seconds=15))
    assert order.status == "done" and order.result == "Ordered: ref PO-9, $21.50"
    _sync(db_session, s, naive, T0 + timedelta(seconds=30))
    assert len(seen) == 3  # a done order is no longer polled


# --- polling ---------------------------------------------------------------------------------


class CountingNaive(FakeNaive):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.reads = 0

    def get_card(self, card_id):
        self.reads += 1
        return super().get_card(card_id)


def _edit(card, **changes):
    """Change a fake card the way Naïve would: with a new `updated_at`."""
    card.update(changes, updated_at=f"{card['updated_at']}+")


def test_a_parked_card_needs_a_person_until_naive_unblocks_it(db_session, settings, arm):
    incident_id = _incident(db_session, arm)
    _triage(db_session, settings)
    naive = FakeNaive(polls_to_done=99)
    _sync(db_session, settings, naive)
    order = _order(db_session, incident_id)
    card = naive.cards[order.naive_card_id]
    naive.comments[card["id"]].append({"body": "Supplier wants a card number"})
    _edit(card, status="blocked")
    _sync(db_session, settings, naive, T0 + timedelta(seconds=15))
    assert order.status == "needs_human"
    assert order.result == "Parked by Naïve: Supplier wants a card number"
    _edit(card, status="doing")
    _sync(db_session, settings, naive, T0 + timedelta(seconds=30))
    assert order.status == "in_progress"


def test_polling_acts_on_changes_only_and_stops_when_the_card_is_done(db_session, settings, arm):
    """Review finding 4: a new comment on a parked card, then a bad `done` note, both
    refresh the result; after Naïve's `done` the card is no longer read."""
    incident_id = _incident(db_session, arm)
    _triage(db_session, settings)
    naive = CountingNaive(polls_to_done=99)
    _sync(db_session, settings, naive)
    order = _order(db_session, incident_id)
    card = naive.cards[order.naive_card_id]
    comments = naive.comments[card["id"]]
    comments.append({"body": "Supplier wants a card number"})
    _edit(card, status="blocked")
    _sync(db_session, settings, naive, T0 + timedelta(seconds=15))
    n_events = len(events.find(db_session, {"work_order": order.id}))
    _sync(db_session, settings, naive, T0 + timedelta(seconds=30))  # nothing changed
    assert len(events.find(db_session, {"work_order": order.id})) == n_events

    comments.append({"body": "Supplier will take an invoice"})
    _edit(card)
    _sync(db_session, settings, naive, T0 + timedelta(seconds=45))
    assert order.status == "needs_human"
    assert order.result == "Parked by Naïve: Supplier will take an invoice"
    updated = events.find(db_session, {"work_order": order.id}, kinds=["workorder.card_updated"])
    assert len(updated) == 1

    _edit(card, status="done", note="ordered it, all good")  # no RESULT line
    _sync(db_session, settings, naive, T0 + timedelta(seconds=60))
    assert order.status == "needs_human" and "without a valid RESULT" in order.result
    reads = naive.reads
    _sync(db_session, settings, naive, T0 + timedelta(seconds=75))
    assert naive.reads == reads  # Naïve's done is final: polling stopped


# --- resolve ---------------------------------------------------------------------------------


def test_deny_is_refused_while_a_card_is_live(db_session, settings, arm):
    """Review finding 2: denying an order whose card is running would orphan the card."""
    incident_id = _incident(db_session, arm)
    _triage(db_session, settings)
    naive = FakeNaive(polls_to_done=99)
    _sync(db_session, settings, naive)
    order = _order(db_session, incident_id)
    _sync(db_session, settings, naive, T0 + timedelta(seconds=15))
    assert order.status == "in_progress"
    with pytest.raises(ApiError) as err:
        workorders.resolve(db_session, order.id, "deny", "kent", "never mind", T0)
    assert err.value.status == 409 and err.value.error == "card_open"
    assert "close it on the board first" in err.value.detail
    assert order.status == "in_progress"


def test_approve_accepts_a_card_only_when_naive_says_done(db_session, settings, arm):
    """Review finding 3: a parked card is still open on Naïve, so it can't be accepted."""
    incident_id = _incident(db_session, arm)
    _triage(db_session, settings)
    naive = FakeNaive(polls_to_done=99)
    _sync(db_session, settings, naive)
    order = _order(db_session, incident_id)
    card = naive.cards[order.naive_card_id]
    _edit(card, status="blocked")
    _sync(db_session, settings, naive, T0 + timedelta(seconds=15))
    assert order.status == "needs_human"
    with pytest.raises(ApiError) as err:
        workorders.resolve(db_session, order.id, "approve", "kent", "looks fine", T0)
    assert err.value.error == "card_open" and order.status == "needs_human"
    with pytest.raises(ApiError):
        workorders.resolve(db_session, order.id, "deny", "kent", "nope", T0)

    _edit(card, status="done", note="STOPPED: supplier closed")
    _sync(db_session, settings, naive, T0 + timedelta(seconds=30))
    assert order.status == "stopped"
    workorders.resolve(db_session, order.id, "deny", "kent", "will order by hand", T0)
    assert order.status == "denied"


def test_resolve_rules(db_session, settings, arm):
    incident_id = _incident(db_session, arm)
    _triage(db_session, settings)
    order = _order(db_session, incident_id)
    with pytest.raises(ApiError) as err:
        workorders.resolve(db_session, order.id, "approve", "kent", "go", T0)
    assert err.value.error == "not_waiting"  # pending is already queued
    with pytest.raises(ApiError) as err:
        workorders.resolve(db_session, "wo_missing", "deny", "kent", "x", T0)
    assert err.value.status == 404


def test_a_bad_fleet_timezone_fails_at_startup(make_settings):
    with pytest.raises(ValidationError, match="FLEET_TIMEZONE 'Mars/Olympus'"):
        make_settings(fleet_timezone="Mars/Olympus")
    assert make_settings(fleet_timezone="America/Los_Angeles").fleet_timezone


# --- the deny / post race (real connections, committed rows) ---------------------------------


@pytest.fixture
def committed(engine, settings):
    """A device, a hardware incident and its pending order, committed for real so two
    connections can contend for the row; removed afterwards."""
    device_id = f"race-{uuid.uuid4().hex[:6]}"
    with Session(engine) as s:
        devices.register(s, device_id, actor="human:test", actor_class="human")
        incident_id = _incident(s, device_id)
        (order,) = _triage(s, settings)
        s.commit()
        order_id = order.id
    yield order_id
    with Session(engine) as s:
        s.execute(delete(Event).where(Event.refs.contains({"device": device_id})))
        s.execute(delete(WorkOrder).where(WorkOrder.device_id == device_id))
        s.execute(delete(Incident).where(Incident.id == incident_id))
        s.execute(delete(Device).where(Device.id == device_id))
        s.commit()


def test_sync_skips_an_order_a_person_is_denying(engine, settings, committed):
    naive = FakeNaive()
    with Session(engine) as person, Session(engine) as worker:
        workorders.resolve(person, committed, "deny", "kent", "not needed", T0)  # row locked
        assert _sync(worker, settings, naive)["pending"] == 0  # SKIP LOCKED
        worker.commit()
        person.commit()
        _sync(worker, settings, naive, T0 + timedelta(seconds=15))
        worker.commit()
    assert naive.cards == {}
    with Session(engine) as s:
        assert s.get(WorkOrder, committed).status == "denied"


def test_a_deny_cannot_land_while_sync_is_posting(engine, settings, committed):
    """Review finding 2: sync holds the row lock mid-post, so the deny waits (here: times
    out), and afterwards the order has a live card and deny is refused."""
    blocked = []

    class DenyMidPost(FakeNaive):
        def create_card(self, title, body, assignee, idempotency_key):
            card = super().create_card(title, body, assignee, idempotency_key)
            with Session(engine) as person:
                person.execute(text("SET LOCAL lock_timeout = '200ms'"))
                try:
                    workorders.resolve(person, committed, "deny", "kent", "stop", T0)
                except OperationalError:
                    blocked.append(True)
                person.rollback()
            return card

    naive = DenyMidPost(polls_to_done=99)
    with Session(engine) as worker:
        _sync(worker, settings, naive)
        worker.commit()
    assert blocked == [True] and len(naive.cards) == 1
    with Session(engine) as person:
        order = person.get(WorkOrder, committed)
        assert order.status in ("posted", "in_progress")
        with pytest.raises(ApiError) as err:
            workorders.resolve(person, committed, "deny", "kent", "stop", T0)
        assert err.value.error == "card_open"
