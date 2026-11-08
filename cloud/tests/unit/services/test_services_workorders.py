"""services/workorders.py: triage, cap counting, posting, polling, resolving. No network:
FakeNaive, or HttpNaive on an `httpx.MockTransport`."""

import json
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from pydantic import SecretStr

from romulus_cloud import events
from romulus_cloud.adapters.naive import FakeNaive, HttpNaive, NaiveError
from romulus_cloud.domain.triage import parse_catalog
from romulus_cloud.errors import ApiError
from romulus_cloud.models import WorkOrder
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
    last = events.find(db_session, {"work_order": order.id})[-1]
    assert f"failed {workorders.MAX_POST_ATTEMPTS} times in a row" in last.why
    retried = events.find(db_session, {"work_order": order.id}, kinds=["workorder.post_retried"])
    assert len(retried) == workorders.MAX_POST_ATTEMPTS - 1


def test_a_permanent_error_needs_a_person_and_approve_requeues(db_session, settings, arm):
    incident_id = _incident(db_session, arm)
    _triage(db_session, settings)
    _sync(db_session, settings, AlwaysFails(400, "invalid_request"))
    order = _order(db_session, incident_id)
    assert order.status == "needs_human" and order.naive_card_id is None
    workorders.resolve(db_session, order.id, "approve", "kent", "fixed the body", T0)
    assert order.status == "pending" and order.raw is None
    naive = FakeNaive()
    _sync(db_session, settings, naive, T0 + timedelta(seconds=1))
    assert order.status == "in_progress" and len(naive.cards) == 1


def test_live_mode_refuses_the_placeholder_phone(db_session, settings, arm):
    incident_id = _incident(db_session, arm)
    _triage(db_session, settings)
    live = settings.model_copy(update={"naive_mode": "live"})  # the client is still fake
    naive = FakeNaive()
    _sync(db_session, live, naive)
    order = _order(db_session, incident_id)
    assert order.status == "needs_human" and naive.cards == {}
    workorders.resolve(db_session, order.id, "approve", "kent", "phone set", T0)
    with_phone = live.model_copy(update={"work_order_phone": "+1 teammate"})
    _sync(db_session, with_phone, naive, T0 + timedelta(seconds=1))
    (card,) = naive.cards.values()
    assert "· +1 teammate" in card["body"]


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
    assert order.status == "in_progress" and order.naive_card_id == "crd_1"

    card.update(status="done", note="RESULT: ordered ORDER_REF=PO-9 COST_USD=$21.50\nthanks")
    card["updated_at"] = "t2"
    _sync(db_session, s, naive, T0 + timedelta(seconds=15))
    assert order.status == "done" and order.result == "Ordered: ref PO-9, $21.50"
    _sync(db_session, s, naive, T0 + timedelta(seconds=30))
    assert len(seen) == 3  # a done order is no longer polled


# --- polling ---------------------------------------------------------------------------------


def test_a_parked_card_needs_a_person_until_naive_unblocks_it(db_session, settings, arm):
    incident_id = _incident(db_session, arm)
    _triage(db_session, settings)
    naive = FakeNaive(polls_to_done=99)
    _sync(db_session, settings, naive)
    order = _order(db_session, incident_id)
    card = naive.cards[order.naive_card_id]
    card["status"] = "blocked"
    naive.comments[card["id"]].append({"body": "Supplier wants a card number"})
    _sync(db_session, settings, naive, T0 + timedelta(seconds=15))
    assert order.status == "needs_human"
    assert order.result == "Parked by Naïve: Supplier wants a card number"
    card["status"] = "doing"
    _sync(db_session, settings, naive, T0 + timedelta(seconds=30))
    assert order.status == "in_progress"


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
