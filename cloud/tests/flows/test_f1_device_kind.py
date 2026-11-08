"""F1 · A device first seen through a check-in registers with the right kind.

A fake arm says `kind: "sim"` and registers as sim; a device that says nothing registers as
`unknown`, which counts as real for safety: it can't be a canary until a person sets its
kind. The seed and the fake fleet's default agree (`arm-1` + `sim-1…3`, A18).
"""

from datetime import UTC, datetime
from pathlib import Path

from fake_device.__main__ import parse_args
from fake_device.arm import FakeArm
from romulus_cloud import events
from romulus_cloud.adapters.blobstore import build_blobstore
from romulus_cloud.services.seed import DEVICES, seed


def _first_seen(db_session, device_id):
    (event,) = events.find(db_session, {"device": device_id}, kinds=["device.first_seen"])
    return event


def test_an_unseeded_fake_arm_registers_as_sim(client, seeded, db_session, tmp_path):
    arm = FakeArm(client, "sim-4", tmp_path / "sim-4", canary=False, seed=0)
    arm.tick(datetime(2026, 11, 7, 23, 0, tzinfo=UTC))

    assert client.get("/v1/devices/sim-4").json()["kind"] == "sim"
    event = _first_seen(db_session, "sim-4")
    assert event.data["kind"] == "sim"
    assert event.data["kind_source"] == "sent by the device in its check-in"
    assert "registered it as kind sim (sent by the device in its check-in)" in event.why


def test_a_device_that_sends_no_kind_is_unknown_and_never_real(client, seeded, example, db_session):
    body = example("checkin")
    body.pop("kind", None)
    assert client.post("/v1/devices/mystery-1/checkin", json=body).status_code == 200

    assert client.get("/v1/devices/mystery-1").json()["kind"] == "unknown"
    event = _first_seen(db_session, "mystery-1")
    assert event.data["kind"] == "unknown"
    assert "the check-in sent no kind" in event.why and "treated as real for safety" in event.why


def test_an_unrecognised_kind_is_unknown(client, seeded, example, db_session):
    body = example("checkin") | {"kind": "simulated-ish"}
    client.post("/v1/devices/odd-1/checkin", json=body)
    assert client.get("/v1/devices/odd-1").json()["kind"] == "unknown"
    assert "unrecognised kind 'simulated-ish'" in _first_seen(db_session, "odd-1").why


def test_a_known_device_keeps_its_kind_whatever_the_check_in_says(client, seeded, example):
    client.post("/v1/devices/arm-1/checkin", json=example("checkin") | {"kind": "sim"})
    assert client.get("/v1/devices/arm-1").json()["kind"] == "real"


def test_an_unknown_device_cannot_be_a_canary_until_a_person_sets_its_kind(
    client, seeded, example, count_events
):
    client.post("/v1/devices/mystery-1/checkin", json=example("checkin"))

    resp = client.patch("/v1/devices/mystery-1", json={"role": "canary", "by": "kent"})
    assert resp.status_code == 409 and resp.json()["error"] == "unknown_kind"
    assert client.get("/v1/devices/mystery-1").json()["role"] == "normal"

    resp = client.patch(
        "/v1/devices/mystery-1", json={"kind": "sim", "role": "canary", "by": "kent"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["kind"] == "sim" and resp.json()["role"] == "canary"
    assert count_events("device.updated", {"device": "mystery-1"}) == 1

    resp = client.patch("/v1/devices/mystery-1", json={"kind": "robot"})
    assert resp.status_code == 422

    resp = client.post("/v1/devices", json={"id": "x-1", "kind": "unknown", "role": "canary"})
    assert resp.status_code == 409


def test_seed_sets_kind_and_role_on_devices_that_checked_in_first(
    client, db_session, example, settings
):
    """`arm-1` and `sim-2` check in before `make seed`: the seed makes arm-1 real and
    sim-2 the canary, with an event each, and a second seed changes nothing."""
    body = example("checkin")
    body.pop("kind", None)
    for device_id in ("arm-1", "sim-2"):
        assert client.post(f"/v1/devices/{device_id}/checkin", json=body).status_code == 200
    assert client.get("/v1/devices/arm-1").json()["kind"] == "unknown"

    store = build_blobstore(settings.blob_dir)
    result = seed(db_session, store)
    assert result.updated == ["arm-1", "sim-2"]
    assert {"sim-1", "sim-3"} <= set(result.created)

    arm, sim2 = client.get("/v1/devices/arm-1").json(), client.get("/v1/devices/sim-2").json()
    assert (arm["kind"], arm["role"]) == ("real", "normal")
    assert (sim2["kind"], sim2["role"]) == ("sim", "canary")
    (event,) = events.find(db_session, {"device": "arm-1"}, kinds=["device.updated"])
    assert event.actor == "human:seed" and event.data == {"kind": "real"}
    assert "checked in before the seed (kind unknown, role normal)" in event.why

    again = seed(db_session, store)
    assert again.updated == [] and again.created == []


def test_the_fake_fleet_default_matches_the_seed():
    """`make sim` and `make seed` name the same fake arms, so none auto-registers."""
    n = parse_args([]).n
    seeded_sims = [d for d, kind, _ in DEVICES if kind == "sim"]
    assert [f"sim-{i}" for i in range(1, n + 1)] == seeded_sims
    assert ("arm-1", "real", "normal") in DEVICES
    compose = (Path(__file__).resolve().parents[3] / "compose.yaml").read_text()
    assert f"${{FAKE_DEVICES_N:-{n}}}" in compose
