"""F4 + F8 step 4: a candidate that breaks an old resolved incident is rolled back by the
regression corpus, with that incident named, before it reaches any device.

sim-3 once stalled its gripper at 950 mA and `backoff_retry` fixed it (a regression case:
v1's `gripper_stall` rule fires above 900). Later sim-1's healer raises that threshold to
2000 to stop a stall at 2100 mA. The candidate replays its own incident fine, but sim-3's
old incident no longer fires, so the gate rolls it back with that incident in the why.
"""

import uuid
from datetime import timedelta

from romulus_cloud.models import Overlay
from romulus_cloud.services import datasets

S = timedelta(seconds=1)


def _incident(example, kind, ma, *, overlay=None):
    body = example("incident_rules_patch")
    body |= {
        "incident_id": str(uuid.uuid4()),
        "kind": kind,
        "summary": f"gripper current {ma} mA",
        "signals": {"gripper_current_ma": [ma]},
        "overlay": overlay,
    }
    body["outcome"]["resolving_action"] = "backoff_retry"
    return body


def test_f4_candidate_that_regresses_an_old_incident_is_rolled_back_naming_it(
    client, seeded, example, work, now, db_session, count_events
):
    # 1. An old resolved incident on sim-3 becomes a regression case (the worker's sync).
    old = _incident(example, "gripper_stall", 950)
    assert client.post("/v1/devices/sim-3/incidents", json=old).status_code in (200, 201)
    assert datasets.sync_regression_cases(db_session, now) == 1
    db_session.commit()

    # 2. sim-1's healer raises the threshold to stop a 2100 mA stall, and keeps the fix.
    overlay = {
        "overlay_id": "ov-grip",
        "diff": {"rules": {"gripper_stall": {"above": 2000}}},
        "code_patch": None,
    }
    new = _incident(example, "gripper_stall", 2100, overlay=overlay)
    assert client.post("/v1/devices/sim-1/incidents", json=new).status_code in (200, 201)
    held = example("overlay_status_held")
    assert client.post("/v1/devices/sim-1/overlays/ov-grip", json=held).status_code == 204

    # 3. The batch folds it into firmware v2; the gate replays it and the corpus.
    work(now + 10 * S)
    work(now + 11 * S)
    fw = next(c for c in client.get("/v1/candidates").json() if c["type"] == "firmware")
    fw = client.get(f"/v1/candidates/{fw['id']}").json()
    assert fw["status"] == "rolled_back" and fw["canary_device"] is None
    why = fw["verdict"]["why"]
    assert "replayed 1 incident; 1 pass" in why  # its own incident still passes
    assert "regression corpus" in why and "1 regress" in why
    assert old["incident_id"] in why  # the regressed incident, named

    # Nothing reached a device, and the story of the new incident says why.
    assert {d["assigned"]["firmware"] for d in client.get("/v1/devices").json()} == {1}
    assert count_events("candidate.promoted") == 0
    story = client.get(f"/v1/incidents/{new['incident_id']}/story").json()
    rolled = [st for st in story["steps"] if st["kind"] == "candidate.rolled_back"]
    assert rolled and any(old["incident_id"] in st["why"] for st in rolled)
    db_session.expire_all()
    ov = db_session.get(Overlay, ("sim-1", "ov-grip"))
    assert ov.status == "lockin_rolled_back" and old["incident_id"] in ov.status_reason
