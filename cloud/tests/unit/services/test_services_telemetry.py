"""services/telemetry: uploads, labels and linking behind the M4 recording routes."""

import hashlib
import threading
import time
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session

from romulus_cloud import events
from romulus_cloud.adapters.blobstore import FsBlobStore, build_blobstore
from romulus_cloud.errors import ApiError
from romulus_cloud.models import Blob, Device, Event, Label, Recording
from romulus_cloud.services import incidents, telemetry
from romulus_cloud.services.seed import seed
from romulus_contracts import IncidentReport, RecordingMeta


def test_write_chunk_caps_with_a_413(tmp_path):
    with FsBlobStore(tmp_path).writer(max_bytes=10) as writer:
        telemetry.write_chunk(writer, b"hello ")
        telemetry.write_chunk(writer, b"bob")
        assert writer.sha256 == hashlib.sha256(b"hello bob").hexdigest()
        with pytest.raises(ApiError) as exc:
            telemetry.write_chunk(writer, b"!!")
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
    with store.writer() as writer:
        writer.write(b"wrong")
        with pytest.raises(ApiError) as exc:
            telemetry.store_blob(
                fleet, store, rec.id, writer, declared_sha256=None, media_type=None, now=now
            )
    assert exc.value.status == 409 and not store.exists(rec.sha256)
    assert not telemetry.is_stored(fleet, rec.sha256)


def test_store_blob_once_then_resend_is_quiet(fleet, store, now):
    rec, _, upload = telemetry.receive(fleet, "arm-1", *_meta(b"bytes"), now)
    assert upload == "needed"
    for expected in (True, False):
        with store.writer() as writer:
            writer.write(b"bytes")
            stored = telemetry.store_blob(
                fleet,
                store,
                rec.id,
                writer,
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


def _store(session, store, recording_id, data, now):
    with store.writer() as writer:
        writer.write(data)
        return telemetry.store_blob(
            session, store, recording_id, writer, declared_sha256=None, media_type=None, now=now
        )


def _wait_for_lock_wait(engine, timeout_s=5.0) -> bool:
    """True once some backend in this test database is waiting on a row lock."""
    deadline = time.monotonic() + timeout_s
    with engine.connect() as conn:
        while time.monotonic() < deadline:
            waiting = conn.execute(
                text(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE datname = current_database() AND wait_event_type = 'Lock'"
                )
            ).scalar()
            conn.rollback()  # pg_stat_activity is a per-transaction snapshot; refresh it
            if waiting:
                return True
            time.sleep(0.02)
    return False


def test_concurrent_first_uploads_of_the_same_bytes_dont_collide(engine, store, now):
    """Two connections store the same new blob at once (the review's 500). The second
    insert waits on the first's uncommitted row, then does nothing instead of failing."""
    device = f"race-{uuid.uuid4().hex[:8]}"
    data = b"the same bytes from two recordings"
    sha = hashlib.sha256(data).hexdigest()
    with Session(engine) as setup:  # real, committed rows; deleted in `finally`
        setup.add(Device(id=device))
        setup.flush()
        a, _, _ = telemetry.receive(setup, device, *_meta(data), now)
        b, _, _ = telemetry.receive(setup, device, *_meta(data), now)
        ids = [a.id, b.id]
        setup.commit()

    errors: list[Exception] = []

    def second() -> None:
        try:
            with Session(engine) as s2:
                _store(s2, store, ids[1], data, now)
                s2.commit()
        except Exception as exc:  # noqa: BLE001 - reported by the assert below
            errors.append(exc)

    first = Session(engine)
    thread = threading.Thread(target=second)
    try:
        assert _store(first, store, ids[0], data, now) is True  # blob row held, uncommitted
        thread.start()
        blocked = _wait_for_lock_wait(engine)
        first.commit()
        thread.join(timeout=10)
        assert errors == [], errors
        assert blocked, "the second upload never reached the blob insert"
        assert not thread.is_alive() and errors == []
        with Session(engine) as check:
            n = check.scalar(select(func.count()).select_from(Blob).where(Blob.sha256 == sha))
            assert n == 1
    finally:
        first.close()
        thread.join(timeout=10)
        with Session(engine) as cleanup:
            cleanup.execute(delete(Event).where(Event.refs.contains({"device": device})))
            cleanup.execute(delete(Label).where(Label.target_id.in_(ids)))
            cleanup.execute(delete(Recording).where(Recording.id.in_(ids)))
            cleanup.execute(delete(Blob).where(Blob.sha256 == sha))
            cleanup.execute(delete(Device).where(Device.id == device))
            cleanup.commit()
