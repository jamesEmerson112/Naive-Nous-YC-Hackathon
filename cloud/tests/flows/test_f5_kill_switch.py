"""F5: the kill switch (flows doc F5, D11).

Freeze mid-canary: every device is pinned to its last good versions, the candidate
freezes, check-ins say `frozen`, and nothing moves however long it stays frozen. Unfreeze:
the candidate restarts from replay with a **fresh** canary window, so it can't promote
before that new window ends.
"""

from datetime import timedelta

from fake_device.scenarios import SCENARIOS
from romulus_cloud.models import Event

S = timedelta(seconds=1)


def _devices(client):
    return {d["id"]: d for d in client.get("/v1/devices").json()}


def _assigned(client):
    return {k: d["assigned"] for k, d in _devices(client).items()}


def test_f5_freeze_mid_canary_then_unfreeze_starts_a_fresh_window(
    client, arms, tick_arms, work, now, count_events, db_session
):
    tick_arms(now)
    tick_arms(now + 10 * S)
    for i, step in enumerate(SCENARIOS["rules"](arms["sim-1"], 0)):
        step.run(now + (20 + i) * S)
    work(now + 40 * S)
    work(now + 41 * S)  # canary starts
    tick_arms(now + 50 * S)  # sim-2 installs v2
    tick_arms(now + 90 * S)
    fw_id = next(c["id"] for c in client.get("/v1/candidates").json() if c["type"] == "firmware")
    assert client.get(f"/v1/candidates/{fw_id}").json()["status"] == "canary"

    # 1-2. Freeze: a human event, devices pinned to last good, candidates frozen.
    resp = client.post("/v1/fleet/freeze", json={"by": "kent", "why": "demo freeze"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["frozen"] is True and body["why"].startswith("human:kent froze the fleet")
    frozen = db_session.query(Event).filter(Event.kind == "fleet.frozen").one()
    assert frozen.actor == "human:kent" and frozen.actor_class == "human"
    pinned = _assigned(client)
    devs = _devices(client)
    # The canary installed v2, so its last good is v2; it goes back to what it had before.
    assert devs["sim-2"]["last_good"]["firmware"] == 2
    assert devs["sim-2"]["assigned"] == {"firmware": 1, "harness": 1}
    assert {p["device"] for p in body["pinned"]} == {"sim-2"}
    for name, d in devs.items():
        if name != "sim-2" and d["last_good"]["firmware"] is not None:
            assert d["assigned"] == d["last_good"], name
    work(now + 100 * S)
    for c in client.get("/v1/candidates").json():
        assert c["status"] == "frozen"
    events = client.get(f"/v1/candidates/{fw_id}").json()["events"]
    assert "fleet frozen by the kill switch while canary" in events[-1]["why"]

    # While frozen: check-ins say so, and nothing moves, well past the canary window.
    result = arms["sim-1"].tick(now + 110 * S)
    assert result.response.frozen is True
    for t in (120, 200, 400):
        tick_arms(now + t * S)
        work(now + t * S + S)
    assert _assigned(client) == pinned
    assert count_events("candidate.promoted") == 0
    # The batch may still run; with nothing held it makes nothing.
    assert client.post("/v1/batch/run", json={"by": "human:kent"}).json()["changed"] is True
    work(now + 410 * S)
    assert {c["status"] for c in client.get("/v1/candidates").json()} == {"frozen"}

    # 3. Unfreeze: a human event; candidates restart from replay, then a fresh canary.
    resp = client.post("/v1/fleet/unfreeze", json={"reason": "demo over"})
    assert resp.json()["frozen"] is False
    assert resp.json()["why"].startswith("human:operator unfroze the fleet: demo over")
    work(now + 500 * S)
    assert client.get(f"/v1/candidates/{fw_id}").json()["status"] == "replaying"
    restart = now + 501 * S
    work(restart)
    cand = client.get(f"/v1/candidates/{fw_id}").json()
    assert cand["status"] == "canary"
    assert cand["canary_started_at"] == restart.isoformat()
    assert cand["canary_prev_version"] == 1  # kept from the first run

    # No promotion before the fresh window (120 s from `restart`) ends, even though the
    # canary has run v2 since the first run.
    for t in (520, 560, 600, 615):
        tick_arms(now + t * S)
        work(now + t * S + S)
    assert client.get(f"/v1/candidates/{fw_id}").json()["status"] == "canary"
    work(restart + 121 * S)
    assert client.get(f"/v1/candidates/{fw_id}").json()["status"] == "promoted"
    assert count_events("fleet.unfrozen") == 1


def test_freeze_right_after_a_promotion_does_not_split_the_fleet(
    client, arms, tick_arms, work, now, clock
):
    """Review finding 2: v2 is promoted, only sim-1 installs it, then freeze/unfreeze.
    Unfreeze puts every device back on the promoted release, so the fleet converges."""
    tick_arms(now)
    tick_arms(now + 10 * S)
    for i, step in enumerate(SCENARIOS["rules"](arms["sim-1"], 0)):
        step.run(now + (20 + i) * S)
    work(now + 40 * S)
    work(now + 41 * S)
    for t in (50, 80, 110, 150):
        tick_arms(now + t * S)
    work(now + 165 * S)
    assert {c["status"] for c in client.get("/v1/candidates").json()} == {"promoted"}
    clock.at(now + 170 * S)
    arms["sim-1"].tick(now + 170 * S)  # only sim-1 installs before the freeze

    frozen = client.post("/v1/fleet/freeze", json={"by": "kent", "why": "x"}).json()
    assert _assigned(client)["sim-3"] == {"firmware": 1, "harness": 1}  # its last good
    assert {p["device"] for p in frozen["pinned"]} >= {"sim-3"}
    resp = client.post("/v1/fleet/unfreeze", json={"by": "kent", "why": "x"}).json()
    assert "Back on the promoted release" in resp["why"]
    assert all(a == {"firmware": 2, "harness": 2} for a in _assigned(client).values())
    for t in range(200, 260, 10):
        tick_arms(now + t * S)
        work(now + t * S + S)
    devs = _devices(client)
    for name in arms:
        assert devs[name]["running"]["firmware"] == devs[name]["running"]["harness"] == 2
