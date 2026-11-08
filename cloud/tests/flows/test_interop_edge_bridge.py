"""Interop: the edge bridge (tools/edge_bridge), unmodified, against the real app.

The bridge reads a healer `var/` folder written the way Olir's pod healer writes it
(`edge_bridge.fake_healer`) and reports through the same `device_http` bridge the fake
device uses. It proves the pod's records land as the cloud expects with no contract
change: the pod registers as real hardware, scenario 01 becomes one resolved incident
with its log window and agent trace as evidence, and scenario 06 (the healer asks a
person to reseat the sensor) becomes a work order.
"""

from datetime import UTC, datetime, timedelta
from functools import partial

from edge_bridge.bridge import Bridge
from edge_bridge.fake_healer import (
    BAD_CRC,
    T0,
    FakeSocket,
    PoddVar,
    scenario_01_fixed,
    scenario_06_handed_off,
)
from fake_device.client import DeviceClient
from fake_device.outbox import Outbox
from romulus_cloud.services import workorders
from romulus_cloud.worker import Step, run_tick


def _bridge(device_http, pod, sock, tmp_path):
    state = tmp_path / "bridge"
    return Bridge(
        pod.paths,
        "pod-1",
        DeviceClient(device_http, "pod-1"),
        Outbox(state / "outbox"),
        sock,
        state / "state.json",
    )


def test_the_pod_healer_reaches_the_cloud_through_the_edge_bridge(
    client, device_http, seeded, sessions, settings, tmp_path
):
    # The one-time operator step from the bridge's README: the pod isn't an arm.
    resp = client.post(
        "/v1/devices",
        json={"id": "pod-1", "kind": "real", "device_class": "sensor_pod", "by": "kent"},
    )
    assert resp.status_code == 201, resp.text

    pod, sock = PoddVar(tmp_path / "pod"), FakeSocket()
    sock.last_boot = {
        "type": "E",
        "t": 1,
        "ev": "boot",
        "build": "pod v1.1 2026-11-08",
        "crc": f"0x{BAD_CRC}",
        "ts": T0,
    }
    bridge = _bridge(device_http, pod, sock, tmp_path)
    assert bridge.tick(T0).checked_in

    (device,) = [d for d in client.get("/v1/devices").json() if d["id"] == "pod-1"]
    assert (device["kind"], device["device_class"]) == ("real", "sensor_pod")
    # No arm firmware for a sensor pod. (Harness versions aren't per class, so the arm
    # healer's harness is assigned anyway; the bridge installs nothing either way.)
    assert device["assigned"]["firmware"] is None
    assert device["running"]["mcu_build"] == "v1.1@32ACFDAF"
    assert device["health"] == "ok"

    # Scenario 01: crash loop, fixed by the healer and verified by fw_flash.
    t = T0 + 60
    scenario_01_fixed(pod, t)
    result = bridge.tick(t + 200)
    assert result.sent == 3
    (incident,) = [i for i in client.get("/v1/incidents").json() if i["device_id"] == "pod-1"]
    assert incident["incident_id"] == result.reported[0]
    assert incident["kind"] == "crash"
    assert incident["suspected_cause"] == "software"
    story = client.get(f"/v1/incidents/{incident['incident_id']}/story").json()
    assert story["chain"] == "crash → agent acted → evidence: log window → evidence: agent trace"
    recordings = client.get("/v1/recordings", params={"incident": incident["incident_id"]})
    assert sorted(r["kind"] for r in recordings.json()) == ["agent_trace", "log_window"]

    # Scenario 06: the sensor is gone; the healer hardens the I2C code, then asks a person.
    t = T0 + 600
    scenario_06_handed_off(pod, t)
    bridge.tick(t + 140)
    (lost,) = [
        i
        for i in client.get("/v1/incidents").json()
        if i["device_id"] == "pod-1" and i["kind"] == "acc_error"
    ]
    assert lost["suspected_cause"] == "hardware" and not lost["resolved"]

    now = datetime(2026, 11, 8, 2, 0, tzinfo=UTC)
    step = Step("workorders.triage_new", partial(workorders.triage_new, settings=settings))
    assert run_tick([step], sessions, now + timedelta(seconds=1), {}) == {
        "workorders.triage_new": "ok"
    }
    orders = [o for o in client.get("/v1/work-orders").json() if o["device_id"] == "pod-1"]
    assert [o["incident_id"] for o in orders] == [lost["incident_id"]]
