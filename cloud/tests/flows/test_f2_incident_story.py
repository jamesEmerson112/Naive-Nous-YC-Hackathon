"""F2 · Incident → story (flows doc F2): report → overlay → held, told in plain English.

Everything goes through the HTTP API with the contract examples as payloads.
"""

import pytest
from sqlalchemy import func, select

from romulus_cloud.models import Incident, Overlay

RULES = "0b6c6f3e-8d2a-4c1b-b5e3-6f7a8b9c0d1e"  # incident_rules_patch.json
D1 = "3f2b8c1e-6a4d-4e7b-9c21-5d8e0f1a2b3c"  # incident_d1_code_patch.json


@pytest.fixture
def arm(client, seeded, example):
    """F1 done: arm-1 has checked in."""
    assert client.post("/v1/devices/arm-1/checkin", json=example("checkin")).status_code == 200
    return "arm-1"


def _story(client, incident_id):
    resp = client.get(f"/v1/incidents/{incident_id}/story")
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_f2_rules_patch_story(client, arm, example, count_events, db_session):
    # 1. The device reports camera_occluded, resolved, with overlay ov-12.
    resp = client.post("/v1/devices/arm-1/incidents", json=example("incident_rules_patch"))
    assert resp.status_code == 201 and resp.json() == {"incident_id": RULES}

    # Sending it again returns 200 with the same incident and creates nothing.
    again = client.post("/v1/devices/arm-1/incidents", json=example("incident_rules_patch"))
    assert again.status_code == 200 and again.json() == {"incident_id": RULES}
    assert db_session.scalar(select(func.count()).select_from(Incident)) == 1
    assert count_events("incident.reported", {"incident": RULES}) == 1
    assert count_events("overlay.applied", {"incident": RULES}) == 1

    # 2. Later the device reports ov-12 as held.
    held = client.post("/v1/devices/arm-1/overlays/ov-12", json=example("overlay_status_held"))
    assert held.status_code == 204

    story = _story(client, RULES)
    assert story["chain"] == "camera_occluded → agent acted → overlay ov-12 → held"
    assert [s["kind"] for s in story["steps"]] == [
        "incident.reported",
        "incident.agent_acted",
        "overlay.applied",
        "overlay.held",
    ]
    broke, agent, overlay, kept = story["steps"]
    assert broke["line"] == (
        "arm-1 reported camera_occluded: Camera brightness fell below threshold during pick"
    )
    assert broke["why"] == "Detected by signals brightness; healer outcome fix-config."
    assert agent["why"].startswith("Frames dark for 3 s; occlusion, not hardware.")
    assert "safe_pose" in agent["line"] and "resolved it" in agent["line"]
    assert agent["actor_class"] == "agent"
    assert "rules camera_occluded" in overlay["line"]
    assert kept["why"] == "arm-1 kept overlay ov-12: no recurrence for 20 min."
    assert all(s["why"] for s in story["steps"])
    assert story["device_id"] == "arm-1" and story["resolved"] is True


def test_f2_incident_stores_raw_and_parsed_fields(client, arm, example, db_session):
    body = example("incident_rules_patch") | {"healer_build": "pi-0.3"}  # an unknown field
    client.post("/v1/devices/arm-1/incidents", json=body)
    incident = db_session.get(Incident, RULES)
    assert incident.raw == body
    assert incident.kind == "camera_occluded" and incident.suspected_cause == "software"
    assert incident.signals == {"brightness": [0.41, 0.18, 0.12, 0.33]}
    assert incident.occurred_at.isoformat() == "2026-11-07T22:02:11+00:00"
    assert incident.received_at is not None
    overlay = db_session.get(Overlay, ("arm-1", "ov-12"))
    assert overlay.diff == {"rules": {"camera_occluded": {"below": 0.35}}}
    assert overlay.base_firmware == 1 and overlay.status == "active"


def test_f2_d1_code_patch_held_then_reverted(client, arm, example, count_events, db_session):
    resp = client.post("/v1/devices/arm-1/incidents", json=example("incident_d1_code_patch"))
    assert resp.status_code == 201
    overlay = db_session.get(Overlay, ("arm-1", "ov-d1-0001"))
    assert overlay.code_patch.startswith("--- a/control/hold.py")
    assert overlay.base_code_rev == "c0de001"
    assert overlay.touches_safety is False

    url = "/v1/devices/arm-1/overlays/ov-d1-0001"
    assert client.post(url, json=example("overlay_status_held")).status_code == 204
    story = _story(client, D1)
    assert story["chain"] == "unexpected_release → agent acted → overlay ov-d1-0001 → held"
    assert story["steps"][0]["why"] == (
        "Detected by tier 3 health check; log: S HOLDING->RELEASE reason=dist_lt_threshold "
        "dist_mm=31; signals dist_mm, gripper_closed; healer outcome fix-code."
    )
    applied = story["steps"][2]
    assert "code control/hold.py, tests/test_hold_proximity.py" in applied["line"]
    assert "SPEC.md says never open the gripper" in story["steps"][1]["why"]

    # A conflicting later status is recorded with its why; latest wins.
    reverted = example("overlay_status_reverted") | {"occurred_at": "2026-11-07T23:40:00Z"}
    assert client.post(url, json=reverted).status_code == 204
    story = _story(client, D1)
    assert story["chain"].endswith("→ held → reverted")
    assert story["steps"][-1]["why"].endswith("It was held before.")
    db_session.expire_all()
    assert db_session.get(Overlay, ("arm-1", "ov-d1-0001")).status == "reverted"
    assert count_events("overlay.reverted", {"incident": D1}) == 1


def test_f2_overlay_status_resend_is_a_noop(client, arm, example, count_events):
    client.post("/v1/devices/arm-1/incidents", json=example("incident_rules_patch"))
    url = "/v1/devices/arm-1/overlays/ov-12"
    assert client.post(url, json=example("overlay_status_held")).status_code == 204
    resend = client.post(url, json=example("overlay_status_held"))
    assert resend.status_code == 200
    assert count_events("overlay.held", {"device": "arm-1", "overlay": "ov-12"}) == 1


def test_f2_code_patch_touching_safety_is_flagged(client, arm, example):
    body = example("incident_d1_code_patch")
    body["overlay"]["code_patch"] = (
        "--- a/visor/watchdog.py\n+++ b/visor/watchdog.py\n@@ -1 +1 @@\n-TIMEOUT_S = 5\n"
        "+TIMEOUT_S = 10\n"
    )
    client.post("/v1/devices/arm-1/incidents", json=body)
    applied = _story(client, D1)["steps"][2]
    assert applied["refs"]["touches_safety"] is True
    assert "safety layer or visor" in applied["line"]


def test_f2_late_report_is_placed_by_occurred_at(client, arm, example):
    rules = example("incident_rules_patch")  # occurred 22:02
    d1 = example("incident_d1_code_patch")  # occurred 23:14, but arrives first
    client.post("/v1/devices/arm-1/incidents", json=d1)
    client.post("/v1/devices/arm-1/incidents", json=rules)
    listed = [i["incident_id"] for i in client.get("/v1/incidents").json()]
    assert listed == [D1, RULES]  # newest first by occurred_at, not by arrival
    assert [i["incident_id"] for i in client.get("/v1/incidents?limit=1").json()] == [D1]
    assert client.get("/v1/incidents?limit=0").status_code == 422
    assert [i["incident_id"] for i in client.get("/v1/incidents?kind=camera_occluded").json()] == [
        RULES
    ]


def test_f2_hardware_incident_without_overlay(client, arm, example):
    resp = client.post("/v1/devices/arm-1/incidents", json=example("incident_park_hardware"))
    assert resp.status_code == 201
    story = _story(client, resp.json()["incident_id"])
    assert story["chain"] == "servo_absent → agent acted"
    assert "did not resolve it with park; suspected cause: hardware" in story["steps"][1]["line"]


def test_f2_status_for_an_unknown_overlay_is_recorded(client, arm, example, db_session):
    url = "/v1/devices/arm-1/overlays/ov-99"
    assert client.post(url, json=example("overlay_status_reverted")).status_code == 204
    overlay = db_session.get(Overlay, ("arm-1", "ov-99"))
    assert overlay.status == "reverted" and overlay.incident_id is None


def test_f2_rejections(client, arm, example):
    skew = example("incident_rules_patch") | {"contract_version": "2.0"}
    resp = client.post("/v1/devices/arm-1/incidents", json=skew)
    assert resp.status_code == 400 and resp.json()["error"] == "unsupported_contract_version"
    resp = client.post("/v1/devices/nobody/incidents", json=example("incident_rules_patch"))
    assert resp.status_code == 404 and resp.json()["error"] == "unknown_device"
    bad = example("incident_rules_patch") | {"incident_id": "not-a-uuid"}
    assert client.post("/v1/devices/arm-1/incidents", json=bad).status_code == 422
    assert client.get(f"/v1/incidents/{D1}/story").json()["error"] == "unknown_incident"
    # The same incident id from a different device is a conflict, not a silent merge.
    client.post("/v1/devices/arm-1/incidents", json=example("incident_rules_patch"))
    client.post("/v1/devices/sim-1/checkin", json=example("checkin"))
    resp = client.post("/v1/devices/sim-1/incidents", json=example("incident_rules_patch"))
    assert resp.status_code == 409 and resp.json()["error"] == "incident_id_conflict"


def test_f2_overlay_superseded_by_a_new_version_is_not_a_failed_fix(
    client, arm, example, count_events, db_session
):
    client.post("/v1/devices/arm-1/incidents", json=example("incident_rules_patch"))
    url = "/v1/devices/arm-1/overlays/ov-12"
    client.post(url, json=example("overlay_status_held"))
    # Firmware v2 ships; the device installs it, and the visor drops the overlay (D7).
    v2 = example("firmware_response")["manifest"] | {"version": 2}
    client.post("/v1/firmware", json={"manifest": v2})
    client.patch("/v1/devices/arm-1", json={"assigned": {"firmware": 2}})
    install = {"type": "firmware", "version": 2, "result": "installed"}
    installed_at = {"occurred_at": "2026-11-08T00:10:00Z"}
    client.post("/v1/devices/arm-1/installs", json=install | installed_at)
    body = {
        "status": "superseded",  # accepted before contracts know it (PR #5 adds it)
        "reason": "replaced by firmware 2 (D7)",
        "occurred_at": "2026-11-08T00:10:05Z",
    }
    assert client.post(url, json=body).status_code == 204
    assert client.post(url, json=body).status_code == 200  # a resend changes nothing

    assert count_events("overlay.superseded", {"incident": RULES}) == 1
    assert count_events("overlay.reverted", {"incident": RULES}) == 0  # not a failed heal
    story = _story(client, RULES)
    assert story["chain"] == "camera_occluded → agent acted → overlay ov-12 → held → superseded"
    why = story["steps"][-1]["why"]
    assert why.startswith(
        "Overlay ov-12 on arm-1 was superseded by a new version: replaced by firmware 2"
    )
    assert "Not a failed fix" in why
    db_session.expire_all()
    overlay = db_session.get(Overlay, ("arm-1", "ov-12"))
    # The overlay was held but never locked in, so firmware 2 doesn't contain it: it stays
    # held and re-batches on the new base (no fix lost; orchestrator decision on PR #18).
    assert overlay.status == "held"
    assert "stays held for the next batch" in why
    assert overlay.raw["last_status"] == body
    # After `superseded`, a late `held` changes nothing; a late `reverted` is recorded (with
    # its body) but the overlay stays held; its resend is a no-op.
    assert client.post(url, json=example("overlay_status_held")).status_code == 200
    reverted = example("overlay_status_reverted")
    assert client.post(url, json=reverted).status_code == 204
    assert client.post(url, json=reverted).status_code == 200
    assert count_events("overlay.reverted", {"incident": RULES}) == 1
    db_session.expire_all()
    overlay = db_session.get(Overlay, ("arm-1", "ov-12"))
    assert overlay.status == "held" and overlay.raw["last_status"] == body
    assert overlay.raw["ignored_status"] == reverted


def test_f2_cloud_status_is_sticky(client, arm, example, count_events, db_session):
    """F3: the origin device sends `superseded` when it installs the promoted version."""
    client.post("/v1/devices/arm-1/incidents", json=example("incident_rules_patch"))
    url = "/v1/devices/arm-1/overlays/ov-12"
    client.post(url, json=example("overlay_status_held"))
    db_session.get(Overlay, ("arm-1", "ov-12")).status = "locked_in"  # what M3's gate does
    db_session.commit()
    gone = {"status": "superseded", "reason": "replaced by firmware 2 (D7)"}
    assert client.post(url, json=gone).status_code == 204
    assert client.post(url, json=gone).status_code == 200  # resend
    assert client.post(url, json=example("overlay_status_held")).status_code == 200
    assert client.post(url, json=example("overlay_status_reverted")).status_code == 204
    db_session.expire_all()
    assert db_session.get(Overlay, ("arm-1", "ov-12")).status == "locked_in"
    assert count_events("overlay.superseded", {"incident": RULES}) == 1
    story = _story(client, RULES)
    (superseded,) = [st for st in story["steps"] if st["kind"] == "overlay.superseded"]
    assert superseded["why"].endswith(
        "The overlay stays locked_in: the cloud already decided its fate."
    )


def test_f2_status_before_incident_is_filled_in(client, arm, example, count_events, db_session):
    """Outbox order can put the overlay status first; the incident then fills the overlay."""
    body = example("incident_d1_code_patch")
    body["overlay"]["code_patch"] = (
        "--- a/safety/limits.py\n+++ b/safety/limits.py\n@@ -1 +1 @@\n-MAX = 1\n+MAX = 2\n"
    )
    url = "/v1/devices/arm-1/overlays/ov-d1-0001"
    assert client.post(url, json=example("overlay_status_held")).status_code == 204
    assert client.post("/v1/devices/arm-1/incidents", json=body).status_code == 201
    db_session.expire_all()
    overlay = db_session.get(Overlay, ("arm-1", "ov-d1-0001"))
    assert overlay.incident_id == D1 and overlay.status == "held"
    assert overlay.touches_safety is True and overlay.code_patch.startswith("--- a/safety")
    assert overlay.raw["last_status"] == example("overlay_status_held")
    assert count_events("overlay.applied", {"incident": D1, "touches_safety": True}) == 1
    story = _story(client, D1)
    assert story["chain"] == "unexpected_release → agent acted → overlay ov-d1-0001 → held"


def test_f2_incident_before_status_same_result(client, arm, example, db_session):
    body = example("incident_d1_code_patch")
    body["overlay"]["code_patch"] = (
        "--- a/safety/limits.py\n+++ b/safety/limits.py\n@@ -1 +1 @@\n-MAX = 1\n+MAX = 2\n"
    )
    client.post("/v1/devices/arm-1/incidents", json=body)
    client.post("/v1/devices/arm-1/overlays/ov-d1-0001", json=example("overlay_status_held"))
    db_session.expire_all()
    overlay = db_session.get(Overlay, ("arm-1", "ov-d1-0001"))
    assert overlay.touches_safety is True and overlay.status == "held"
    story = _story(client, D1)
    assert story["chain"] == "unexpected_release → agent acted → overlay ov-d1-0001 → held"
    assert story["steps"][-1]["refs"]["touches_safety"] is True
