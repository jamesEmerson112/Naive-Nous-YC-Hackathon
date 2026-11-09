"""F2 on screen, redesign (docs/impeccable-redesign/BUILD.md, "Tones"): an incident waiting
on a person shows in the `you` tone (sun) on the timeline and its own page, while the story
keeps the domain's tone. Everything goes through HTTP with the contract examples."""

import html

import pytest

from romulus_cloud import events
from romulus_cloud.services import incidents

RULES = "0b6c6f3e-8d2a-4c1b-b5e3-6f7a8b9c0d1e"  # incident_rules_patch.json


@pytest.fixture
def incident(client, seeded, example):
    assert client.post("/v1/devices/arm-1/checkin", json=example("checkin")).status_code == 200
    client.post("/v1/devices/arm-1/incidents", json=example("incident_rules_patch"))
    return RULES


def _work_order(db_session, status):
    events.record(
        db_session,
        "romulus",
        "automation",
        "workorder.status_changed",
        {
            "incident": RULES,
            "device": "arm-1",
            "work_order": "wo_3d7cd6464e2e4980",
            "status": status,
        },
        f"The work order moved to {status}.",
    )
    db_session.commit()


def _text(resp) -> str:
    assert resp.status_code == 200, resp.text
    return html.unescape(resp.text)


def test_a_work_order_that_needs_a_person_reads_as_you(client, incident, db_session):
    _work_order(db_session, "needs_human")

    rows = _text(client.get("/partials/incidents"))
    assert '<li class="incident tone-edge-you">' in rows
    assert '<span class="status tone-you">work order needs a person</span>' in rows
    page = _text(client.get(f"/incidents/{RULES}"))
    assert "tone-you" in page and "work order needs a person" in page
    # The domain is untouched: the story still says bad.
    db_session.expire_all()
    _, story = incidents.story(db_session, RULES)
    assert story.status == ("work order needs a person", "bad")


def test_other_statuses_keep_their_tone(client, incident, db_session):
    _work_order(db_session, "stopped")
    rows = _text(client.get("/partials/incidents"))
    assert "tone-you" not in rows
    assert '<span class="status tone-bad">order stopped</span>' in rows
