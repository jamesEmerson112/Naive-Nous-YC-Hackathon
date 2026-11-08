"""Labels, dataset snapshots, exports and the regression corpus (telemetry §5–6, flow F8).

- Labels: people add or remove labels on a recording (append-only rows plus tombstones).
- Datasets are saved filters; `snapshot` freezes the current matches into an immutable
  `dataset_versions` row (recording ids, incident ids and a manifest sha256 over the ids
  and each recording's sha256). Later label changes never touch a version.
- Exports are requested here and built by the worker step `run_exports`: each is one
  deterministic tar under `EXPORT_DIR` with a `manifest.json` (snapshot, counts, the sha256
  of every file). A crash before commit leaves it `requested`, so the next tick rebuilds it.
- `sync_regression_cases` keeps one `regression_cases` row per resolved incident;
  `regression_cases(...)` reads them in the shape `domain.gate.replay` takes.

Nothing here commits; routes and worker steps do.
"""

import io
import os
import tarfile
from collections.abc import Iterable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from romulus_cloud import events
from romulus_cloud.adapters.blobstore import BlobNotFound, BlobStore
from romulus_cloud.domain import datasets as dd
from romulus_cloud.domain import gate
from romulus_cloud.domain.gate import ReplayCase
from romulus_cloud.domain.labels import split_label
from romulus_cloud.errors import ApiError
from romulus_cloud.ids import new_id
from romulus_cloud.models import (
    Dataset,
    DatasetVersion,
    Device,
    Export,
    Incident,
    Label,
    Overlay,
    Recording,
    RegressionCase,
    WorkOrder,
)
from romulus_cloud.services.telemetry import current_labels, get_recording

LABEL_CHANGED = "label.changed"
SNAPSHOTTED = "dataset.snapshotted"
CREATED = "dataset.created"
EXPORT_REQUESTED = "export.requested"
EXPORT_BUILT = "export.built"
EXPORT_FAILED = "export.failed"
CASE_ADDED = "regression.case_added"


def _bad(detail: str) -> ApiError:
    return ApiError(422, "invalid_request", detail)


# --- labels ----------------------------------------------------------------------------------


def _recording_refs(rec: Recording) -> dict[str, Any]:
    refs: dict[str, Any] = {"recording": rec.id, "device": rec.device_id}
    if rec.incident_id:
        refs["incident"] = rec.incident_id
    return refs


def change_labels(
    session: Session,
    recording_id: str,
    *,
    add: list[str],
    remove: list[str],
    actor: str,
    why: str,
    now: datetime,
) -> list[str]:
    """Add and remove human labels on one recording; return its labels afterwards.

    Adding a label it has, or removing one it lacks, changes nothing; a call that changes
    nothing writes no event.
    """
    rec = get_recording(session, recording_id)
    clean = lambda labels: sorted({x.strip() for x in labels if x and x.strip()})  # noqa: E731
    add, remove = clean(add), clean(remove)
    if set(add) & set(remove):
        raise _bad(f"Labels {sorted(set(add) & set(remove))} are both added and removed.")
    have = set(current_labels(session, "recording", rec.id))
    added = [x for x in add if x not in have]
    removed = [x for x in remove if x in have]
    for label, tombstone in [(x, False) for x in added] + [(x, True) for x in removed]:
        key, value = split_label(label)
        session.add(
            Label(
                fleet_id=rec.fleet_id,
                target_type="recording",
                target_id=rec.id,
                key=key,
                value=value,
                source="human",
                author=actor,
                removed=tombstone,
            )
        )
    session.flush()
    if added or removed:
        parts = []
        if added:
            parts.append(f"added {', '.join(added)}")
        if removed:
            parts.append(f"removed {', '.join(removed)}")
        reason = f" because {why.strip()}" if why.strip() else ""
        events.record(
            session,
            actor,
            "human",
            LABEL_CHANGED,
            _recording_refs(rec),
            f"{actor.split(':', 1)[-1]} {' and '.join(parts)} on {rec.kind} recording "
            f"{rec.id}{reason}.",
            data={"added": added, "removed": removed, "why": why.strip() or None},
            ts=now,
            fleet_id=rec.fleet_id,
        )
    return current_labels(session, "recording", rec.id)


# --- matching --------------------------------------------------------------------------------


def labels_by_recording(session: Session, fleet_id: str) -> dict[str, list[str]]:
    """Current labels of every recording, from one query (append order, tombstones applied)."""
    rows = session.execute(
        select(Label.target_id, Label.key, Label.value, Label.removed)
        .where(Label.fleet_id == fleet_id, Label.target_type == "recording")
        .order_by(Label.id)
    )
    out: dict[str, dict[str, None]] = {}
    for target, key, value, removed in rows:
        label = key if value is None else f"{key}:{value}"
        have = out.setdefault(target, {})
        if removed:
            have.pop(label, None)
        else:
            have[label] = None
    return {k: sorted(v) for k, v in out.items()}


def find_recordings(
    session: Session, f: dd.Filter, *, fleet_id: str, limit: int | None = None
) -> list[Recording]:
    """Recordings matching `f`, newest first. Filtered in Python: v0 fleets are small, and
    the label rule needs the replayed label view (trigger to move into SQL: > ~50k rows)."""
    stmt = select(Recording).where(Recording.fleet_id == fleet_id)
    if f.kinds:
        stmt = stmt.where(Recording.kind.in_(f.kinds))
    if f.device:
        stmt = stmt.where(Recording.device_id == f.device)
    stmt = stmt.order_by(
        func.coalesce(Recording.started_at, Recording.received_at).desc(), Recording.id
    )
    labels = labels_by_recording(session, fleet_id)
    out = []
    for rec in session.scalars(stmt):
        facts = {"kind": rec.kind, "device_id": rec.device_id, "at": _rec_at(rec)}
        if dd.matches_recording(f, facts, labels.get(rec.id, [])):
            out.append(rec)
            if limit is not None and len(out) >= limit:
                break
    return out


def _rec_at(rec: Recording) -> datetime:
    return rec.started_at or rec.received_at


# --- datasets and snapshots ------------------------------------------------------------------


def parse_filter(raw: dict[str, Any] | None) -> dd.Filter:
    try:
        return dd.parse_filter(raw)
    except ValueError as exc:
        raise _bad(f"Bad dataset filter: {exc}.") from exc


def create_dataset(
    session: Session,
    *,
    name: str,
    purpose: str,
    filter_: dict[str, Any],
    actor: str,
    fleet_id: str,
    now: datetime,
) -> Dataset:
    if not name.strip():
        raise _bad("A dataset needs a name.")
    if purpose not in dd.PURPOSES:
        raise _bad(f"purpose must be one of {list(dd.PURPOSES)}, not {purpose!r}.")
    parse_filter(filter_)
    ds = Dataset(
        id=new_id("ds"), fleet_id=fleet_id, name=name.strip(), purpose=purpose, filter=filter_
    )
    session.add(ds)
    session.flush()
    events.record(
        session,
        actor,
        "human",
        CREATED,
        {"dataset": ds.id},
        f"{actor.split(':', 1)[-1]} saved dataset {ds.name!r} ({purpose}) with filter "
        f"{dd.canonical(filter_).decode()}.",
        ts=now,
        fleet_id=fleet_id,
    )
    return ds


def get_dataset(session: Session, dataset_id: str, *, lock: bool = False) -> Dataset:
    stmt = select(Dataset).where(Dataset.id == dataset_id)
    ds = session.scalar(stmt.with_for_update() if lock else stmt)
    if ds is None:
        raise ApiError(404, "unknown_dataset", f"Dataset {dataset_id} does not exist.")
    return ds


def get_version(session: Session, dataset_id: str, n: int) -> DatasetVersion:
    v = session.get(DatasetVersion, (dataset_id, n))
    if v is None:
        raise ApiError(404, "unknown_dataset_version", f"Dataset {dataset_id} has no version {n}.")
    return v


def list_datasets(session: Session, fleet_id: str) -> list[Dataset]:
    stmt = select(Dataset).where(Dataset.fleet_id == fleet_id).order_by(Dataset.created_at.desc())
    return list(session.scalars(stmt))


def list_versions(session: Session, dataset_id: str) -> list[DatasetVersion]:
    stmt = select(DatasetVersion).where(DatasetVersion.dataset_id == dataset_id)
    return list(session.scalars(stmt.order_by(DatasetVersion.n)))


def snapshot(session: Session, dataset_id: str, *, actor: str, now: datetime) -> DatasetVersion:
    """Freeze the dataset filter's current matches into version n+1.

    Recordings match the filter; incidents join through a matching recording, or on their
    own (device and time range) when the filter names no recording-only field.
    """
    ds = get_dataset(session, dataset_id, lock=True)  # serializes concurrent snapshots
    f = parse_filter(ds.filter)
    recs = find_recordings(session, f, fleet_id=ds.fleet_id)
    incident_ids = {r.incident_id for r in recs if r.incident_id}
    if not f.recording_only:
        stmt = select(Incident.id, Incident.device_id, Incident.occurred_at).where(
            Incident.fleet_id == ds.fleet_id
        )
        for iid, device_id, occurred_at in session.execute(stmt):
            if dd.matches_incident(f, {"device_id": device_id, "occurred_at": occurred_at}):
                incident_ids.add(iid)
    # Only incidents that exist: a recording may name one that hasn't arrived yet.
    if incident_ids:
        known = select(Incident.id).where(Incident.id.in_(incident_ids))
        incident_ids = set(session.scalars(known))
    last = session.scalar(
        select(func.max(DatasetVersion.n)).where(DatasetVersion.dataset_id == ds.id)
    )
    n = (last or 0) + 1
    frozen_recs = [
        {
            "id": r.id,
            "sha256": r.sha256,
            "kind": r.kind,
            "format": r.format,
            "device_id": r.device_id,
            "incident_id": r.incident_id,
        }
        for r in recs
    ]
    # Context for every incident an export may mention, including a trace's own incident.
    context_ids = incident_ids | {r.incident_id for r in recs if r.incident_id}
    manifest, digest = dd.snapshot_manifest(
        ds.id, n, ds.filter, frozen_recs, incident_ids, freeze_context(session, context_ids)
    )
    version = DatasetVersion(
        dataset_id=ds.id,
        n=n,
        fleet_id=ds.fleet_id,
        recording_ids=[r["id"] for r in manifest["recordings"]],
        incident_ids=manifest["incident_ids"],
        manifest_sha256=digest,
    )
    session.add(version)
    session.flush()
    events.record(
        session,
        actor,
        "human",
        SNAPSHOTTED,
        {"dataset": ds.id},
        f"{actor.split(':', 1)[-1]} froze dataset {ds.name!r} as v{n}: "
        f"{len(version.recording_ids)} recordings and {len(version.incident_ids)} incidents "
        "(later label changes don't alter it).",
        # The full manifest (recordings, incident context) lives here; exports read it back.
        data={"n": n, "manifest_sha256": digest, "manifest": manifest},
        ts=now,
        fleet_id=ds.fleet_id,
    )
    return version


def version_view(v: DatasetVersion) -> dict[str, Any]:
    return {
        "dataset_id": v.dataset_id,
        "n": v.n,
        "recording_ids": list(v.recording_ids),
        "incident_ids": list(v.incident_ids),
        "manifest_sha256": v.manifest_sha256,
        "created_at": v.created_at.isoformat() if v.created_at else None,
    }


def dataset_view(session: Session, ds: Dataset) -> dict[str, Any]:
    return {
        "id": ds.id,
        "name": ds.name,
        "purpose": ds.purpose,
        "filter": ds.filter,
        "versions": [version_view(v) for v in list_versions(session, ds.id)],
    }


# --- exports ---------------------------------------------------------------------------------


def request_export(
    session: Session,
    dataset_id: str,
    n: int,
    fmt: str,
    *,
    redact: bool,
    actor: str,
    now: datetime,
) -> Export:
    """Queue an export of one dataset version; the worker builds it (`run_exports`).

    The `redact` option lives in the `export.requested` event (the exports table has no
    options column, and adding one after M7 means a manual ALTER).
    """
    if fmt in dd.CUT_FORMATS:
        raise _bad(f"Format {fmt!r} isn't available: {dd.CUT_FORMATS[fmt]}.")
    if fmt not in dd.FORMATS:
        raise _bad(f"format must be one of {list(dd.FORMATS)}, not {fmt!r}.")
    version = get_version(session, dataset_id, n)
    exp = Export(
        id=new_id("exp"),
        fleet_id=version.fleet_id,
        dataset_id=dataset_id,
        n=n,
        format=fmt,
        status="requested",
        updated_at=now,
    )
    session.add(exp)
    session.flush()
    how = " with secrets and home paths redacted" if redact and fmt == "traces_jsonl" else ""
    events.record(
        session,
        actor,
        "human",
        EXPORT_REQUESTED,
        {"dataset": dataset_id, "export": exp.id},
        f"{actor.split(':', 1)[-1]} asked for a {fmt} export of dataset {dataset_id} "
        f"v{n}{how}; the worker builds it next.",
        data={"n": n, "format": fmt, "redact": redact},
        ts=now,
        fleet_id=version.fleet_id,
    )
    return exp


def get_export(session: Session, export_id: str) -> Export:
    exp = session.get(Export, export_id)
    if exp is None:
        raise ApiError(404, "unknown_export", f"Export {export_id} does not exist.")
    return exp


def list_exports(session: Session, fleet_id: str, limit: int = 50) -> list[Export]:
    stmt = select(Export).where(Export.fleet_id == fleet_id)
    return list(session.scalars(stmt.order_by(Export.created_at.desc()).limit(limit)))


def export_view(exp: Export) -> dict[str, Any]:
    return {
        "id": exp.id,
        "dataset_id": exp.dataset_id,
        "n": exp.n,
        "format": exp.format,
        "status": exp.status,
        "sha256": exp.sha256,
        "error": exp.error,
        "download": f"/v1/exports/{exp.id}/file" if exp.status == "built" else None,
        "created_at": exp.created_at.isoformat() if exp.created_at else None,
        "updated_at": exp.updated_at.isoformat() if exp.updated_at else None,
    }


def _redact_option(session: Session, exp: Export) -> bool:
    asked = events.find(session, {"export": exp.id}, kinds=[EXPORT_REQUESTED])
    return bool(asked and (asked[0].data or {}).get("redact"))


class ExportError(Exception):
    """An export can't be built exactly as its snapshot says; the message says why."""


def _code_patch(inc: Incident) -> bool:
    overlay = (inc.raw or {}).get("overlay")
    return bool(isinstance(overlay, dict) and overlay.get("code_patch"))


def freeze_context(session: Session, incident_ids: set[str]) -> dict[str, dict[str, Any]]:
    """The per-incident state exports read, as of now: the incident's facts plus its
    overlay's status and lock-in and its work order, which change after the snapshot."""
    if not incident_ids:
        return {}
    out = {}
    for inc in session.scalars(select(Incident).where(Incident.id.in_(incident_ids))):
        overlay = session.get(Overlay, (inc.device_id, inc.overlay_id)) if inc.overlay_id else None
        work_order = session.scalar(select(WorkOrder.id).where(WorkOrder.incident_id == inc.id))
        out[inc.id] = {
            "incident_id": inc.id,
            "device_id": inc.device_id,
            "occurred_at": inc.occurred_at.isoformat(),
            "kind": inc.kind,
            "summary": inc.summary,
            "firmware": (inc.running or {}).get("firmware"),
            "signals": inc.signals,
            "resolved": inc.resolved,
            "resolving_action": inc.resolving_action,
            "suspected_cause": inc.suspected_cause,
            "code_patch": _code_patch(inc),
            "overlay_id": inc.overlay_id,
            "overlay_diff": overlay.diff if overlay else None,
            "overlay_outcome": overlay.status if overlay else None,
            "locked_into": overlay.candidate_id if overlay else None,
            "work_order": work_order,
        }
    return out


def frozen_manifest(session: Session, version: DatasetVersion) -> dict[str, Any]:
    """The manifest frozen when `version` was taken, checked against its sha256."""
    for e in events.find(session, {"dataset": version.dataset_id}, kinds=[SNAPSHOTTED]):
        data = e.data or {}
        if data.get("n") == version.n and data.get("manifest") is not None:
            if dd.sha256(dd.canonical(data["manifest"])) != version.manifest_sha256:
                raise ExportError(
                    f"dataset {version.dataset_id} v{version.n}'s frozen manifest no longer "
                    "matches its sha256"
                )
            return data["manifest"]
    raise ExportError(f"dataset {version.dataset_id} v{version.n} has no frozen manifest")


def _read_blob(store: BlobStore, rec: dict[str, Any]) -> bytes:
    """A snapshot recording's bytes. Fails when they are missing or don't match the sha256
    frozen in the snapshot, so an export never quietly shrinks or changes."""
    try:
        with store.get(rec["sha256"]) as fh:
            data = fh.read()
    except BlobNotFound as exc:
        raise ExportError(
            f"recording {rec['id']}'s bytes (sha256 {rec['sha256']}) are missing from the "
            "blob store, so the snapshot can't be exported exactly"
        ) from exc
    if dd.sha256(data) != rec["sha256"]:
        raise ExportError(
            f"recording {rec['id']}'s stored bytes hash to {dd.sha256(data)}, not the "
            f"{rec['sha256']} frozen in the snapshot"
        )
    return data


def _by_time(context: dict[str, dict[str, Any]], ids) -> list[dict[str, Any]]:
    rows = [context[i] for i in ids if i in context]
    return sorted(rows, key=lambda c: (c["occurred_at"], c["incident_id"]))


def _regression_files(manifest: dict[str, Any]) -> tuple[dict, dict]:
    """Not redacted: summaries and causes come from our own devices, and the corpus stays
    in house (telemetry §10)."""
    files: dict[str, bytes] = {}
    evidence: dict[str, list[dict[str, Any]]] = {}
    for rec in manifest["recordings"]:
        if rec.get("incident_id"):
            evidence.setdefault(rec["incident_id"], []).append(
                {k: rec[k] for k in ("id", "kind", "format", "sha256")}
            )
    rows, skipped = [], 0
    for inc in _by_time(manifest["context"], manifest["incident_ids"]):
        case = dd.regression_case(inc)
        if case is None:
            skipped += 1
            continue
        for k in ("device_id", "occurred_at", "firmware", "summary", "suspected_cause"):
            case[k] = inc[k]
        case["evidence"] = evidence.get(inc["incident_id"], [])
        rows.append(case)
        files[f"cases/{inc['incident_id']}/case.json"] = dd.canonical(case)
    files["cases.jsonl"] = dd.jsonl(rows)
    return files, {"cases": len(rows), "incidents_skipped": skipped}


def _traces_files(manifest: dict[str, Any], store: BlobStore, redact: bool) -> tuple[dict, dict]:
    context = manifest["context"]
    lines = []
    for rec in manifest["recordings"]:
        if rec.get("kind") != "agent_trace":
            continue
        text = _read_blob(store, rec).decode("utf-8", errors="replace")
        inc = context.get(rec.get("incident_id") or "")
        facts = ("kind", "summary", "signals", "resolving_action", "suspected_cause")
        state = ("overlay_diff", "overlay_outcome", "locked_into", "work_order")
        line = {
            "recording_id": rec["id"],
            "device_id": rec.get("device_id"),
            "incident_id": rec.get("incident_id"),
            "conversations": dd.sharegpt(text),
            "completed": bool(inc and inc["resolved"]),
            "incident": {k: inc[k] for k in facts} if inc else None,
            **{k: (inc[k] if inc else None) for k in state},
        }
        lines.append(dd.redact(line) if redact else line)
    evals = [
        {
            "incident_id": inc["incident_id"],
            "evidence": f"{inc['kind']}: {inc['summary']}",
            "expected_diagnosis": inc["suspected_cause"],
            "expected_action": inc["resolving_action"],
        }
        for inc in _by_time(context, context)
    ]
    files = {
        "traces.jsonl": dd.jsonl(lines),
        "evals.jsonl": dd.jsonl(dd.redact(evals) if redact else evals),
    }
    return files, {"traces": len(lines), "evals": len(evals)}


def build_export(session: Session, exp: Export, store: BlobStore) -> tuple[bytes, dict[str, Any]]:
    """The export's tar bytes and its manifest, built only from the snapshot's frozen
    manifest and the blobs it names: same snapshot → same bytes, or an ExportError (F8)."""
    version = get_version(session, exp.dataset_id, exp.n)
    ds = get_dataset(session, exp.dataset_id)
    frozen = frozen_manifest(session, version)
    redact = exp.format == "traces_jsonl" and _redact_option(session, exp)
    if exp.format == "regression_corpus":
        files, counts = _regression_files(frozen)
    elif exp.format == "traces_jsonl":
        files, counts = _traces_files(frozen, store, redact)
    else:
        raise ValueError(f"unknown export format {exp.format!r}")
    root = f"{exp.format}_{ds.id}_v{exp.n}"
    manifest = {
        "format": exp.format,
        "dataset_id": ds.id,
        "dataset_name": ds.name,
        "purpose": ds.purpose,
        "n": exp.n,
        "snapshot_sha256": version.manifest_sha256,
        "redact": redact,
        "counts": counts,
        "files": {name: dd.sha256(data) for name, data in sorted(files.items())},
    }
    files["manifest.json"] = dd.canonical(manifest)
    return tar_bytes({f"{root}/{k}": v for k, v in files.items()}), manifest


def tar_bytes(files: Mapping[str, bytes]) -> bytes:
    """A plain tar of `files` with fixed metadata (mtime 0, uid 0, mode 0644), members in
    sorted order: identical files always give an identical archive."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for name in sorted(files):
            data = files[name]
            info = tarfile.TarInfo(name)
            info.size, info.mtime, info.mode = len(data), 0, 0o644
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def export_path(export_dir: str, exp: Export) -> Path:
    return Path(export_dir) / f"{exp.id}.tar"


def run_exports(
    session: Session, now: datetime, *, export_dir: str, store: BlobStore, limit: int = 3
) -> int:
    """Build up to `limit` requested exports; return how many were built or failed.

    Each tar is written to a temp file and renamed into place, so a half-written export is
    never served. A failure marks the export `failed` with the reason (and an event).
    """
    todo = session.scalars(
        select(Export)
        .where(Export.status == "requested")
        .order_by(Export.created_at, Export.id)
        .limit(limit)
        .with_for_update(skip_locked=True)
    ).all()
    for exp in todo:
        refs = {"dataset": exp.dataset_id, "export": exp.id}
        try:
            data, manifest = build_export(session, exp, store)
            path = export_path(export_dir, exp)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tar.tmp")
            tmp.write_bytes(data)
            os.replace(tmp, path)
        except Exception as exc:  # one bad export must not block the others
            exp.status, exp.error, exp.updated_at = "failed", repr(exc)[:2000], now
            events.record(
                session,
                "automation:worker",
                "automation",
                EXPORT_FAILED,
                refs,
                f"The {exp.format} export of {exp.dataset_id} v{exp.n} failed: {exc}.",
                ts=now,
                fleet_id=exp.fleet_id,
            )
            continue
        exp.status, exp.path, exp.sha256, exp.error = "built", str(path), dd.sha256(data), None
        exp.updated_at = now
        counts = ", ".join(f"{v} {k.replace('_', ' ')}" for k, v in manifest["counts"].items())
        events.record(
            session,
            "automation:worker",
            "automation",
            EXPORT_BUILT,
            refs,
            f"Built the {exp.format} export of {exp.dataset_id} v{exp.n} ({counts}); "
            f"download it at /v1/exports/{exp.id}/file.",
            data={"manifest": manifest, "sha256": exp.sha256, "size_bytes": len(data)},
            ts=now,
            fleet_id=exp.fleet_id,
        )
    session.flush()
    return len(todo)


# --- regression corpus for the gate ------------------------------------------------------------


def sync_regression_cases(session: Session, now: datetime) -> int:
    """Add a `regression_cases` row for every resolved incident that can be replayed and
    doesn't have one yet, so the gate gets stricter as the fleet fails (telemetry §1).

    The rule of `domain.datasets.regression_case` runs in SQL (an action that isn't a
    hardware action, object signals, no code patch, no work order) and only the needed
    columns are read, so incidents that never qualify cost nothing each tick. Insert-once
    and idempotent, since incidents never change. Returns rows added.
    """
    code_patch = Incident.raw["overlay"]["code_patch"].astext
    action = func.lower(func.trim(func.coalesce(Incident.resolving_action, "")))
    stmt = (
        select(
            Incident.id,
            Incident.fleet_id,
            Incident.device_id,
            Incident.kind,
            Incident.signals,
            Incident.resolving_action,
        )
        .where(
            Incident.resolved.is_(True),
            action != "",
            action.not_in(dd.HARDWARE_ACTIONS),
            func.jsonb_typeof(Incident.signals) == "object",
            Incident.signals != text("'{}'::jsonb"),
            func.coalesce(code_patch, "") == "",
            Incident.id.not_in(select(RegressionCase.incident_id)),
            Incident.id.not_in(select(WorkOrder.incident_id)),
        )
        .order_by(Incident.occurred_at, Incident.id)
    )
    added = 0
    for iid, fleet_id, device_id, kind, signals, resolving_action in session.execute(stmt):
        case = dd.regression_case(
            {
                "incident_id": iid,
                "kind": kind,
                "resolved": True,
                "resolving_action": resolving_action,
                "signals": signals,
            }
        )
        if case is None:  # the pure rule has the last word
            continue
        inserted = session.execute(
            pg_insert(RegressionCase)
            .values(
                incident_id=iid,
                fleet_id=fleet_id,
                kind=case["kind"],
                signals=case["signals"],
                expected_action=case["expected_action"],
            )
            .on_conflict_do_nothing(index_elements=["incident_id"])
            .returning(RegressionCase.incident_id)
        ).scalar()
        if inserted is None:
            continue
        added += 1
        events.record(
            session,
            "automation:worker",
            "automation",
            CASE_ADDED,
            {"incident": iid, "device": device_id},
            f"Resolved incident {kind} on {device_id} joined the regression corpus: "
            f"every future candidate must fire {case['expected_action']} on its signals.",
            data={"signals": sorted(case["signals"]), "expected_action": case["expected_action"]},
            ts=now,
            fleet_id=fleet_id,
        )
    return added


def regression_cases(session: Session, fleet_id: str, device_class: str) -> list[ReplayCase]:
    """Every regression case for one device class, oldest first, as `gate.replay` takes them.
    Replay them with `replay_corpus`, which treats an empty list as a pass."""
    stmt = (
        select(RegressionCase)
        .join(Incident, Incident.id == RegressionCase.incident_id)
        .join(Device, Device.id == Incident.device_id)
        .where(RegressionCase.fleet_id == fleet_id, Device.device_class == device_class)
        .order_by(Incident.occurred_at, RegressionCase.incident_id)
    )
    return [
        ReplayCase(
            case_id=row.incident_id,
            signals=row.signals,
            expected_action=row.expected_action,
            kind=row.kind,
        )
        for row in session.scalars(stmt)
    ]


def pinned_blob_shas(session: Session) -> set[str]:
    """Every blob sha256 any dataset version refers to. The disk budget (M7) must never
    delete these (telemetry §7), or that snapshot's exports start failing."""
    ids = select(func.jsonb_array_elements_text(DatasetVersion.recording_ids))
    return set(session.scalars(select(Recording.sha256).where(Recording.id.in_(ids)).distinct()))


def replay_corpus(
    candidate: Mapping[str, Any],
    cases: Iterable[gate.ReplayCase],
    *,
    baseline: Mapping[str, Any] | None = None,
) -> gate.ReplayResult:
    """Replay the regression corpus against a candidate manifest, for the gate (F8 step 4).
    Pure (no DB); it lives here because domain modules may not import each other.

    - **Empty corpus → pass** ("no regression cases yet"). `gate.replay` fails an empty
      list because a candidate must replay its own incident; the corpus has no such rule.
    - **Malformed rules → fail** with a sentence naming the problem, never an exception, so
      one bad candidate can't stop the gate for the others.
    - **Regression-only with `baseline`** (the current release's manifest): only cases the
      baseline passes count. A case the release already fails (a fix that never held, an
      expectation no rule can fire) is reported but can't block the candidate.
    """
    cases = list(cases)
    if not cases:
        return gate.ReplayResult(True, "regression corpus: no cases yet, so nothing can regress")
    if not isinstance(candidate, Mapping) or not isinstance(candidate.get("rules") or {}, Mapping):
        why = "regression corpus: candidate rules are malformed (not a map)"
        return gate.ReplayResult(False, why)
    try:
        result = gate.replay(candidate, cases)
    except Exception as exc:  # e.g. a rule whose fields have the wrong types
        return gate.ReplayResult(False, f"regression corpus: candidate rules are malformed ({exc})")
    if not result.cases:  # replay could not run a single case: malformed rules
        return gate.ReplayResult(False, f"regression corpus: {result.why}")
    counted = {c.case_id for c in cases}
    already = ""
    if baseline is not None:
        try:
            base = gate.replay(baseline, cases)
        except Exception:
            base = None
        if base is not None and base.cases:
            counted = {c.case_id for c in base.cases if c.passed}
            n_old = len(cases) - len(counted)
            already = f"; {n_old} already fail on the current release" if n_old else ""
    failed = [c for c in result.cases if not c.passed and c.case_id in counted]
    n = len(counted)
    if not failed:
        return gate.ReplayResult(
            True, f"regression corpus: replayed {n} incidents; {n} pass{already}", result.cases
        )
    detail = "; ".join(f"{c.case_id}: {c.why}" for c in failed)
    return gate.ReplayResult(
        False,
        f"regression corpus: replayed {n} incidents; {len(failed)} regress{already} ({detail})",
        result.cases,
    )
