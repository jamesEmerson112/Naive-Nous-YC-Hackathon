"""Outbox: queue on disk, retry while offline, drain in order, drop permanent 4xx (D8)."""

import hashlib
import logging
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fake_device.client import DeviceClient
from fake_device.fake_cloud import FakeCloud
from fake_device.outbox import Outbox
from romulus_contracts import (
    Agent,
    IncidentReport,
    InstallResult,
    Outcome,
    OverlayStatus,
    RecordingMeta,
)

T0 = datetime(2026, 11, 7, 22, 0, tzinfo=UTC)


def incident(kind: str = "camera_occluded") -> IncidentReport:
    return IncidentReport(
        incident_id=uuid.uuid4(),
        occurred_at=T0,
        kind=kind,
        agent=Agent(reasoning="test", actions=["safe_pose"]),
        outcome=Outcome(resolved=True, resolving_action="safe_pose"),
    )


def recording(incident_id: uuid.UUID, data: bytes) -> RecordingMeta:
    return RecordingMeta(
        recording_id=uuid.uuid4(),
        kind="agent_trace",
        format="jsonl",
        sha256=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data),
        started_at=T0,
        incident_id=incident_id,
    )


@pytest.fixture
def cloud() -> FakeCloud:
    return FakeCloud.seeded()


@pytest.fixture
def client(cloud: FakeCloud) -> DeviceClient:
    return DeviceClient(cloud.http(), "sim-1")


def test_drain_sends_everything_in_order(tmp_path, cloud, client):
    box = Outbox(tmp_path)
    inc = incident()
    box.add_incident(inc)
    box.add_recording(recording(inc.incident_id, b"trace"), b"trace")
    box.add_overlay_status("ov-1", OverlayStatus(status="held"))
    box.add_install(InstallResult(type="firmware", version=1, result="installed"))
    assert [e["kind"] for e in box.pending()] == ["incident", "recording", "overlay", "install"]

    assert box.drain(client, T0) == 4
    assert len(box) == 0
    assert list(tmp_path.iterdir()) == []  # blob copies are deleted too
    assert [p.rsplit("/", 1)[-1] for p in cloud.paths()] == [
        "incidents",
        "recordings",
        "blob",
        "ov-1",
        "installs",
    ]


def test_offline_keeps_queue_and_drains_in_order_later(tmp_path, cloud, client):
    box = Outbox(tmp_path)
    cloud.offline = True
    first, second = incident("a"), incident("b")
    box.add_incident(first)
    box.add_recording(recording(first.incident_id, b"one"), b"one")
    box.add_incident(second)

    assert box.drain(client, T0) == 0
    assert len(box) == 3 and cloud.requests == []

    cloud.offline = False
    assert box.drain(client, T0 + timedelta(minutes=5)) == 3
    assert list(cloud.incidents) == [str(first.incident_id), str(second.incident_id)]
    assert cloud.paths().index("/v1/devices/sim-1/recordings") == 1  # after its incident


def test_queue_survives_restart(tmp_path, cloud, client):
    cloud.offline = True
    box = Outbox(tmp_path)
    inc = incident()
    box.add_incident(inc)
    box.drain(client, T0)

    reopened = Outbox(tmp_path)  # e.g. the Pi rebooted
    reopened.add_overlay_status("ov-1", OverlayStatus(status="reverted"))
    assert [e["kind"] for e in reopened.pending()] == ["incident", "overlay"]
    cloud.offline = False
    assert reopened.drain(client, T0) == 2
    assert str(inc.incident_id) in cloud.incidents


def test_backoff_after_failure(tmp_path, cloud, client):
    box = Outbox(tmp_path)
    box.add_incident(incident())
    cloud.fail_status = 503
    box.drain(client, T0)
    assert box.retry_at == T0 + timedelta(seconds=2)
    box.drain(client, T0 + timedelta(seconds=3))
    assert box.retry_at == T0 + timedelta(seconds=3 + 4)  # exponential

    cloud.fail_status = None
    calls = len(cloud.requests)
    assert box.drain(client, T0 + timedelta(seconds=5)) == 0  # still backing off
    assert len(cloud.requests) == calls
    assert box.drain(client, T0 + timedelta(seconds=8)) == 1
    assert box.retry_at is None and box.failures == 0


def test_retry_on_5xx_stops_the_queue(tmp_path, cloud, client):
    box = Outbox(tmp_path)
    box.add_incident(incident("a"))
    box.add_incident(incident("b"))
    cloud.fail_status = 500
    box.drain(client, T0)
    assert len(cloud.requests) == 1  # nothing behind the failed entry was tried
    assert len(box) == 2


def test_permanent_4xx_is_dropped_with_a_log_line(tmp_path, cloud, client, caplog):
    box = Outbox(tmp_path)
    box.add_incident(incident())
    good = incident("next")
    cloud.fail_status = 422
    with caplog.at_level(logging.WARNING, logger="fake_device.outbox"):
        assert box.drain(client, T0) == 0
    assert "dropping" in caplog.text
    assert len(box) == 0

    cloud.fail_status = None
    box.add_incident(good)
    assert box.drain(client, T0) == 1


def test_resend_after_lost_response_is_idempotent(tmp_path, cloud, client):
    # The cloud stored the incident but the device never saw the answer, so it sends again.
    inc = incident()
    client.report_incident(inc)
    box = Outbox(tmp_path)
    box.add_incident(inc)
    assert box.drain(client, T0) == 1
    assert len(cloud.incidents) == 1
    assert cloud.paths() == ["/v1/devices/sim-1/incidents"] * 2


def test_corrupt_entry_is_dropped(tmp_path, cloud, client):
    box = Outbox(tmp_path)
    (tmp_path / "00000001.incident.json").write_text("{not json")
    box = Outbox(tmp_path)
    box.add_incident(incident())
    assert box.drain(client, T0) == 1
    assert len(box) == 0
