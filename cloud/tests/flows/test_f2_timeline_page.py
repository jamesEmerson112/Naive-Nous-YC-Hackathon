"""F2 on screen (M2, D10): the timeline and story pages show an incident someone else can
follow, built only from events. Everything goes through HTTP with the contract examples."""

import html

import pytest

from romulus_cloud import events

RULES = "0b6c6f3e-8d2a-4c1b-b5e3-6f7a8b9c0d1e"  # incident_rules_patch.json, 22:02:11Z
D1 = "3f2b8c1e-6a4d-4e7b-9c21-5d8e0f1a2b3c"  # incident_d1_code_patch.json, 23:14:05Z


@pytest.fixture
def arm(client, seeded, example):
    assert client.post("/v1/devices/arm-1/checkin", json=example("checkin")).status_code == 200
    return "arm-1"


def _text(resp) -> str:
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/html")
    return html.unescape(resp.text)


def test_timeline_empty_fleet(client):
    page = _text(client.get("/"))
    assert "No incidents yet" in page
    assert "Freeze fleet" in page and "/v1/fleet/freeze" in page
    assert "/static/htmx.min.js" in page and 'hx-get="/partials/incidents"' in page


def test_timeline_lists_incidents_newest_first_with_status(client, arm, example):
    client.post("/v1/devices/arm-1/incidents", json=example("incident_rules_patch"))
    client.post("/v1/devices/arm-1/incidents", json=example("incident_d1_code_patch"))
    client.post("/v1/devices/arm-1/overlays/ov-d1-0001", json=example("overlay_status_held"))

    page = _text(client.get("/"))
    assert page.index(f"/incidents/{D1}") < page.index(f"/incidents/{RULES}")
    assert "unexpected_release" in page and "camera_occluded" in page
    assert "overlay held" in page  # D1's status
    assert "overlay on trial" in page  # the rules incident has no held/reverted yet
    assert "unexpected_release → agent acted → overlay ov-d1-0001 → held" in page
    assert "3:14:05 PM" in page and "Sat Nov 7" in page  # 23:14:05Z in Pacific time
    # Fleet strip: seeded devices with their versions and health.
    for device in ("arm-1", "sim-1", "sim-2", "sim-3"):
        assert device in page
    assert "fw 1 · h 1" in page

    partial = _text(client.get("/partials/incidents"))
    assert "<html" not in partial and f"/incidents/{D1}" in partial
    fleet = _text(client.get("/partials/fleet"))
    assert "arm-1" in fleet and "<html" not in fleet


def test_story_page_reads_like_a_story(client, arm, example):
    client.post("/v1/devices/arm-1/incidents", json=example("incident_d1_code_patch"))
    client.post("/v1/devices/arm-1/overlays/ov-d1-0001", json=example("overlay_status_held"))

    page = _text(client.get(f"/incidents/{D1}"))
    story = client.get(f"/v1/incidents/{D1}/story").json()
    for step in story["steps"]:
        assert step["line"] in page
        assert step["why"] in page
    # Order on screen follows the story.
    positions = [page.index(s["why"]) for s in story["steps"]]
    assert positions == sorted(positions)
    assert "Gripper opened while holding" in page
    assert "3:14:05 PM" in page and "3:34:05 PM" in page and "20 min later" in page
    assert "same moment" in page  # the agent's actions share the incident's occurred_at
    assert 'class="badge actor-badge">agent' in page
    assert 'class="badge actor-badge">automation' in page
    assert "suspected cause: software" in page and "resolved on the device" in page
    assert 'class="safety"' not in page and "Touches safety:" not in page
    # Readable names in step headers; the raw actor and kind stay in a tooltip.
    assert '<span class="actor">arm-1</span> · <span class="kind">incident reported</span>' in page
    assert 'title="device:arm-1 · incident.reported"' in page
    assert ">device:arm-1<" not in page

    partial = _text(client.get(f"/partials/incidents/{D1}"))
    assert "<html" not in partial and story["steps"][-1]["why"] in partial


def test_story_flags_touches_safety(client, arm, example):
    body = example("incident_d1_code_patch")
    body["overlay"]["code_patch"] = (
        "--- a/visor/watchdog.py\n+++ b/visor/watchdog.py\n@@ -1 +1 @@\n-TIMEOUT_S = 5\n"
        "+TIMEOUT_S = 10\n"
    )
    client.post("/v1/devices/arm-1/incidents", json=body)
    page = _text(client.get(f"/incidents/{D1}"))
    assert "Touches safety: this incident's patch changes the safety layer or visor" in page
    assert 'class="safety">touches safety' in page
    assert "touches safety" in _text(client.get("/"))


def test_story_renders_recording_refs_as_evidence(client, arm, example, db_session):
    client.post("/v1/devices/arm-1/incidents", json=example("incident_rules_patch"))
    rec = "5d1e0000-0000-4000-8000-000000000001"
    events.record(
        db_session,
        "device:arm-1",
        "automation",
        "recording.received",
        {"incident": RULES, "device": "arm-1", "recording": rec},
        "arm-1 uploaded the incident window.",
    )
    db_session.commit()
    page = _text(client.get(f"/incidents/{RULES}"))
    assert f'href="/v1/recordings/{rec}"' in page
    assert "arm-1 uploaded the incident window." in page


def test_device_strings_are_escaped(client, arm, example):
    body = example("incident_rules_patch")
    body["summary"] = "<img src=x onerror=alert(1)>"
    body["agent"]["reasoning"] = "<script>alert('pwned')</script> threshold raised"
    client.post("/v1/devices/arm-1/incidents", json=body)
    for url in ("/", "/partials/incidents", f"/incidents/{RULES}", f"/partials/incidents/{RULES}"):
        raw = client.get(url).text
        assert "<script>alert" not in raw and "<img src=x" not in raw
    story = client.get(f"/incidents/{RULES}").text
    assert "&lt;script&gt;alert(&#39;pwned&#39;)&lt;/script&gt; threshold raised" in story
    assert "&lt;img src=x onerror=alert(1)&gt;" in story


def test_unknown_incident_page_is_404(client):
    resp = client.get(f"/incidents/{D1}")
    assert resp.status_code == 404 and resp.json()["error"] == "unknown_incident"


def test_freeze_button_posts_to_honest_stub(client):
    page = _text(client.get("/"))
    assert 'data-url="/v1/fleet/freeze"' in page
    ks = _text(client.get("/partials/killswitch"))
    assert 'id="killswitch"' in ks and "Freeze fleet" in ks
    for route in ("/v1/fleet/freeze", "/v1/fleet/unfreeze"):
        resp = client.post(route, json={"by": "dashboard", "reason": "test"})
        assert resp.status_code == 501
        assert resp.json()["detail"].startswith("Kill switch not wired yet (M3)")


def test_frozen_fleet_shows_on_every_page(client, arm, example, db_session):
    client.post("/v1/devices/arm-1/incidents", json=example("incident_rules_patch"))
    events.record(
        db_session, "human:kent", "human", "fleet.frozen", {}, "Kent froze the fleet for the demo."
    )
    db_session.commit()
    for url in ("/", f"/incidents/{RULES}", "/partials/killswitch"):
        page = _text(client.get(url))
        assert "FLEET FROZEN" in page and "/v1/fleet/unfreeze" in page
    assert "Kent froze the fleet for the demo." in _text(client.get("/partials/fleet"))


def test_static_files_are_served(client):
    js = client.get("/static/htmx.min.js")
    assert js.status_code == 200 and "htmx 2.0.4" in js.text[:200]
    css = client.get("/static/romulus.css")
    assert css.status_code == 200 and "prefers-color-scheme: dark" in css.text


def test_polling_pauses_when_hidden_and_reports_staleness(client, arm, example):
    client.post("/v1/devices/arm-1/incidents", json=example("incident_rules_patch"))
    trigger = "hx-trigger=\"every 3s [document.visibilityState === 'visible']\""
    for url in ("/", f"/incidents/{RULES}"):
        page = _text(client.get(url))
        assert page.count(trigger) >= 2  # the page's own poll plus the kill switch
        assert 'every 3s"' not in page  # no poll without the visibility filter
        assert 'id="stale"' in page and "htmx:sendError" in page and "htmx:responseError" in page


def test_freeze_asks_for_a_name_recorded_as_human(client):
    page = _text(client.get("/"))
    assert "recorded as human:<name>" in page and '"operator"' in page
    assert "body.why" in page


def test_fleet_strip_and_switch_read_only_this_fleet(client, arm, db_session):
    events.record(
        db_session,
        "human:other",
        "human",
        "fleet.frozen",
        {},
        "Another fleet was frozen.",
        fleet_id="other",
    )
    db_session.commit()
    for url in ("/", "/partials/killswitch", "/partials/fleet"):
        page = _text(client.get(url))
        assert "FLEET FROZEN" not in page and "Another fleet was frozen." not in page


def test_timeline_shows_at_most_the_limit(client, arm, example, monkeypatch):
    from romulus_cloud.api import web

    monkeypatch.setattr(web, "TIMELINE_LIMIT", 1)
    client.post("/v1/devices/arm-1/incidents", json=example("incident_rules_patch"))
    client.post("/v1/devices/arm-1/incidents", json=example("incident_d1_code_patch"))
    page = _text(client.get("/partials/incidents"))
    assert f"/incidents/{D1}" in page and f"/incidents/{RULES}" not in page
