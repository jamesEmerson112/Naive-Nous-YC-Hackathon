"""Outbox: queue on disk, retry while offline, drain in order, drop permanent 4xx (D8)."""

import hashlib
import logging
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
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


def files(directory: Path) -> list[Path]:
    """Queue files, without the outbox's own `.seq` counter and lock."""
    return [p for p in directory.iterdir() if not p.name.startswith(".")]


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
    assert files(tmp_path) == []  # blob copies are deleted too
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


def test_permanent_4xx_is_dead_lettered_and_the_queue_moves_on(tmp_path, cloud, caplog):
    def handle(request: httpx.Request) -> httpx.Response:
        if b'"kind":"rejected"' in request.content.replace(b" ", b""):
            return httpx.Response(422, json={"error": "invalid_payload", "detail": "bad kind"})
        return cloud.handle(request)

    http = httpx.Client(transport=httpx.MockTransport(handle), base_url="http://cloud")
    client = DeviceClient(http, "sim-1")
    box = Outbox(tmp_path)
    bad, good = incident("rejected"), incident("next")
    box.add_incident(bad)
    box.add_recording(recording(bad.incident_id, b"x"), b"x")
    box.add_incident(good)
    with caplog.at_level(logging.WARNING, logger="fake_device.outbox"):
        assert box.drain(client, T0) == 2  # the recording and the good incident
    assert "422 invalid_payload: bad kind" in caplog.text
    assert len(box) == 0
    assert list(cloud.incidents) == [str(good.incident_id)]
    (dead,) = box.dead_letters()
    assert dead["payload"]["incident_id"] == str(bad.incident_id)
    assert dead["error"] == "422 invalid_payload: bad kind"


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
    assert box.dead_letters()[0]["raw"] == "{not json"


# -- review fixes: crash safety and shared directories ------------------------------------


def test_two_outboxes_on_one_directory_keep_every_entry_in_order(tmp_path, cloud, client):
    visor_box, healer_box = Outbox(tmp_path), Outbox(tmp_path)  # e.g. two processes
    a, b, c, d = (incident(k) for k in "abcd")
    visor_box.add_incident(a)
    healer_box.add_incident(b)
    visor_box.add_incident(c)
    healer_box.add_incident(d)
    assert len(visor_box) == 4
    assert visor_box.drain(client, T0) == 4
    assert [i["kind"] for i in cloud.incidents.values()] == ["a", "b", "c", "d"]


def test_recording_with_missing_blob_is_dead_lettered(tmp_path, cloud, client):
    box = Outbox(tmp_path)
    inc = incident()
    box.add_recording(recording(inc.incident_id, b"x"), b"x")
    box.add_incident(incident("after"))
    (blob,) = tmp_path.glob("*.bin")
    blob.unlink()  # e.g. an older version crashed between the two deletes
    assert box.drain(client, T0) == 1
    assert [i["kind"] for i in cloud.incidents.values()] == ["after"]
    assert "MissingBlob" in box.dead_letters()[0]["error"]


def test_orphan_blob_is_ignored(tmp_path, cloud, client):
    (tmp_path / "00000000000000000001-deadbeef.recording.bin").write_bytes(b"orphan")
    box = Outbox(tmp_path)
    assert len(box) == 0 and box.drain(client, T0) == 0


def test_remove_deletes_the_entry_before_the_blob(tmp_path, cloud, client, monkeypatch):
    box = Outbox(tmp_path)
    inc = incident()
    box.add_recording(recording(inc.incident_id, b"x"), b"x")
    deleted = []
    real_unlink = Path.unlink

    def spy(self, missing_ok=False):
        deleted.append(self.suffix)
        real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", spy)
    box.drain(client, T0)
    assert deleted == [".json", ".bin"]


def test_writes_leave_no_temp_files(tmp_path):
    box = Outbox(tmp_path)
    inc = incident()
    box.add_incident(inc)
    box.add_recording(recording(inc.incident_id, b"x"), b"x")
    assert sorted(p.suffix for p in files(tmp_path)) == [".bin", ".json", ".json"]


def test_order_survives_a_backwards_clock_step(tmp_path, cloud, client, monkeypatch):
    box = Outbox(tmp_path)
    clock = iter([3_000_000_000, 2_000_000_000, 1_000_000_000])  # NTP steps the clock back
    monkeypatch.setattr(time, "time", lambda: next(clock))
    monkeypatch.setattr(time, "time_ns", lambda: int(next(clock) * 1e9))
    for kind in "abc":
        box.add_incident(incident(kind))
    monkeypatch.undo()
    box.drain(client, T0)
    assert [i["kind"] for i in cloud.incidents.values()] == ["a", "b", "c"]


def test_sequence_persists_across_restarts(tmp_path, cloud, client):
    box = Outbox(tmp_path)
    box.add_incident(incident("a"))
    box.drain(client, T0)  # the queue is empty again
    Outbox(tmp_path).add_incident(incident("b"))  # a new process
    (entry,) = files(tmp_path)
    assert entry.name.startswith(f"{2:020d}-")


ADD_25 = """
import sys, uuid
from datetime import UTC, datetime
from fake_device.outbox import Outbox
from romulus_contracts import IncidentReport, Outcome
box = Outbox(sys.argv[1])
for i in range(25):
    box.add_incident(IncidentReport(incident_id=uuid.uuid4(), occurred_at=datetime.now(UTC),
                                    kind=f"{sys.argv[2]}{i}", outcome=Outcome(resolved=True)))
"""


def test_two_processes_share_one_outbox(tmp_path):
    procs = [subprocess.Popen([sys.executable, "-c", ADD_25, str(tmp_path), tag]) for tag in "xy"]
    assert [p.wait(timeout=60) for p in procs] == [0, 0]
    box = Outbox(tmp_path)
    seqs = sorted(int(p.name.split("-", 1)[0]) for p in tmp_path.glob("*.json"))
    assert seqs == list(range(1, 51))  # 50 entries, each with its own number
    kinds = [e["payload"]["kind"] for e in box.pending()]
    for tag in "xy":  # each process's own entries stay in its order
        assert [k for k in kinds if k[0] == tag] == [f"{tag}{i}" for i in range(25)]
