"""F4: bad candidate → rollback (flows doc F4), both bad-candidate modes of the fake arm.

- `bad`: the canary's post-install health check fails, it rolls back on its own and
  reports `rolled_back`.
- `bad-late`: the install passes, then the canary checks in `failing` and repeats the
  fault. Its `last_good` is already the bad candidate (installed == assigned), which is why
  the gate restores the canary's *previous assignment*, not `last_good`.
"""

from datetime import timedelta

import pytest

from fake_device.scenarios import SCENARIOS
from romulus_cloud.models import Overlay

S = timedelta(seconds=1)


def _devices(client):
    return {d["id"]: d for d in client.get("/v1/devices").json()}


@pytest.mark.parametrize(
    ("scenario", "reason"),
    [
        ("bad", "canary install rolled_back"),
        ("bad-late", "canary repeated camera_occluded"),
    ],
)
def test_f4_bad_candidate_is_rolled_back_on_the_canary(
    scenario, reason, client, arms, tick_arms, work, now, count_events, db_session
):
    tick_arms(now)
    tick_arms(now + 10 * S)
    # A held overlay from the canary itself, in bad-candidate mode.
    for i, step in enumerate(SCENARIOS[scenario](arms["sim-2"], 0)):
        step.run(now + (20 + i) * S)
    tick_arms(now + 30 * S)

    # 1. The batch makes firmware v2 (and harness v2); replay passes; v2 goes to the canary.
    work(now + 40 * S)
    work(now + 41 * S)
    cands = {c["type"]: c for c in client.get("/v1/candidates").json()}
    fw = client.get(f"/v1/candidates/{cands['firmware']['id']}").json()
    assert fw["status"] == "canary" and fw["replay"]["passed"]
    assert fw["canary_prev_version"] == 1

    # 2. The canary installs it and fails.
    installs = tick_arms(now + 50 * S)["sim-2"]
    if scenario == "bad":
        assert ("firmware", 2, "rolled_back") in installs
    else:
        assert ("firmware", 2, "installed") in installs
        assert _devices(client)["sim-2"]["last_good"]["firmware"] == 2  # the trap
        tick_arms(now + 60 * S)  # checks in failing
    work(now + 70 * S)

    # 3. Rolled back: the canary is assigned what it had before, with the reason shown.
    fw = client.get(f"/v1/candidates/{cands['firmware']['id']}").json()
    assert fw["status"] == "rolled_back"
    rolled = [e for e in fw["events"] if e["kind"] == "candidate.rolled_back"]
    assert len(rolled) == 1 and reason in rolled[0]["why"]
    assert "sim-2 is assigned firmware 1 again" in rolled[0]["why"]
    # The harness from the same overlay goes with it.
    hv = client.get(f"/v1/candidates/{cands['harness']['id']}").json()
    assert hv["status"] == "rolled_back"
    assert any(
        "withdrawn: firmware v2 from the same overlay(s) was rolled back" in e["why"]
        for e in hv["events"]
    )
    # The overlay is never re-batched, and the device keeps its local fix (sticky state).
    overlay_id = fw["from_overlays"][0]["overlay_id"]
    overlay = db_session.get(Overlay, ("sim-2", overlay_id))
    db_session.refresh(overlay)
    assert overlay.status == "lockin_rolled_back"
    assert "firmware v2 was rolled back" in overlay.status_reason

    devs = _devices(client)
    assert devs["sim-2"]["assigned"] == {"firmware": 1, "harness": 1}
    # No non-canary device was ever assigned v2.
    for other in ("sim-1", "sim-3", "arm-1"):
        assert devs[other]["assigned"] == {"firmware": 1, "harness": 1}
    assert count_events("candidate.promoted") == 0

    # The canary goes back to its last good version and stays there.
    tick_arms(now + 80 * S)
    tick_arms(now + 90 * S)
    sim2 = _devices(client)["sim-2"]
    assert sim2["running"]["firmware"] == sim2["last_good"]["firmware"] == 1
    assert sim2["running"]["harness"] == 1
    for t in (100, 300):
        work(now + t * S)
    assert _devices(client)["sim-2"]["assigned"] == {"firmware": 1, "harness": 1}

    # The story of the original incident ends in the rollback, with its reason.
    first = min(
        (i for i in client.get("/v1/incidents").json() if i["device_id"] == "sim-2"),
        key=lambda i: i["occurred_at"],
    )
    story = client.get(f"/v1/incidents/{first['incident_id']}/story").json()
    chain = story["chain"]  # evidence uploads (M4) may interleave with the gate's steps
    assert "→ locked in → firmware v2 → harness v2 → replay ✓ → canary sim-2 →" in chain, chain
    assert chain.endswith("→ rolled back"), chain
    assert reason in [s for s in story["steps"] if s["kind"] == "candidate.rolled_back"][0]["why"]
    kept = [s for s in story["steps"] if s["kind"] == "overlay.lockin_rolled_back"]
    assert kept and "the device keeps its local fix" in kept[0]["why"]
    if scenario == "bad":  # the rolled-back install left the overlay on the canary
        assert arms["sim-2"].device.overlay is not None
        assert arms["sim-2"].tick(now + 400 * S).response.drop_overlay is False
