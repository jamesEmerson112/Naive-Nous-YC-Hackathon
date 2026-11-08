"""Telemetry ingest (APIs §3.7, telemetry §2–5, flow F7): store raw, interpret later.

A recording arrives as metadata first (`receive`), then its bytes (`store_blob`). `kind`
and `format` are open strings and nothing is parsed. Blobs are content-addressed, so two
recordings with the same sha256 share one blob. Recording events carry `recording` and,
when known, `incident` in their refs, so the incident's story shows them as evidence.
An incident may arrive before or after its recordings; `link_incident` catches up.
`enforce_budget` (a worker step, M7) deletes the oldest unlabeled episode blobs once the
store is over `BLOB_BUDGET_GB`; incident evidence is never deleted. Nothing here commits.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from romulus_cloud import events
from romulus_cloud.adapters.blobstore import (
    BlobNotFound,
    BlobStore,
    BlobTooLarge,
    BlobWriter,
    build_blobstore,
)
from romulus_cloud.domain.labels import auto_labels, split_label
from romulus_cloud.errors import ApiError
from romulus_cloud.log import log_line
from romulus_cloud.models import (
    Blob,
    DatasetVersion,
    Incident,
    Label,
    Overlay,
    Recording,
    WorkOrder,
)
from romulus_cloud.services.devices import get_device
from romulus_cloud.settings import Settings, get_settings
from romulus_contracts import IncidentReport, RecordingMeta

RECEIVED = "recording.received"
BLOB_STORED = "recording.blob_stored"
LINKED = "recording.linked"
PRUNED = "recording.blobs_pruned"

GB = 1024**3
"""`BLOB_BUDGET_GB` counts binary gigabytes."""


def download_path(recording_id: str) -> str:
    return f"/v1/recordings/{recording_id}/blob"


def get_recording(session: Session, recording_id: str) -> Recording:
    rec = session.get(Recording, recording_id)
    if rec is None:
        raise ApiError(
            404,
            "unknown_recording",
            f"Recording {recording_id} does not exist. Send its metadata first "
            "(POST /v1/devices/{device_id}/recordings).",
        )
    return rec


def is_stored(session: Session, sha256: str) -> bool:
    return session.get(Blob, sha256) is not None


def current_labels(session: Session, target_type: str, target_id: str) -> list[str]:
    """Labels as `key:value` strings after replaying the append-only rows and tombstones."""
    rows = session.scalars(
        select(Label)
        .where(Label.target_type == target_type, Label.target_id == target_id)
        .order_by(Label.id)
    )
    labels: dict[str, None] = {}
    for row in rows:
        label = row.key if row.value is None else f"{row.key}:{row.value}"
        if row.removed:
            labels.pop(label, None)
        else:
            labels[label] = None
    return sorted(labels)


def evidence(rec: Recording, stored: bool) -> dict[str, Any]:
    """What a story shows for one recording: kind, format, size, time range, download."""
    return {
        "recording": rec.id,
        "kind": rec.kind,
        "format": rec.format,
        "size_bytes": rec.size_bytes,
        "sha256": rec.sha256,
        "started_at": rec.started_at.isoformat() if rec.started_at else None,
        "ended_at": rec.ended_at.isoformat() if rec.ended_at else None,
        "stored": stored,
        "download": download_path(rec.id),
    }


def view(session: Session, rec: Recording) -> dict[str, Any]:
    """A recording's metadata as JSON for operators, with its current labels."""
    return {
        **evidence(rec, is_stored(session, rec.sha256)),
        "device_id": rec.device_id,
        "incident_id": rec.incident_id,
        "overlay_id": rec.overlay_id,
        "firmware": rec.firmware,
        "harness": rec.harness,
        "episode": rec.episode,
        "streams": rec.streams,
        "schema_version": rec.schema_version,
        "received_at": rec.received_at.isoformat(),
        "labels": current_labels(session, "recording", rec.id),
    }


# --- labels ----------------------------------------------------------------------------------


def _incident_contract(incident: Incident | None) -> IncidentReport | None:
    if incident is None or not incident.raw:
        return None
    return IncidentReport.model_validate(incident.raw)


def _auto_labels_for(session: Session, rec: Recording, incident: Incident | None) -> set[str]:
    overlay_status = None
    if rec.overlay_id:
        overlay = session.get(Overlay, (rec.device_id, rec.overlay_id))
        overlay_status = overlay.status if overlay else None
    escalated = incident is not None and (
        session.scalar(select(WorkOrder.id).where(WorkOrder.incident_id == incident.id)) is not None
    )
    return auto_labels(
        RecordingMeta.model_validate(rec.raw),
        _incident_contract(incident),
        overlay_status=overlay_status,
        escalated=escalated,
    )


def _add_labels(
    session: Session, rec: Recording, labels: set[str] | list[str], source: str, author: str
) -> list[str]:
    """Append the labels the recording doesn't carry yet; return the ones added."""
    have = set(current_labels(session, "recording", rec.id))
    added = sorted({label.strip() for label in labels if label.strip()} - have)
    for label in added:
        key, value = split_label(label)
        session.add(
            Label(
                fleet_id=rec.fleet_id,
                target_type="recording",
                target_id=rec.id,
                key=key,
                value=value,
                source=source,
                author=author,
            )
        )
    session.flush()
    return added


# --- ingest ----------------------------------------------------------------------------------


def _refs(rec: Recording) -> dict[str, Any]:
    refs: dict[str, Any] = {"recording": rec.id, "device": rec.device_id}
    if rec.incident_id:
        refs["incident"] = rec.incident_id
    if rec.overlay_id:
        refs["overlay"] = rec.overlay_id
    return refs


def _evidence_ts(rec: Recording) -> datetime:
    # `recording.received` is placed when the evidence was complete on the device (the end
    # of its window, device time), so the story shows it among the incident's own steps
    # (K9). The upload and link events happen on the server, so they use `now`.
    return rec.ended_at or rec.started_at or rec.received_at


def _upload(session: Session, sha256: str) -> str:
    return "exists" if is_stored(session, sha256) else "needed"


def receive(
    session: Session, device_id: str, meta: RecordingMeta, raw: dict[str, Any], now: datetime
) -> tuple[Recording, bool, str]:
    """Store a recording's metadata. Returns (recording, created, upload); a retry is a
    no-op. `upload` is "exists" when a blob with that sha256 is already stored."""
    device = get_device(session, device_id)
    rid = str(meta.recording_id)
    existing = session.get(Recording, rid)
    if existing is not None:
        if existing.device_id != device_id:
            raise ApiError(
                409,
                "recording_id_conflict",
                f"Recording {rid} was already sent by {existing.device_id}; "
                "recording ids must be unique UUIDs.",
            )
        return existing, False, _upload(session, existing.sha256)

    incident_id = str(meta.incident_id) if meta.incident_id else None
    if incident_id is None:
        # The incident may have listed this recording before the recording said whose it is.
        incident_id = session.scalar(
            select(Incident.id).where(
                Incident.device_id == device_id, Incident.recording_ids.contains([rid])
            )
        )
    running = meta.running
    rec = Recording(
        id=rid,
        fleet_id=device.fleet_id,
        device_id=device_id,
        kind=meta.kind,
        format=meta.format,
        sha256=meta.sha256,
        size_bytes=meta.size_bytes,
        started_at=meta.started_at,
        ended_at=meta.ended_at,
        received_at=now,
        firmware=running.firmware if running else None,
        harness=running.harness if running else None,
        overlay_id=meta.overlay_id,
        incident_id=incident_id,
        episode=meta.episode.model_dump(mode="json") if meta.episode else None,
        streams=list(meta.streams),
        schema_version=meta.schema_version,
        raw=raw,
    )
    session.add(rec)
    session.flush()

    incident = session.get(Incident, incident_id) if incident_id else None
    auto = _add_labels(session, rec, _auto_labels_for(session, rec, incident), "auto", "auto")
    suggested = _add_labels(session, rec, meta.labels, "agent", f"device:{device_id}")
    stored = is_stored(session, rec.sha256)

    whose = f"incident {incident_id}" if incident_id else "no incident"
    if incident_id and incident is None:
        whose += " (not reported yet; it links when it arrives)"
    then = (
        "a blob with that sha256 is already stored, so no upload is needed"
        if stored
        else "the bytes come next"
    )
    events.record(
        session,
        f"device:{device_id}",
        "automation",
        RECEIVED,
        _refs(rec),
        f"{device_id} sent a {rec.kind} recording ({rec.format}, {size_text(rec.size_bytes)}) "
        f"for {whose}; {then}.",
        data={
            "evidence": evidence(rec, stored),
            "upload": "exists" if stored else "needed",
            "labels": {"auto": auto, "device": suggested},
        },
        ts=_evidence_ts(rec),
        fleet_id=rec.fleet_id,
    )
    return rec, True, "exists" if stored else "needed"


def write_chunk(writer: BlobWriter, chunk: bytes) -> None:
    """Add one chunk of an upload; past the writer's cap, a 413 with a clear sentence."""
    try:
        writer.write(chunk)
    except BlobTooLarge as exc:
        raise too_large(exc.max_bytes) from exc


def too_large(max_bytes: int) -> ApiError:
    return ApiError(
        413,
        "blob_too_large",
        f"Recordings are capped at {size_text(max_bytes)} (RECORDING_MAX_BYTES); nothing was "
        "stored. Split the window or lower the camera resolution.",
    )


def _mismatch(detail: str) -> ApiError:
    return ApiError(409, "sha256_mismatch", detail + " Nothing was stored; resend the file.")


def store_blob(
    session: Session,
    store: BlobStore,
    recording_id: str,
    writer: BlobWriter,
    *,
    declared_sha256: str | None,
    media_type: str | None,
    now: datetime,
) -> bool:
    """Keep a recording's bytes (already streamed into `writer`) if they hash to its sha256.
    Returns whether this call stored them for this recording (False on a harmless resend).
    Safe under concurrent uploads of the same bytes: the blob row is insert-or-nothing."""
    rec = get_recording(session, recording_id)
    declared = (declared_sha256 or "").strip().lower()
    if declared and declared != rec.sha256:
        raise _mismatch(
            f"The X-Sha256 header says {declared}, but recording {rec.id}'s metadata says "
            f"{rec.sha256}."
        )
    if writer.sha256 != rec.sha256:
        raise _mismatch(
            f"The bytes received for recording {rec.id} hash to {writer.sha256}, not the "
            f"{rec.sha256} its metadata declared."
        )

    deduplicated = is_stored(session, rec.sha256)
    if not deduplicated or not store.exists(rec.sha256):
        size = writer.commit(rec.sha256)  # the hash matched above; this renames into place
        session.execute(
            pg_insert(Blob)
            .values(
                sha256=rec.sha256,
                fleet_id=rec.fleet_id,
                size_bytes=size,
                media_type=media_type,
                path=rec.sha256,
            )
            .on_conflict_do_nothing(index_elements=["sha256"])
        )

    noted = events.find(session, {"recording": rec.id}, kinds=[RECEIVED, BLOB_STORED])
    if any(e.kind == BLOB_STORED or (e.data or {}).get("upload") == "exists" for e in noted):
        return False  # a resend: the story already says these bytes are here
    how = (
        "already stored under that sha256 by another recording, so nothing was written twice"
        if deduplicated
        else "stored under its sha256"
    )
    events.record(
        session,
        f"device:{rec.device_id}",
        "automation",
        BLOB_STORED,
        _refs(rec),
        f"The {rec.kind} bytes from {rec.device_id} ({size_text(writer.size)}) matched their "
        f"sha256 and are {how}; they can be downloaded.",
        data={
            "evidence": evidence(rec, True),
            "deduplicated": deduplicated,
        },
        ts=now,
        fleet_id=rec.fleet_id,
    )
    return True


def open_blob(session: Session, store: BlobStore, rec: Recording):
    """The recording's bytes for download. 404 until they are uploaded."""
    if not is_stored(session, rec.sha256):
        raise ApiError(
            404,
            "blob_not_uploaded",
            f"Recording {rec.id}'s bytes have not been uploaded yet "
            f"(PUT /v1/recordings/{rec.id}/blob).",
        )
    try:
        return store.get(rec.sha256)
    except BlobNotFound as exc:
        raise ApiError(
            503, "blob_missing", f"Recording {rec.id}'s bytes are missing from the blob store."
        ) from exc


def link_incident(session: Session, incident: Incident, now: datetime) -> int:
    """After an incident arrives: link recordings that came first and add the labels that
    need the incident (kind, outcome, cause). Returns how many recordings changed."""
    listed = [str(r) for r in incident.recording_ids or []]
    stmt = select(Recording).where(Recording.device_id == incident.device_id)
    if listed:
        stmt = stmt.where((Recording.incident_id == incident.id) | Recording.id.in_(listed))
    else:
        stmt = stmt.where(Recording.incident_id == incident.id)
    changed = 0
    for rec in session.scalars(stmt.order_by(Recording.started_at, Recording.id)):
        newly_linked = rec.incident_id is None
        if newly_linked:
            rec.incident_id = incident.id
            session.flush()
        added = _add_labels(session, rec, _auto_labels_for(session, rec, incident), "auto", "auto")
        if not newly_linked and not added:
            continue
        changed += 1
        what = "linked it" if newly_linked else "it already named the incident"
        labels = f" and added labels {', '.join(added)}" if added else ""
        events.record(
            session,
            "automation:telemetry",
            "automation",
            LINKED,
            _refs(rec),
            f"Incident {incident.id} ({incident.kind}) arrived after recording {rec.id} "
            f"({rec.kind}); {what}{labels}.",
            data={
                "evidence": evidence(rec, is_stored(session, rec.sha256)),
                "labels_added": added,
                "linked": newly_linked,
            },
            ts=now,
            fleet_id=rec.fleet_id,
        )
    return changed


# --- disk budget (telemetry §7, build plan K8) ---------------------------------------------


def _curated(session: Session, recording_ids: list[str]) -> bool:
    """Whether any of these recordings carries a current label that isn't automatic
    (a person's, an agent's or the device's): those are kept."""
    rows = session.scalars(
        select(Label)
        .where(Label.target_type == "recording", Label.target_id.in_(recording_ids))
        .order_by(Label.id)
    )
    current: dict[tuple[str, str, str | None], str] = {}
    for row in rows:
        key = (row.target_id, row.key, row.value)
        if row.removed:
            current.pop(key, None)
        else:
            current[key] = row.source
    return any(source != "auto" for source in current.values())


def _prunable(session: Session, sha256: str) -> bool:
    """A blob may go only if every recording using it is an episode with no incident,
    no curated label and no dataset snapshot."""
    users = list(session.scalars(select(Recording).where(Recording.sha256 == sha256)))
    if not users or any(r.kind != "episode" or r.incident_id for r in users):
        return False
    ids = [r.id for r in users]
    if _curated(session, ids):
        return False
    in_snapshot = select(DatasetVersion.dataset_id).where(
        or_(*(DatasetVersion.recording_ids.contains([rid]) for rid in ids))
    )
    return session.scalar(in_snapshot.limit(1)) is None


def enforce_budget(
    session: Session,
    now: datetime,
    settings: Settings | None = None,
    store: BlobStore | None = None,
) -> int:
    """Worker step (every 10 min): while the blob store is over `BLOB_BUDGET_GB`, delete
    the oldest unlabeled `episode` blobs. Never incident evidence, labeled recordings,
    snapshot members or packages. One event per batch; returns how many blobs went."""
    settings = settings or get_settings()
    store = store or build_blobstore(settings.blob_dir)
    budget = int(settings.blob_budget_gb * GB)
    total = int(session.scalar(select(func.coalesce(func.sum(Blob.size_bytes), 0))) or 0)
    if total <= budget:
        return 0
    before = total
    oldest = session.execute(
        select(Recording.id, Blob)
        .join(Blob, Blob.sha256 == Recording.sha256)
        .where(Recording.kind == "episode", Recording.incident_id.is_(None))
        .order_by(Recording.received_at, Recording.id)
    ).all()
    deleted: list[dict[str, Any]] = []
    seen: set[str] = set()
    for recording_id, blob in oldest:
        if total <= budget:
            break
        if blob.sha256 in seen:
            continue
        seen.add(blob.sha256)
        if not _prunable(session, blob.sha256):
            continue
        deleted.append(
            {"recording": recording_id, "sha256": blob.sha256, "size_bytes": blob.size_bytes}
        )
        total -= blob.size_bytes
        session.delete(blob)
    if not deleted:
        log_line(
            "telemetry.over_budget",
            level="warning",
            blob_bytes=total,
            budget_bytes=budget,
            note="nothing deletable: only incident evidence, labeled or snapshot blobs left",
        )
        return 0
    session.flush()
    still = (
        ""
        if total <= budget
        else f" It is still {size_text(total - budget)} over: the rest is incident evidence, "
        "labeled recordings or snapshots, which are never deleted."
    )
    events.record(
        session,
        "automation:telemetry",
        "automation",
        PRUNED,
        {},
        f"The blob store was {size_text(before)}, over its {settings.blob_budget_gb:g} GB budget, "
        f"so the {len(deleted)} oldest unlabeled episode blob(s) ({size_text(before - total)}) "
        f"were deleted; their metadata stays.{still}",
        data={
            "deleted": deleted,
            "blob_bytes_before": before,
            "blob_bytes_after": total,
            "budget_bytes": budget,
        },
        ts=now,
    )
    for item in deleted:  # files last, after the rows and the event are in the transaction
        store.delete(item["sha256"])
    return len(deleted)


def size_text(n: int | None) -> str:
    if n is None:
        return "size unknown"
    for unit, scale in (("GB", 1 << 30), ("MB", 1 << 20), ("KB", 1 << 10)):
        if n >= scale:
            return f"{n / scale:.1f} {unit}"
    return f"{n} B"
