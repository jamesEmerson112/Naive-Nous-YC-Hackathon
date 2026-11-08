"""DeviceClient against the in-memory FakeCloud: every device route (APIs §3)."""

import hashlib
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from fake_device.client import DeviceClient, HashMismatch
from fake_device.fake_cloud import V1_MANIFEST, FakeCloud
from romulus_contracts import (
    CONTRACT_VERSION,
    CheckIn,
    IncidentReport,
    InstallResult,
    OverlayStatus,
    RecordingMeta,
)

EXAMPLES = Path(__file__).resolve().parents[3] / "packages" / "contracts" / "examples"
NOW = datetime(2026, 11, 7, 22, 0, tzinfo=UTC)


def example(name: str) -> dict:
    return json.loads((EXAMPLES / name).read_text())


@pytest.fixture
def cloud() -> FakeCloud:
    return FakeCloud.seeded()


@pytest.fixture
def client(cloud: FakeCloud) -> DeviceClient:
    return DeviceClient(cloud.http(), "sim-1")


def test_checkin_returns_assignment(cloud, client):
    resp = client.checkin(CheckIn.model_validate(example("checkin_first_boot.json")))
    assert resp.assigned.firmware == 1 and resp.assigned.harness == 1
    assert not resp.frozen and not resp.drop_overlay
    assert cloud.paths() == ["/v1/devices/sim-1/checkin"]
    assert cloud.checkins["sim-1"][0]["running"] is None


def test_checkin_keeps_extra_fields(cloud, client):
    client.checkin(CheckIn.model_validate(example("checkin_degraded_overlay.json")))
    assert cloud.checkins["sim-1"][0]["health_detail"]["tier"] == 3


def test_pull_firmware_verifies_hash(client):
    manifest = client.pull_firmware(1)
    assert manifest.code_rev == V1_MANIFEST["code_rev"]
    assert manifest.rules == V1_MANIFEST["rules"]


def test_pull_firmware_hash_mismatch(cloud, client):
    cloud.corrupt.add("/v1/firmware/1")
    with pytest.raises(HashMismatch) as err:
        client.pull_firmware(1)
    assert err.value.expected == "0" * 64


def test_pull_firmware_missing_is_http_error(client):
    with pytest.raises(httpx.HTTPStatusError) as err:
        client.pull_firmware(99)
    assert err.value.response.status_code == 404


def test_pull_packages_verify_hash(cloud, client):
    assert client.pull_harness(1) == cloud.harness[1]
    assert client.pull_code("c0de001") == cloud.code["c0de001"]
    cloud.corrupt.update({"/v1/harness/1/package", "/v1/code/c0de001/package"})
    with pytest.raises(HashMismatch):
        client.pull_harness(1)
    with pytest.raises(HashMismatch):
        client.pull_code("c0de001")


def test_report_install(cloud, client):
    client.report_install(InstallResult.model_validate(example("install_result_firmware.json")))
    device, body = cloud.installs[0]
    assert device == "sim-1" and body["result"] == "installed"
    assert body["contract_version"] == CONTRACT_VERSION  # added to every request


def test_report_incident_is_idempotent(cloud, client):
    incident = IncidentReport.model_validate(example("incident_rules_patch.json"))
    first = client.report_incident(incident)
    second = client.report_incident(incident)
    assert first.incident_id == second.incident_id == incident.incident_id
    assert len(cloud.incidents) == 1


def test_report_incident_code_patch_round_trips(cloud, client):
    raw = example("incident_d1_code_patch.json")
    client.report_incident(IncidentReport.model_validate(raw))
    stored = cloud.incidents[raw["incident_id"]]
    assert stored["overlay"]["code_patch"] == raw["overlay"]["code_patch"]
    assert stored["outcome"]["category"] == "fix-code" and stored["tier"] == 3


def test_report_overlay(cloud, client):
    client.report_overlay("ov-12", OverlayStatus(status="held", reason="no recurrence"))
    device, overlay_id, body = cloud.overlays[0]
    assert (device, overlay_id, body["status"]) == ("sim-1", "ov-12", "held")
    assert cloud.paths() == ["/v1/devices/sim-1/overlays/ov-12"]


def _meta(data: bytes) -> RecordingMeta:
    return RecordingMeta(
        recording_id=uuid.uuid4(),
        kind="agent_trace",
        format="jsonl",
        sha256=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data),
        started_at=NOW,
    )


def test_upload_recording_metadata_then_blob(cloud, client):
    data = b'{"role": "assistant"}\n'
    meta = _meta(data)
    accepted = client.upload_recording(meta, data)
    assert accepted.upload == "needed"
    assert cloud.paths() == [
        "/v1/devices/sim-1/recordings",
        f"/v1/recordings/{meta.recording_id}/blob",
    ]
    assert cloud.blobs[meta.sha256] == data


def test_upload_recording_skips_known_blob(cloud, client):
    data = b"same bytes"
    client.upload_recording(_meta(data), data)
    again = _meta(data)
    assert client.upload_recording(again, data).upload == "exists"
    assert cloud.paths("PUT") == [cloud.paths("PUT")[0]]  # only the first blob was uploaded


def test_blob_with_wrong_sha_is_409(cloud, client):
    meta = _meta(b"declared")
    client.create_recording(meta)
    with pytest.raises(httpx.HTTPStatusError) as err:
        client.upload_blob(str(meta.recording_id), b"different bytes")
    assert err.value.response.status_code == 409
    assert err.value.response.json()["error"] == "sha256_mismatch"


def test_offline_raises_transport_error(cloud, client):
    cloud.offline = True
    with pytest.raises(httpx.TransportError):
        client.pull_firmware(1)
