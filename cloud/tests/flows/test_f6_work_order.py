"""F6: a hardware fault becomes a work order, a Naïve card, and a result in the story.

Everything runs on `FakeNaive` (no network; K13). The worker steps run through
`run_tick` with the fake client and test settings passed in, as the worker would call them.
"""

import uuid
from datetime import UTC, datetime, timedelta
from functools import partial

from fake_device.arm import FakeArm
from fake_device.scenarios import SCENARIOS
from romulus_cloud.adapters.naive import FakeNaive, NaiveError
from romulus_cloud.services import workorders
from romulus_cloud.worker import Step, run_tick

T0 = datetime(2026, 11, 7, 22, 0, tzinfo=UTC)
"""Worker time. The resolve route stamps events with the real clock, which is later."""


def _steps(settings, naive):
    return [
        Step("workorders.triage_new", partial(workorders.triage_new, settings=settings)),
        Step("workorders.sync", partial(workorders.sync, settings=settings, naive=naive)),
    ]


def _tick(sessions, settings, naive, now):
    results = run_tick(_steps(settings, naive), sessions, now, {})
    assert set(results.values()) == {"ok"}, results


def _report_park(client, example, device_id, **changes):
    body = example("incident_park_hardware") | {
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


def _story(client, incident_id):
    return client.get(f"/v1/incidents/{incident_id}/story").json()


def test_park_hardware_from_the_fake_device_becomes_a_card_and_an_ordered_part(
    client, device_http, seeded, sessions, make_settings, tmp_path
):
    settings = make_settings(work_order_deliver_to="YC bench 1")
    naive = FakeNaive(polls_to_done=3)
    arm = FakeArm(device_http, "sim-1", tmp_path / "sim-1")
    for step in SCENARIOS["park"](arm):
        step.run(T0)
    (incident,) = [i for i in client.get("/v1/incidents").json() if i["device_id"] == "sim-1"]
    assert incident["kind"] == "servo_absent" and incident["suspected_cause"] == "hardware"

    _tick(sessions, settings, naive, T0 + timedelta(seconds=2))
    order = _only_order(client, incident["incident_id"])
    assert order["status"] == "in_progress" and order["part"] == "sts3215-servo"
    (card,) = naive.cards.values()
    assert order["naive_card_id"] == card["id"]
    assert card["assignee"] == "fleet-ops"
    # Self-contained body: device, part, symptom, phone and address from settings (K13).
    assert "Robot: sim-1 (so101, sim)" in card["body"]
    assert "Request: order 1 × Feetech STS3215 servo (12 V)" in card["body"]
    assert "Wrist servo id 5 stopped answering" in card["body"]
    assert "· TEAMMATE_PHONE" in card["body"] and "Deliver to: YC bench 1" in card["body"]
    assert (
        "- Call only this number. Never contact any other supplier or business." in (card["body"])
    )

    for i in (2, 3):
        _tick(sessions, settings, naive, T0 + timedelta(seconds=2 + 15 * i))
    order = client.get(f"/v1/work-orders/{order['id']}").json()
    assert order["status"] == "done"
    assert order["result"] == "Ordered: ref FAKE-0001, $12.00"
    kinds = [e["kind"] for e in order["events"]]
    assert kinds == [
        "workorder.created",
        "workorder.card_posted",
        "workorder.status_changed",
        "workorder.status_changed",
    ]

    story = _story(client, incident["incident_id"])
    assert story["chain"].startswith("servo_absent → agent acted → ")
    wo_steps = [s for s in story["steps"] if s["kind"].startswith("workorder.")]
    assert " → ".join(s["label"] for s in wo_steps) == (
        f"work order {order['id']} → card posted → in progress"
        " → done: RESULT: ordered ORDER_REF=FAKE-0001 COST_USD=12.00"
    )
    assert wo_steps[0]["why"].startswith("servo_absent on sim-1 is unresolved and the healer")
    assert wo_steps[-1]["actor"].startswith("naive:ses_fake")
    assert len(naive.cards) == 1


def test_over_cap_is_blocked_until_a_person_approves(
    client, seeded, sessions, make_settings, example
):
    settings = make_settings(order_max_usd=20)  # the servo's MAX_USD is 40
    naive = FakeNaive(polls_to_done=1)
    incident_id = _report_park(client, example, "arm-1")

    _tick(sessions, settings, naive, T0)
    _tick(sessions, settings, naive, T0 + timedelta(seconds=15))
    order = _only_order(client, incident_id)
    assert order["status"] == "blocked_over_cap" and naive.cards == {}
    story = _story(client, incident_id)
    assert "MAX_USD $40.00 is over ORDER_MAX_USD $20.00" in story["steps"][-1]["why"]
    assert story["chain"].endswith(f"work order {order['id']} (blocked over cap)")

    resp = client.post(
        f"/v1/work-orders/{order['id']}/resolve",
        json={"action": "approve", "by": "kent", "why": "servo price checked"},
    )
    assert resp.status_code == 200 and resp.json()["status"] == "pending"
    _tick(sessions, settings, naive, T0 + timedelta(seconds=30))
    order = client.get(f"/v1/work-orders/{order['id']}").json()
    assert order["status"] == "done" and len(naive.cards) == 1
    approved = [e for e in order["events"] if e["actor"] == "human:kent"]
    assert approved and approved[0]["refs"]["to"] == "pending"


def test_deny_closes_a_blocked_order_and_nothing_is_posted(
    client, seeded, sessions, make_settings, example
):
    settings = make_settings(order_max_usd=20)
    naive = FakeNaive()
    incident_id = _report_park(client, example, "arm-1")
    _tick(sessions, settings, naive, T0)
    order = _only_order(client, incident_id)

    resp = client.post(
        f"/v1/work-orders/{order['id']}/resolve",
        json={"action": "deny", "by": "kent", "why": "fixing the cable by hand"},
    )
    assert resp.json()["status"] == "denied"
    _tick(sessions, settings, naive, T0 + timedelta(seconds=15))
    assert naive.cards == {}
    assert _story(client, incident_id)["chain"].endswith("→ denied")
    again = client.post(
        f"/v1/work-orders/{order['id']}/resolve",
        json={"action": "approve", "by": "kent", "why": "changed my mind"},
    )
    assert again.status_code == 409 and again.json()["error"] == "work_order_closed"


def test_a_stopped_card_waits_for_a_person(client, seeded, sessions, make_settings, example):
    settings = make_settings()
    naive = FakeNaive(polls_to_done=1, outcome="stopped", stop_reason="servo out of stock")
    incident_id = _report_park(client, example, "arm-1")
    _tick(sessions, settings, naive, T0)
    order = _only_order(client, incident_id)
    assert order["status"] == "stopped"
    assert order["result"] == "Stopped: servo out of stock"
    resp = client.get("/v1/work-orders", params={"status": "stopped"}).json()
    assert [o["id"] for o in resp] == [order["id"]]


class TimesOutOnce(FakeNaive):
    """The first create reaches Naïve but the client times out: the card exists, and the
    retry with the same key gets a 409 `idempotency_conflict` once (still in flight)."""

    conflicts = 1

    def create_card(self, title, body, assignee, idempotency_key):
        card = super().create_card(title, body, assignee, idempotency_key)
        if self.conflicts:
            self.conflicts -= 1
            raise NaiveError("409 idempotency_conflict", status=409, code="idempotency_conflict")
        return card


def test_idempotent_re_post_after_a_timeout_makes_one_card(
    client, seeded, sessions, make_settings, example
):
    settings = make_settings()
    naive = TimesOutOnce(polls_to_done=1)
    incident_id = _report_park(client, example, "arm-1")

    _tick(sessions, settings, naive, T0)
    order = _only_order(client, incident_id)
    assert order["status"] == "pending" and order["naive_card_id"] is None
    _tick(sessions, settings, naive, T0 + timedelta(seconds=5))  # inside the 15 s backoff
    assert _only_order(client, incident_id)["status"] == "pending"

    _tick(sessions, settings, naive, T0 + timedelta(seconds=16))
    order = client.get(f"/v1/work-orders/{order['id']}").json()
    assert order["status"] == "done" and len(naive.cards) == 1
    retried = [e for e in order["events"] if e["kind"] == "workorder.post_retried"]
    assert len(retried) == 1 and "idempotency_conflict" in retried[0]["why"]


def test_an_overspend_in_the_note_needs_a_person(client, seeded, sessions, make_settings, example):
    settings = make_settings()
    naive = FakeNaive(polls_to_done=1, cost_usd="99.00")
    incident_id = _report_park(client, example, "arm-1")
    _tick(sessions, settings, naive, T0)
    order = _only_order(client, incident_id)
    assert order["status"] == "needs_human"
    assert "over MAX_USD $40" in order["result"]
    story = _story(client, incident_id)
    assert story["chain"].endswith(
        "needs human: RESULT: ordered ORDER_REF=FAKE-0001 COST_USD=99.00"
    )

    # A person checks the overspend and accepts it.
    resp = client.post(
        f"/v1/work-orders/{order['id']}/resolve",
        json={"action": "approve", "by": "kent", "why": "price went up, fine"},
    )
    assert resp.json()["status"] == "done" and len(naive.cards) == 1


def test_one_work_order_per_incident_even_when_triage_and_reports_repeat(
    client, seeded, sessions, make_settings, example, count_events
):
    settings = make_settings()
    naive = FakeNaive()
    body = example("incident_park_hardware") | {
        "incident_id": str(uuid.uuid4()),
        "occurred_at": (T0 - timedelta(minutes=1)).isoformat(),
    }
    client.post("/v1/devices/arm-1/incidents", json=body)
    _tick(sessions, settings, naive, T0)
    client.post("/v1/devices/arm-1/incidents", json=body)  # the device retries
    _tick(sessions, settings, naive, T0 + timedelta(seconds=1))
    _tick(sessions, settings, naive, T0 + timedelta(seconds=2))
    _only_order(client, body["incident_id"])
    assert count_events("workorder.created", {"incident": body["incident_id"]}) == 1
    assert len(naive.cards) == 1


def test_per_robot_cap_blocks_a_second_order_the_same_day(
    client, seeded, sessions, make_settings, example
):
    settings = make_settings(orders_per_robot_day_max=1)
    naive = FakeNaive()
    first = _report_park(client, example, "arm-1")
    _tick(sessions, settings, naive, T0)
    second = _report_park(client, example, "arm-1")
    other = _report_park(client, example, "sim-2")
    _tick(sessions, settings, naive, T0 + timedelta(seconds=1))
    assert _only_order(client, first)["status"] != "blocked_over_cap"
    assert _only_order(client, second)["status"] == "blocked_over_cap"
    assert _only_order(client, other)["status"] != "blocked_over_cap"


def test_resolve_needs_a_named_person_and_a_reason(
    client, seeded, sessions, make_settings, example
):
    settings = make_settings(order_max_usd=20)
    incident_id = _report_park(client, example, "arm-1")
    _tick(sessions, settings, FakeNaive(), T0)
    order = _only_order(client, incident_id)
    for body in ({"by": "   ", "why": "ok"}, {"by": "kent", "why": " "}):
        resp = client.post(
            f"/v1/work-orders/{order['id']}/resolve", json={"action": "deny", **body}
        )
        assert resp.status_code == 422, resp.text
    assert _only_order(client, incident_id)["status"] == "blocked_over_cap"
