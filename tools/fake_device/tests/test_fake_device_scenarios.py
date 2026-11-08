"""Scripted scenarios: the request sequence each one sends, and what the cloud can do with it."""

import io
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import fake_device.__main__ as cli
import httpx
import pytest
from fake_device.arm import FakeArm
from fake_device.fake_cloud import V1_MANIFEST, FakeCloud
from fake_device.scenarios import (
    D1_CODE_PATCH,
    bad_candidate,
    code_patch,
    park_hardware,
    rules_patch,
)
from fake_device.sim import merge_rules
from mcap.reader import make_reader
from romulus_contracts import IncidentReport, replay_passes
from romulus_contracts.codepatch import validate_code_patch

EXAMPLES = Path(__file__).resolve().parents[3] / "packages" / "contracts" / "examples"
T0 = datetime(2026, 11, 7, 22, 0, tzinfo=UTC)
LATER = T0 + timedelta(minutes=20)


@pytest.fixture
def cloud() -> FakeCloud:
    return FakeCloud.seeded()


def booted(cloud: FakeCloud, tmp_path: Path, device_id: str = "sim-1", **kw) -> FakeArm:
    arm = FakeArm(cloud.http(), device_id, tmp_path / device_id, **kw)
    arm.tick(T0)  # first boot: v1 installed
    cloud.requests.clear()
    return arm


def only_incident(cloud: FakeCloud) -> dict:
    (incident,) = cloud.incidents.values()
    IncidentReport.model_validate(incident)
    return incident


def evidence_paths(incident: dict) -> list[str]:
    paths = [f"/v1/devices/{incident['device']}/incidents"]
    for rid in incident["recordings"]:
        paths += [f"/v1/devices/{incident['device']}/recordings", f"/v1/recordings/{rid}/blob"]
    return paths


def test_rules_patch(cloud, tmp_path):
    arm = booted(cloud, tmp_path)
    fault, held = rules_patch(arm, hold_after_s=1200)
    assert (fault.at_s, held.at_s) == (0, 1200)

    fault.run(T0)
    incident = only_incident(cloud)
    assert cloud.paths() == evidence_paths(incident)  # incident first, then its recordings
    assert incident["kind"] == "camera_occluded"
    assert incident["running"] == {
        "firmware": 1,
        "harness": 1,
        "code_rev": "c0de001",
        "mcu_build": None,
    }
    overlay = incident["overlay"]
    assert overlay["diff"] == {"rules": {"camera_occluded": {"below": 0.35}}}
    assert overlay["code_patch"] is None
    assert incident["outcome"]["category"] == "fix-config"
    kinds = sorted(cloud.recordings[r]["kind"] for r in incident["recordings"])
    assert kinds == ["agent_trace", "incident_window", "log_window"]
    assert all(
        cloud.recordings[r]["incident_id"] == incident["incident_id"]
        for r in incident["recordings"]
    )

    # The candidate the batch job would build (v1 + diff) passes replay on these signals.
    candidate = merge_rules(V1_MANIFEST["rules"], overlay["diff"])
    assert replay_passes(candidate, incident["signals"], incident["outcome"]["resolving_action"])

    arm.tick(T0 + timedelta(seconds=10))
    checkin = cloud.checkins["sim-1"][-1]
    assert (checkin["overlay_id"], checkin["health"]) == (overlay["overlay_id"], "degraded")

    cloud.requests.clear()
    held.run(LATER)
    assert cloud.paths() == [f"/v1/devices/sim-1/overlays/{overlay['overlay_id']}"]
    assert cloud.overlays[-1][2]["status"] == "held"
    arm.tick(LATER + timedelta(seconds=10))
    checkin = cloud.checkins["sim-1"][-1]
    assert (checkin["overlay_id"], checkin["health"]) == (overlay["overlay_id"], "ok")


def test_held_is_skipped_when_a_new_version_replaced_the_overlay(cloud, tmp_path):
    arm = booted(cloud, tmp_path)
    fault, held = rules_patch(arm)
    fault.run(T0)
    arm.device.drop_overlay()
    cloud.requests.clear()
    held.run(LATER)
    assert cloud.requests == []


def test_code_patch_d1(cloud, tmp_path):
    arm = booted(cloud, tmp_path)
    fault, held = code_patch(arm)
    fault.run(T0)
    incident = only_incident(cloud)
    assert cloud.paths() == evidence_paths(incident)
    assert incident["kind"] == "unexpected_release"
    assert incident["running"]["code_rev"] == "c0de001"  # the patch's base
    assert incident["overlay"]["code_patch"] == D1_CODE_PATCH
    assert validate_code_patch(D1_CODE_PATCH) == []
    assert incident["outcome"]["category"] == "fix-code"
    assert incident["tier"] == 3 and incident["log_tail"]

    by_kind = {cloud.recordings[r]["kind"]: cloud.recordings[r] for r in incident["recordings"]}
    log_blob = cloud.blobs[by_kind["log_window"]["sha256"]]
    lines = [
        json.loads(m.data)["line"] for _, _, m in make_reader(io.BytesIO(log_blob)).iter_messages()
    ]
    assert lines == incident["log_tail"]
    trace = cloud.blobs[by_kind["agent_trace"]["sha256"]].decode().splitlines()
    assert json.loads(trace[-1])["content"] == incident["agent"]["reasoning"]

    held.run(LATER)
    _, overlay_id, status = cloud.overlays[-1]
    assert (overlay_id, status["status"]) == (incident["overlay"]["overlay_id"], "held")


def test_d1_patch_matches_the_contracts_example():
    example = json.loads((EXAMPLES / "incident_d1_code_patch.json").read_text())
    assert D1_CODE_PATCH == example["overlay"]["code_patch"]


def test_bad_candidate_rolls_back_on_the_canary(cloud, tmp_path):
    canary = booted(cloud, tmp_path, "sim-2", canary=True)
    steps = bad_candidate(canary, hold_after_s=60)
    assert [s.name for s in steps] == ["bad-candidate mode on", "camera_occluded", "overlay held"]
    for step in steps:
        step.run(T0)
    assert cloud.overlays[-1][2]["status"] == "held"

    # The batch job builds v2 from the held overlay; the gate assigns it to the canary.
    cloud.add_firmware({**V1_MANIFEST, "version": 2})
    cloud.assign("sim-2", firmware=2, harness=1)
    cloud.requests.clear()
    canary.tick(T0 + timedelta(minutes=2))
    assert cloud.paths() == [
        "/v1/devices/sim-2/checkin",
        "/v1/firmware/2",
        "/v1/devices/sim-2/installs",
    ]
    body = cloud.installs[-1][1]
    assert (body["type"], body["version"], body["result"]) == ("firmware", 2, "rolled_back")
    assert "health failing" in body["detail"]
    assert canary.device.firmware == 1

    # The gate rolls the canary back to last good; it stays healthy on v1.
    cloud.assign("sim-2", firmware=1, harness=1)
    canary.tick(T0 + timedelta(minutes=3))
    checkin = cloud.checkins["sim-2"][-1]
    assert (checkin["running"]["firmware"], checkin["health"]) == (1, "ok")

    # Bad mode was for one version only: the next candidate installs normally.
    cloud.add_firmware({**V1_MANIFEST, "version": 3})
    cloud.assign("sim-2", firmware=3, harness=1)
    canary.tick(T0 + timedelta(minutes=4))
    assert cloud.installs[-1][1]["result"] == "installed"


def test_park_hardware(cloud, tmp_path):
    arm = booted(cloud, tmp_path)
    (fault,) = park_hardware(arm)
    fault.run(T0)
    incident = only_incident(cloud)
    assert cloud.paths() == evidence_paths(incident)
    assert incident["overlay"] is None
    assert incident["outcome"] == {
        "resolved": False,
        "resolving_action": "park",
        "suspected_cause": "hardware",
        "category": "park",
    }
    arm.tick(T0 + timedelta(seconds=10))
    assert cloud.checkins["sim-1"][-1]["health"] == "failing"


def test_park_then_repair(cloud, tmp_path):
    arm = booted(cloud, tmp_path)
    fault, repaired = park_hardware(arm, repair_after_s=3600)
    fault.run(T0)
    repaired.run(T0 + timedelta(hours=1))
    arm.tick(T0 + timedelta(hours=1, seconds=10))
    assert cloud.checkins["sim-1"][-1]["health"] == "ok"


def test_incident_made_offline_arrives_once_in_order(cloud, tmp_path):
    """F9 in miniature: faults while the cloud is down, then backfill."""
    arm = booted(cloud, tmp_path)
    cloud.offline = True
    rules_fault, _ = rules_patch(arm)
    (park_fault,) = park_hardware(arm)
    rules_fault.run(T0)
    park_fault.run(T0 + timedelta(seconds=30))
    assert cloud.incidents == {} and len(arm.outbox) == 7

    cloud.offline = False
    arm.tick(T0 + timedelta(minutes=5))
    arm.tick(T0 + timedelta(minutes=6))
    assert [i["kind"] for i in cloud.incidents.values()] == ["camera_occluded", "servo_absent"]
    assert len(cloud.recordings) == 5 and len(arm.outbox) == 0


def test_cli_runs_a_fleet_with_all_scenarios(cloud, tmp_path, monkeypatch):
    cloud = FakeCloud.seeded(devices=("sim-1", "sim-2", "sim-3", "sim-4"))
    real_client = httpx.Client
    monkeypatch.setattr(
        cli.httpx,
        "Client",
        lambda **kw: real_client(
            transport=httpx.MockTransport(cloud.handle), base_url=kw["base_url"]
        ),
    )
    monkeypatch.setattr(cli.time, "sleep", lambda s: None)
    args = "--n 4 --url http://api:8000 --scenario all --hold-after 0 --ticks 2"
    cli.main([*args.split(), "--outbox-dir", str(tmp_path)])
    assert sorted(cloud.checkins) == ["sim-1", "sim-2", "sim-3", "sim-4"]
    assert all(len(c) == 2 for c in cloud.checkins.values())
    by_kind = {i["kind"]: i["device"] for i in cloud.incidents.values()}
    assert by_kind["camera_occluded"] in {"sim-1", "sim-2", "sim-3", "sim-4"}
    assert by_kind["unexpected_release"] != "sim-2"
    assert by_kind["servo_absent"] != "sim-2"
    assert {d for d, _, b in cloud.overlays if b["status"] == "held"} >= {"sim-2"}
    devices = [d for d, _, _ in cloud.overlays]
    assert len(devices) == 3  # rules, code and bad each reported held


def test_plan_puts_bad_on_the_canary(cloud, tmp_path):
    arms = [FakeArm(cloud.http(), f"sim-{i}", tmp_path / str(i), canary=i == 2) for i in (1, 2, 3)]
    steps = cli.plan(arms, ["bad", "rules", "park"], 60)
    assert [(d, s.name) for d, s in steps] == [
        ("sim-2", "bad-candidate mode on"),
        ("sim-2", "camera_occluded"),
        ("sim-2", "overlay held"),
        ("sim-3", "camera_occluded"),
        ("sim-3", "overlay held"),
        ("sim-1", "servo_absent (park)"),
    ]
