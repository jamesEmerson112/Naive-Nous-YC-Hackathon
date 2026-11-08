"""F14: a hardware fault with no catalogued part goes to the Naïve Technician
(docs/naive-technician/design.md §13; D30).

Fake mode throughout: `FixtureNaive` serves the recorded fake session (no network), and
`FakeShop` stands in for the vendor's Shopify JSON. The worker steps run through `run_tick`
with the fakes passed in, as `test_f6_work_order.py` does.
"""

import json
import uuid
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path

import pytest

from romulus_cloud.adapters.naive import FixtureNaive, NaiveError
from romulus_cloud.adapters.shop import FakeShop
from romulus_cloud.models import TechnicianCase
from romulus_cloud.services import incidents, technician, workorders
from romulus_cloud.worker import Step, run_tick

T0 = datetime(2026, 11, 7, 22, 0, tzinfo=UTC)
"""Worker time. The resolve route stamps events with the real clock, which is later."""
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "technician"
ORIGIN = "https://shop.wowrobo.com"
HANDLE = "feetech-sts3215-servo-12v-30kg-high-torque-servo-for-so-arm100"
C018 = "48037575033049"
EMAIL = "tech@example.com"


def _product(price_cents=1599, available=True):
    return {
        "title": "Feetech STS3215 Servo 12V 30KG",
        "variants": [
            {"id": C018, "title": "C018 12V", "price_cents": price_cents, "available": available},
            {"id": "48037575065817", "title": "5-pack", "price_cents": 7495, "available": True},
        ],
    }


def _naive():
    return FixtureNaive(FIXTURES / "joint_stall_fake.json", now=lambda: T0, play_seconds=0)


def _steps(settings, naive, shop):
    return [
        Step("workorders.triage_new", partial(workorders.triage_new, settings=settings)),
        Step("technician.open", partial(technician.open_cases, settings=settings)),
        Step(
            "technician.sync",
            partial(technician.sync, settings=settings, naive=naive, shop=shop),
        ),
    ]


def _tick(sessions, settings, naive, shop, now):
    results = run_tick(_steps(settings, naive, shop), sessions, now, {})
    assert set(results.values()) == {"ok"}, results


def _report_incident(client, device_id, **changes):
    body = json.loads((FIXTURES / "incident_joint_stall.json").read_text())
    body |= {
        "incident_id": str(uuid.uuid4()),
        "occurred_at": (T0 - timedelta(minutes=1)).isoformat(),
        **changes,
    }
    resp = client.post(f"/v1/devices/{device_id}/incidents", json=body)
    assert resp.status_code in (200, 201), resp.text
    return body["incident_id"]


def _only_order(client, incident_id):
    orders = [o for o in client.get("/v1/work-orders").json() if o["incident_id"] == incident_id]
    assert len(orders) == 1, orders
    return orders[0]


def _case(db_session, order_id) -> TechnicianCase:
    db_session.expire_all()
    case = db_session.query(TechnicianCase).filter_by(work_order_id=order_id).one()
    return case


def _status(db_session, incident_id):
    db_session.expire_all()
    _, story = incidents.story(db_session, incident_id)
    return story.status


@pytest.fixture
def settings(make_settings):
    """The app's settings for this module (the routes check TECHNICIAN_MODE too)."""
    return make_settings(technician_mode="fake", technician_email=EMAIL)


@pytest.fixture
def tech_settings(settings):
    return settings


def test_joint_stall_gets_a_diagnosis_an_email_and_a_staged_part_a_person_approves(
    client, seeded, sessions, db_session, tech_settings
):
    naive, shop = _naive(), FakeShop({(ORIGIN, HANDLE): _product()})
    incident_id = _report_incident(client, "sim-1")

    # Tick 1: triage parks it at needs_human with no part; a case opens; the session starts.
    _tick(sessions, tech_settings, naive, shop, T0)
    order = _only_order(client, incident_id)
    assert (order["status"], order["part"]) == ("needs_human", "(none)")
    case = _case(db_session, order["id"])
    assert (case.status, case.mode, case.reason) == ("diagnosing", "fake", "playbook_exhausted")
    assert case.naive_session_id and case.request["metadata"]["romulus_case"] == case.id
    assert case.request["output_schema"]["properties"]["part"]["properties"]["origin"] == {
        "type": "string",
        "enum": [ORIGIN],
    }
    # The case file lists the arm's servo variant only, and the incident as untrusted evidence.
    assert C018 in case.request["message"] and "48037674090713" not in case.request["message"]
    assert '<evidence untrusted="true">' in case.request["message"]
    assert _status(db_session, incident_id) == ("Naïve technician on it", "info")

    # Tick 2: the typed report arrives; the part is checked and staged; the email is queued.
    _tick(sessions, tech_settings, naive, shop, T0 + timedelta(seconds=3))
    case = _case(db_session, order["id"])
    assert case.status == "waiting"
    assert case.report["verdict"] == "Elbow blocked or servo failing: needs hands"
    assert case.actions["email"]["status"] == "sending" and naive.sent == []
    part = client.get(f"/v1/technician/cases/{case.id}").json()["technician_part"]
    assert part["cart_url"] == f"{ORIGIN}/cart/{C018}:1?storefront=true"
    assert (part["unit_usd"], part["qty"]) == ("15.99", 1)
    assert _status(db_session, incident_id) == ("diagnosed: waiting on you", "bad")

    # Tick 3: the email goes out from the Naïve inbox, to the address from settings only.
    _tick(sessions, tech_settings, naive, shop, T0 + timedelta(seconds=6))
    (sent,) = naive.sent
    assert sent["to"] == [EMAIL] and sent["idempotency_key"] == f"{case.id}:email"
    assert len(sent["subject"]) <= 45 and len(sent["text"]) <= 1200
    assert "http" not in sent["text"] and "wowrobo.com" not in sent["text"]
    case = _case(db_session, order["id"])
    assert case.actions["email"]["status"] == "sent"
    kinds = [e["kind"] for e in client.get(f"/v1/incidents/{incident_id}/story").json()["steps"]]
    assert kinds[-3:] == ["technician.opened", "technician.diagnosed", "technician.emailed"]

    # The page: measured numbers, the part card with its ticks, approve available.
    v = technician.view(db_session, case, settings=tech_settings)
    assert v["can_approve"] and v["part"]["staged"] and v["human_clicks"] == 0
    assert [c["ok"] for c in v["part"]["checks"]] == [True, True, True, True]
    assert v["badge"] == "fake (test fixture)" and v["cost_label"] == "$0.41"
    assert v["duration_label"] == "2 min 3 s" and v["email"]["status"] == "sent"
    assert v["feed"] and any(f["text"].startswith("Searched “") for f in v["feed"])
    assert any(f["text"].startswith("Read shop.wowrobo.com/") for f in v["feed"])
    page = client.get(f"/technician/{case.id}")
    assert page.status_code == 200 and "Elbow blocked or servo failing" in page.text
    assert client.get(f"/technician/{case.id}/partial").status_code == 200
    latest = client.get("/technician", follow_redirects=False)
    assert latest.status_code == 302 and latest.headers["location"] == f"/technician/{case.id}"

    # A person approves through the existing resolve route: order done, case done.
    resp = client.post(
        v["resolve_url"], json={"action": "approve", "by": "kent", "why": "checked the elbow"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "done"
    assert resp.json()["result"].startswith("Approved by kent: 1 × Feetech STS3215")
    case = _case(db_session, order["id"])
    assert case.status == "done"
    assert _status(db_session, incident_id) == ("part ordered", "good")
    v = technician.view(db_session, case, settings=tech_settings)
    assert v["human_clicks"] == 1 and v["approved_by"] == "kent" and not v["can_approve"]
    assert v["stages"][-1] == {"label": "approved by kent", "state": "done"}
    assert v["poll"] is False


def test_a_catalogued_fault_gets_no_case(
    client, seeded, sessions, db_session, tech_settings, example
):
    naive, shop = _naive(), FakeShop({(ORIGIN, HANDLE): _product()})
    body = example("incident_park_hardware") | {
        "incident_id": str(uuid.uuid4()),
        "occurred_at": (T0 - timedelta(minutes=1)).isoformat(),
    }
    assert body["kind"] == "servo_absent"
    assert client.post("/v1/devices/sim-1/incidents", json=body).status_code in (200, 201)
    _tick(sessions, tech_settings, naive, shop, T0)
    _tick(sessions, tech_settings, naive, shop, T0 + timedelta(seconds=3))
    order = _only_order(client, body["incident_id"])
    assert order["part"] == "sts3215-servo"
    assert db_session.query(TechnicianCase).count() == 0
    assert naive.requests == {} and naive.sent == []


@pytest.mark.parametrize(
    "product, reason",
    [
        (_product(price_cents=6000), "over ORDER_MAX_USD"),
        (_product(available=False), "out of stock"),
    ],
)
def test_a_part_that_fails_the_gate_stays_off_the_order_but_the_email_goes(
    client, seeded, sessions, db_session, tech_settings, product, reason
):
    naive, shop = _naive(), FakeShop({(ORIGIN, HANDLE): product})
    incident_id = _report_incident(client, "sim-2")
    for i in range(3):
        _tick(sessions, tech_settings, naive, shop, T0 + timedelta(seconds=3 * i))
    order = _only_order(client, incident_id)
    case = _case(db_session, order["id"])
    assert case.status == "waiting" and len(naive.sent) == 1
    assert client.get(f"/v1/technician/cases/{case.id}").json()["technician_part"] is None
    v = technician.view(db_session, case, settings=tech_settings)
    assert v["part"]["staged"] is False and v["part"]["cart_url"] is None
    assert reason in v["part"]["blocked_reason"]
    assert not v["can_approve"] and v["can_deny"]
    # Without a staged part, approve is still refused, as before; deny closes it.
    resp = client.post(
        f"/v1/work-orders/{order['id']}/resolve",
        json={"action": "approve", "by": "kent", "why": "try"},
    )
    assert resp.status_code == 409 and resp.json()["error"] == "no_part"
    resp = client.post(
        f"/v1/work-orders/{order['id']}/resolve",
        json={"action": "deny", "by": "kent", "why": "fixed by hand"},
    )
    assert resp.json()["status"] == "denied"
    assert _case(db_session, order["id"]).status == "stopped"


def test_manual_open_takes_a_mode_and_is_safe_to_repeat(
    client, seeded, sessions, db_session, make_settings
):
    """In live mode a sim arm's order gets no case on its own (K13); the button opens one."""
    settings = make_settings(technician_mode="fake")
    incident_id = _report_incident(client, "sim-1")
    run_tick(
        [Step("workorders.triage_new", partial(workorders.triage_new, settings=settings))],
        sessions,
        T0,
        {},
    )
    order = _only_order(client, incident_id)
    live = make_settings(technician_mode="live")  # no key, no agent: open_cases skips sim-1
    assert technician.open_cases(db_session, T0, settings=live) == []
    first = client.post(f"/v1/work-orders/{order['id']}/technician?mode=fake")
    assert first.status_code == 200, first.text
    again = client.post(f"/v1/work-orders/{order['id']}/technician?mode=fake")
    assert again.json()["id"] == first.json()["id"] and first.json()["reason"] == "manual"
    refused = client.post(f"/v1/work-orders/{order['id']}/technician?mode=replay")
    assert refused.json()["id"] == first.json()["id"]  # an open case comes back as it is
    stop = client.post(f"/v1/technician/cases/{first.json()['id']}/stop", json={"by": "kent"})
    assert stop.status_code == 200 and stop.json()["status"] == "stopped"


class _HeldPost(FixtureNaive):
    """Naïve under load: the POST creates the session but is held open while it sits queued,
    so the client times out (`in_flight`). The session exists and `find_session` finds it."""

    def create_session(self, body, *, idempotency_key):
        super().create_session(body, idempotency_key=idempotency_key)
        raise NaiveError("POST /sessions timed out", code="in_flight", pending=True)


def test_a_queued_session_is_found_by_case_id_not_created_twice(
    client, seeded, sessions, db_session, tech_settings
):
    naive = _HeldPost(FIXTURES / "joint_stall_fake.json", now=lambda: T0, play_seconds=0)
    shop = FakeShop({(ORIGIN, HANDLE): _product()})
    incident_id = _report_incident(client, "sim-3")
    _tick(sessions, tech_settings, naive, shop, T0)
    order = _only_order(client, incident_id)
    case = _case(db_session, order["id"])
    assert case.status == "open" and case.why == "Naïve queued the session; waiting"
    assert case.feed[-1]["text"] == "Naïve queued the session (platform busy)"
    frozen = dict(case.request)
    _tick(sessions, tech_settings, naive, shop, T0 + timedelta(seconds=3))
    case = _case(db_session, order["id"])
    assert case.status == "diagnosing" and case.naive_session_id
    assert case.request == frozen and list(naive.requests) == [case.id]  # one key, one body
