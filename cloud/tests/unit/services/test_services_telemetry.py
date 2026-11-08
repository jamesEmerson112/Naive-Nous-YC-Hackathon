"""services/telemetry: spooling, labels and linking behind the M4 recording routes."""

import hashlib
import uuid
from datetime import timedelta

import pytest

from romulus_cloud import events
from romulus_cloud.adapters.blobstore import build_blobstore
from romulus_cloud.errors import ApiError
from romulus_cloud.models import Label
from romulus_cloud.services import incidents, telemetry
from romulus_cloud.services.seed import seed
from romulus_contracts import IncidentReport, RecordingMeta


def test_spool_hashes_counts_and_caps():
    with telemetry.Spool(max_bytes=10) as spool:
        spool.write(b"hello ")
        spool.write(b"bob")
        assert spool.size == 9
        assert spool.sha256 == hashlib.sha256(b"hello bob").hexdigest()
        with pytest.raises(ApiError) as exc:
            spool.write(b"!!")
    assert exc.value.status == 413 and exc.value.error == "blob_too_large"


@pytest.mark.parametrize(
    "n,text", [(None, "size unknown"), (12, "12 B"), (2048, "2.0 KB"), (3 << 20, "3.0 MB")]
)
def test_size_reads_well(n, text):
    assert telemetry._size(n) == text


@pytest.fixture
def store(settings):
    return build_blobstore(settings.blob_dir)


@pytest.fixture
def fleet(db_session, store):
    seed(db_session, store)
    db_session.flush()
    return db_session


def _meta(data: bytes, **over):
    raw = {
        "recording_id": str(uuid.uuid4()),
        "kind": "agent_trace",
        "format": "jsonl",
        "sha256": hashlib.sha256(data).hexdigest(),
        "size_bytes": len(data),
        "started_at": "2026-11-07T22:01:41Z",
        "ended_at": "2026-11-07T22:02:41Z",
        "running": {"firmware": 1, "harness": 1},
        **over,
    }
    return RecordingMeta.model_validate(raw), raw


def test_current_labels_replays_tombstones(fleet, now):
    rec, _, _ = telemetry.receive(fleet, "arm-1", *_meta(b"x"), now)
    assert telemetry.current_labels(fleet, "recording", rec.id) == ["firmware:v1"]
    fleet.add(
        Label(
            target_type="recording",
            target_id=rec.id,
            key="firmware",
            value="v1",
            source="human",
            removed=True,
        )
    )
    fleet.flush()
    assert telemetry.current_labels(fleet, "recording", rec.id) == []


def test_store_blob_refuses_wrong_bytes_and_keeps_nothing(fleet, store, now):
    rec, _, _ = telemetry.receive(fleet, "arm-1", *_meta(b"right"), now)
    with telemetry.Spool(100) as spool:
        spool.write(b"wrong")
        with pytest.raises(ApiError) as exc:
            telemetry.store_blob(
                fleet, store, rec.id, spool, declared_sha256=None, media_type=None, now=now
            )
    assert exc.value.status == 409 and not store.exists(rec.sha256)
    assert not telemetry.is_stored(fleet, rec.sha256)


def test_store_blob_once_then_resend_is_quiet(fleet, store, now):
    rec, _, upload = telemetry.receive(fleet, "arm-1", *_meta(b"bytes"), now)
    assert upload == "needed"
    for expected in (True, False):
        with telemetry.Spool(100) as spool:
            spool.write(b"bytes")
            stored = telemetry.store_blob(
                fleet,
                store,
                rec.id,
                spool,
                declared_sha256=rec.sha256.upper(),
                media_type=None,
                now=now,
            )
        assert stored is expected
    assert len(events.find(fleet, {"recording": rec.id}, kinds=[telemetry.BLOB_STORED])) == 1


def test_link_incident_links_listed_recordings_and_labels_them(fleet, now, settings):
    _, raw = _meta(b"trace")
    rec, _, _ = telemetry.receive(fleet, "arm-1", RecordingMeta.model_validate(raw), raw, now)
    assert rec.incident_id is None
    body = {
        "incident_id": str(uuid.uuid4()),
        "occurred_at": "2026-11-07T22:02:11Z",
        "kind": "servo_absent",
        "summary": "no reply from servo 3",
        "outcome": {"resolved": False, "suspected_cause": "hardware", "category": "park"},
        "recordings": [rec.id],
    }
    incident, _ = incidents.report(
        fleet,
        "arm-1",
        IncidentReport.model_validate(body),
        body,
        now,
        safety_paths=settings.safety_paths,
    )
    assert telemetry.link_incident(fleet, incident, now + timedelta(seconds=1)) == 1
    assert rec.incident_id == incident.id
    labels = telemetry.current_labels(fleet, "recording", rec.id)
    assert {"kind:servo_absent", "cause:hardware", "outcome:unresolved", "healer:park"} <= set(
        labels
    )
    # Nothing left to do the second time.
    assert telemetry.link_incident(fleet, incident, now) == 0


def test_receive_refuses_unknown_device(db_session, now):
    with pytest.raises(ApiError) as exc:
        telemetry.receive(db_session, "ghost", *_meta(b"x"), now)
    assert exc.value.status == 404
