"""Interop: the fake device (tools/fake_device), unmodified, against the real app.

It runs through the `device_http` bridge (cloud/tests/conftest.py), so its DeviceClient,
visor and outbox behave as they do against a live cloud. Covers F1 (pull → install →
last_good), then F2 with the rules-patch and D1 code-patch scenarios, ending with the
stories, then a new firmware superseding an overlay. Recordings are M4: until then the
outbox dead-letters them on 404, which this test tolerates.
"""

from datetime import UTC, datetime, timedelta

from fake_device.arm import FakeArm
from fake_device.scenarios import SCENARIOS
from romulus_cloud.services.seed import FIRMWARE_V1

T0 = datetime(2026, 11, 7, 22, 0, tzinfo=UTC)


def _tick_all(arms, now):
    return {
        a.device_id: [(r.type, r.version, r.result) for r in a.tick(now).installs] for a in arms
    }


def _story(client, device_id):
    (incident,) = [i for i in client.get("/v1/incidents").json() if i["device_id"] == device_id]
    return client.get(f"/v1/incidents/{incident['incident_id']}/story").json()


def test_fake_fleet_through_version_sync_and_incident_stories(
    client, device_http, seeded, tmp_path
):
    arms = [
        FakeArm(device_http, f"sim-{i}", tmp_path / f"sim-{i}", canary=i == 2) for i in (1, 2, 3)
    ]

    # F1: the visor pulls firmware v1, code c0de001 and harness v1, installs and reports.
    installs = _tick_all(arms, T0)
    for a in arms:
        assert sorted(installs[a.device_id]) == [
            ("code", "c0de001", "installed"),
            ("firmware", 1, "installed"),
            ("harness", 1, "installed"),
        ]
    _tick_all(arms, T0 + timedelta(seconds=10))
    for d in client.get("/v1/devices").json():
        if d["id"] == "arm-1":
            continue
        assert d["running"]["firmware"] == d["assigned"]["firmware"] == 1
        assert d["running"]["harness"] == d["assigned"]["harness"] == 1
        assert d["running"]["code_rev"] == "c0de001"
        assert d["last_good"] == {"firmware": 1, "harness": 1}

    # F2: a rules patch on sim-1 and the D1 code patch on sim-3, each later held.
    steps = SCENARIOS["rules"](arms[0], 0) + SCENARIOS["code"](arms[2], 0)
    now = T0 + timedelta(seconds=20)
    for step in steps:
        step.run(now)
    _tick_all(arms, now)

    rules = _story(client, "sim-1")
    assert rules["chain"].startswith("camera_occluded → agent acted → overlay ov-sim-1-")
    assert rules["chain"].endswith("→ held")
    assert rules["steps"][1]["why"].startswith("Frames dark for 3 s")
    d1 = _story(client, "sim-3")
    assert d1["chain"].startswith("unexpected_release → agent acted → overlay ov-sim-3-")
    assert d1["chain"].endswith("→ held")
    assert "code control/hold.py" in d1["steps"][2]["line"]
    devices = {d["id"]: d for d in client.get("/v1/devices").json()}
    assert devices["sim-1"]["overlay_id"].startswith("ov-sim-1-")

    # A new firmware replaces sim-1's overlay: installed, last_good moves, overlay superseded.
    assert client.post("/v1/firmware", json={"manifest": FIRMWARE_V1 | {"version": 2}}).is_success
    client.patch("/v1/devices/sim-1", json={"assigned": {"firmware": 2}})
    later = T0 + timedelta(seconds=30)
    assert ("firmware", 2, "installed") in _tick_all(arms, later)["sim-1"]
    _tick_all(arms, later + timedelta(seconds=10))
    sim1 = client.get("/v1/devices/sim-1").json()
    assert sim1["running"]["firmware"] == sim1["last_good"]["firmware"] == 2
    assert sim1["overlay_id"] is None
    rules = _story(client, "sim-1")
    assert rules["chain"].endswith("→ held → superseded")
    assert "replaced by firmware 2" in rules["steps"][-1]["why"]
