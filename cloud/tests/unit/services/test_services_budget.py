"""telemetry.enforce_budget (M7, build plan K8): the oldest unlabeled episodes go first;
incident evidence, labeled recordings, snapshot members and packages never do."""

import hashlib
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from fake_device import recordings
from romulus_cloud import events
from romulus_cloud.adapters.blobstore import build_blobstore
from romulus_cloud.models import Blob, Dataset, DatasetVersion, Label, Recording
from romulus_cloud.services import telemetry
from romulus_cloud.services.seed import seed
from romulus_contracts import RecordingMeta, Running

RUNNING = Running(firmware=1, harness=1)


@pytest.fixture
def store(settings):
    return build_blobstore(settings.blob_dir)


@pytest.fixture
def fleet(db_session, store):
    seed(db_session, store)
    db_session.flush()
    return db_session


def upload(session, store, now, meta: RecordingMeta, data: bytes, device="sim-1") -> str:
    raw = meta.model_dump(mode="json")
    telemetry.receive(session, device, meta, raw, now)
    with store.writer() as writer:
        writer.write(data)
        telemetry.store_blob(
            session,
            store,
            str(meta.recording_id),
            writer,
            declared_sha256=meta.sha256,
            media_type=None,
            now=now,
        )
    session.flush()
    return str(meta.recording_id)


def episode(session, store, now, i, pad=20_000):
    meta, data = recordings.episode(now, i, True, RUNNING, pad_bytes=pad)
    return upload(session, store, now, meta, data), meta.sha256


def store_bytes(session) -> int:
    return int(session.scalar(select(func.coalesce(func.sum(Blob.size_bytes), 0))))


def test_under_budget_does_nothing(fleet, store, settings, now):
    episode(fleet, store, now, 1)
    assert telemetry.enforce_budget(fleet, now, settings, store) == 0
    assert events.find(fleet, {}, kinds=[telemetry.PRUNED]) == []


def test_over_budget_deletes_oldest_unlabeled_episodes_only(fleet, store, make_settings, now):
    incident_id = uuid.uuid4()
    meta, data = recordings.incident_window(now, incident_id, RUNNING, ["camera_occluded"])
    evidence = upload(fleet, store, now, meta, data)  # arrives first, so it is the oldest
    eps = [episode(fleet, store, now + timedelta(minutes=i), i) for i in range(1, 6)]
    labeled, _ = eps[0]
    fleet.add(Label(target_type="recording", target_id=labeled, key="good", source="human"))
    fleet.add(Dataset(id="ds_1", name="negatives", purpose="policy"))
    fleet.add(DatasetVersion(dataset_id="ds_1", n=1, recording_ids=[eps[1][0]]))
    fleet.flush()
    two = sum(fleet.get(Blob, sha).size_bytes for _, sha in eps[2:4])
    s = make_settings(blob_budget_gb=(store_bytes(fleet) - two + 0.5) / telemetry.GB)

    deleted = telemetry.enforce_budget(fleet, now + timedelta(hours=1), s, store)

    assert deleted == 2
    assert store_bytes(fleet) <= s.blob_budget_gb * telemetry.GB
    gone = {eps[2][1], eps[3][1]}  # the oldest two that are neither labeled nor snapshotted
    for _, sha in eps:
        assert store.exists(sha) == (sha not in gone)
        assert (fleet.get(Blob, sha) is None) == (sha in gone)
    assert store.exists(meta.sha256) and fleet.get(Recording, evidence) is not None
    assert fleet.scalar(select(func.count()).select_from(Recording)) == 6  # metadata stays
    (event,) = events.find(fleet, {}, kinds=[telemetry.PRUNED])
    assert event.actor_class == "automation"
    assert [d["sha256"] for d in event.data["deleted"]] == [eps[2][1], eps[3][1]]
    assert "2 oldest unlabeled episode blob(s)" in event.why
    assert telemetry.view(fleet, fleet.get(Recording, eps[2][0]))["stored"] is False


def test_a_pinned_unlabeled_episode_survives_the_sweep(fleet, store, make_settings, now):
    """A blob referenced by a dataset version is pinned, even an unlabeled episode."""
    pinned, pinned_sha = episode(fleet, store, now, 1)
    _, loose_sha = episode(fleet, store, now + timedelta(minutes=1), 2)
    fleet.add(Dataset(id="ds_pin", name="negatives", purpose="policy"))
    fleet.add(DatasetVersion(dataset_id="ds_pin", n=1, recording_ids=[pinned]))
    fleet.flush()
    s = make_settings(blob_budget_gb=1 / telemetry.GB)  # 1 byte: everything is over
    assert telemetry.enforce_budget(fleet, now, s, store) == 1
    assert store.exists(pinned_sha) and fleet.get(Blob, pinned_sha) is not None
    assert not store.exists(loose_sha)
    assert telemetry.enforce_budget(fleet, now, s, store) == 0  # nothing else may go


def test_a_blob_shared_with_incident_evidence_is_kept(fleet, store, make_settings, now):
    rid, sha = episode(fleet, store, now, 1)
    data = store.get(sha).read()
    twin = RecordingMeta(
        recording_id=uuid.uuid4(),
        kind="incident_window",
        format="lerobot_v3_tar",
        sha256=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data),
        started_at=now,
        incident_id=uuid.uuid4(),
    )
    upload(fleet, store, now, twin, data)
    s = make_settings(blob_budget_gb=1 / telemetry.GB)  # 1 byte: everything is over
    assert telemetry.enforce_budget(fleet, now, s, store) == 0
    assert store.exists(sha)
    assert events.find(fleet, {}, kinds=[telemetry.PRUNED]) == []


def test_still_over_budget_says_so(fleet, store, make_settings, now):
    episode(fleet, store, now, 1)
    s = make_settings(blob_budget_gb=1 / telemetry.GB)  # packages alone are over
    assert telemetry.enforce_budget(fleet, now, s, store) == 1
    (event,) = events.find(fleet, {}, kinds=[telemetry.PRUNED])
    assert "still" in event.why and "never deleted" in event.why
    assert store_bytes(fleet) > 0  # the seed's packages are never touched


def test_packages_are_never_pruned(fleet, store, make_settings, now):
    before = store_bytes(fleet)
    s = make_settings(blob_budget_gb=1 / telemetry.GB)
    assert telemetry.enforce_budget(fleet, now, s, store) == 0
    assert store_bytes(fleet) == before
    for blob in fleet.scalars(select(Blob)):
        assert store.get(blob.sha256).read(1)


def test_an_automatic_label_alone_does_not_protect_an_episode(fleet, store, make_settings, now):
    rid, sha = episode(fleet, store, now, 1)
    labels = telemetry.current_labels(fleet, "recording", rid)
    assert "episode:success" in labels  # auto labels exist...
    s = make_settings(blob_budget_gb=1 / telemetry.GB)
    assert telemetry.enforce_budget(fleet, now, s, store) == 1  # ...but don't count
    assert not store.exists(sha)


def test_a_removed_human_label_no_longer_protects(fleet, store, make_settings, now):
    rid, sha = episode(fleet, store, now, 1)
    for removed in (False, True):
        fleet.add(
            Label(target_type="recording", target_id=rid, key="k", source="human", removed=removed)
        )
    fleet.flush()
    s = make_settings(blob_budget_gb=1 / telemetry.GB)
    assert telemetry.enforce_budget(fleet, now, s, store) == 1


def test_worker_runs_it_every_ten_minutes():
    from romulus_cloud import worker

    (step,) = [s for s in worker.STEPS if s.name == "telemetry.enforce_budget"]
    assert step.every_s == 600 and step.fn is telemetry.enforce_budget
