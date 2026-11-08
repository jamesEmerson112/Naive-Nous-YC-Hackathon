"""F2 on screen, redesign (docs/impeccable-redesign/README.md §4, "Fleet"): the top of `/`
says where the fleet stands in one sentence, picked in order (frozen, needs you, still broken,
agent working, all healthy, no check-ins yet), and a your-turn card links to the technician
case when one exists, else to the incident. Everything goes through HTTP."""

import html

import pytest

from romulus_cloud import events
from romulus_cloud.models import TechnicianCase

RULES = "0b6c6f3e-8d2a-4c1b-b5e3-6f7a8b9c0d1e"  # incident_rules_patch.json
WO = "wo_3d7cd6464e2e4980"


def _text(resp) -> str:
    assert resp.status_code == 200, resp.text
    return html.unescape(resp.text)


@pytest.fixture
def arm(client, seeded, example):
    assert client.post("/v1/devices/arm-1/checkin", json=example("checkin")).status_code == 200
    return "arm-1"


def _needs_human(db_session):
    events.record(
        db_session,
        "romulus",
        "automation",
        "workorder.status_changed",
        {"incident": RULES, "device": "arm-1", "work_order": WO, "status": "needs_human"},
        "The work order needs a person.",
    )
    db_session.commit()


def test_no_check_ins_yet(client):
    assert "Waiting for the first check-in." in _text(client.get("/partials/overview"))
    assert 'class="turn"' not in _text(client.get("/"))


def test_an_overlay_on_trial_reads_as_the_healer_working(client, arm, example):
    client.post("/v1/devices/arm-1/incidents", json=example("incident_rules_patch"))
    top = _text(client.get("/partials/overview"))
    assert "The healer is working on arm-1." in top
    assert "arm-1, camera occluded: overlay on trial. Nothing needs a person yet." in top
    assert 'class="turn"' not in top


def test_a_reverted_overlay_reads_as_still_open(client, arm, example):
    client.post("/v1/devices/arm-1/incidents", json=example("incident_d1_code_patch"))
    client.post("/v1/devices/arm-1/overlays/ov-d1-0001", json=example("overlay_status_reverted"))
    top = _text(client.get("/partials/overview"))
    assert "1 incident still open." in top
    assert "Newest: arm-1, unexpected release: Gripper opened while holding" in top


def test_needs_you_links_the_incident_when_there_is_no_case(client, arm, example, db_session):
    client.post("/v1/devices/arm-1/incidents", json=example("incident_rules_patch"))
    _needs_human(db_session)
    top = _text(client.get("/partials/overview"))
    assert '<span class="is-you">1 arm needs you.</span>' in top
    assert f'<a class="turn" href="/incidents/{RULES}">' in top
    assert "Work order needs a person: open the incident to decide." in top
    assert "<b>1</b> waiting on a person" in top


def test_needs_you_links_the_technician_case_with_its_ask(client, arm, example, db_session):
    client.post("/v1/devices/arm-1/incidents", json=example("incident_rules_patch"))
    _needs_human(db_session)
    db_session.add(
        TechnicianCase(
            id="tc_flowtest0001",
            work_order_id=WO,
            incident_id=RULES,
            device_id="arm-1",
            reason="manual",
            mode="fake",
            status="waiting",
            actions={
                "part": {
                    "staged": True,
                    "title": "Feetech STS3215 C018 12V",
                    "unit_usd": "15.99",
                    "vendor_label": "WowRobo",
                }
            },
        )
    )
    db_session.commit()
    top = _text(client.get("/partials/overview"))
    assert '<a class="turn" href="/technician/tc_flowtest0001">' in top
    assert "Approve a spare part: Feetech STS3215 C018 12V, $15.99 at WowRobo" in top
    assert "A Naïve technician diagnosed it and staged a spare part for you to approve." in top


def test_frozen_comes_first(client, arm, example, db_session):
    client.post("/v1/devices/arm-1/incidents", json=example("incident_rules_patch"))
    _needs_human(db_session)
    events.record(
        db_session, "human:kent", "human", "fleet.frozen", {}, "Kent froze the fleet for the demo."
    )
    db_session.commit()
    top = _text(client.get("/partials/overview"))
    assert "Fleet frozen." in top and "Kent froze the fleet for the demo." in top
    assert "needs you" not in top
    assert 'class="turn"' in top  # the person's turn still shows under the sentence
