"""The version registry: firmware manifests, harness and code packages (v0 plan §7.1).

Versions are never edited: they are created, read and (later) retired. Package bytes live
in the blob store under their sha256; the row's `path` is that sha256 (the store is
content-addressed). Every create records an Event; nothing here commits.
"""

import gzip
import hashlib
import io
import json
import tarfile
from typing import Any, BinaryIO

from pydantic import ValidationError
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from romulus_cloud import events
from romulus_cloud.adapters.blobstore import BlobNotFound, BlobStore
from romulus_cloud.errors import ApiError
from romulus_cloud.models import (
    Blob,
    Candidate,
    CodeVersion,
    Device,
    FirmwareVersion,
    HarnessVersion,
)
from romulus_contracts import HarnessMeta, firmware_response

TARBALL_MEDIA_TYPE = "application/gzip"


def _first_error(exc: ValidationError) -> str:
    err = exc.errors()[0]
    where = ".".join(str(p) for p in err.get("loc", ()))
    return f"{where}: {err.get('msg', 'invalid')}" if where else str(err.get("msg", "invalid"))


# --- firmware ------------------------------------------------------------------------------


def get_firmware(session: Session, version: int, fleet_id: str = "default") -> FirmwareVersion:
    row = session.get(FirmwareVersion, (fleet_id, version))
    if row is None:
        raise ApiError(404, "unknown_firmware", f"Firmware version {version} does not exist.")
    return row


def list_firmware(session: Session, fleet_id: str = "default") -> list[FirmwareVersion]:
    stmt = select(FirmwareVersion).where(FirmwareVersion.fleet_id == fleet_id)
    return list(session.scalars(stmt.order_by(FirmwareVersion.version)))


def create_firmware(
    session: Session,
    manifest: dict[str, Any],
    *,
    actor: str,
    actor_class: events.ActorClass,
    why: str = "",
    source: str = "manual",
    parent_version: int | None = None,
    fleet_id: str = "default",
) -> FirmwareVersion:
    """Validate the manifest, store it with the sha256 the device will check, record it."""
    try:
        served = firmware_response(manifest)
    except ValidationError as exc:
        raise ApiError(422, "invalid_manifest", f"Manifest rejected: {_first_error(exc)}") from exc
    m = served["manifest"]
    if session.get(FirmwareVersion, (fleet_id, m["version"])) is not None:
        raise ApiError(
            409,
            "version_exists",
            f"Firmware version {m['version']} already exists; "
            "versions are never edited, so create a new one.",
        )
    if session.get(CodeVersion, (fleet_id, m["code_rev"])) is None:
        raise ApiError(
            422,
            "unknown_code_rev",
            f"The manifest names code_rev {m['code_rev']!r}, which is "
            "not in the registry. Upload it with POST /v1/code first.",
        )
    row = FirmwareVersion(
        fleet_id=fleet_id,
        version=m["version"],
        device_class=m["device_class"],
        manifest=m,
        sha256=served["sha256"],
        parent_version=parent_version,
        source=source,
        why=why,
    )
    session.add(row)
    events.record(
        session,
        actor,
        actor_class,
        "firmware.version_created",
        {"firmware": row.version},
        why or f"Firmware v{row.version} created ({source}) on code {m['code_rev']}.",
        data={"code_rev": m["code_rev"], "sha256": row.sha256, "source": source},
        fleet_id=fleet_id,
    )
    return row


# --- packages (harness and code) -------------------------------------------------------------


def _store_blob(session: Session, store: BlobStore, data: bytes, fleet_id: str) -> str:
    sha = hashlib.sha256(data).hexdigest()
    size = store.put(sha, io.BytesIO(data))
    if session.get(Blob, sha) is None:
        session.add(
            Blob(
                sha256=sha,
                fleet_id=fleet_id,
                size_bytes=size,
                media_type=TARBALL_MEDIA_TYPE,
                path=sha,
            )
        )
    return sha


def read_harness_meta(data: bytes) -> HarnessMeta:
    """Parse `harness.json` at the root of a harness `.tar.gz` (APIs §2.2)."""
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
            member = next(
                (m for m in tar.getmembers() if m.name.removeprefix("./") == "harness.json"), None
            )
            if member is None or not member.isfile():
                raise ApiError(
                    422, "invalid_package", "The tarball has no harness.json at its root."
                )
            raw = json.loads(tar.extractfile(member).read())  # type: ignore[union-attr]
    except (tarfile.TarError, OSError, EOFError) as exc:
        raise ApiError(422, "invalid_package", "The upload is not a readable .tar.gz.") from exc
    except json.JSONDecodeError as exc:
        raise ApiError(422, "invalid_package", "harness.json is not valid JSON.") from exc
    try:
        return HarnessMeta.model_validate(raw)
    except ValidationError as exc:
        raise ApiError(
            422, "invalid_package", f"harness.json rejected: {_first_error(exc)}"
        ) from exc


def get_harness(session: Session, version: int, fleet_id: str = "default") -> HarnessVersion:
    row = session.get(HarnessVersion, (fleet_id, version))
    if row is None:
        raise ApiError(404, "unknown_harness", f"Harness version {version} does not exist.")
    return row


def create_harness(
    session: Session,
    store: BlobStore,
    data: bytes,
    *,
    actor: str,
    actor_class: events.ActorClass,
    why: str = "",
    source: str = "manual",
    parent_version: int | None = None,
    fleet_id: str = "default",
) -> tuple[HarnessVersion, bool]:
    """Store a harness tarball under the version in its harness.json. Returns (row, created).

    Uploading the same bytes again is a no-op; different bytes for an existing version is 409.
    """
    meta = read_harness_meta(data)
    sha = hashlib.sha256(data).hexdigest()
    existing = session.get(HarnessVersion, (fleet_id, meta.version))
    if existing is not None:
        if existing.sha256 == sha:
            return existing, False
        raise ApiError(
            409,
            "version_exists",
            f"Harness version {meta.version} already exists with "
            "different bytes; bump the version in harness.json.",
        )
    _store_blob(session, store, data, fleet_id)
    row = HarnessVersion(
        fleet_id=fleet_id,
        version=meta.version,
        path=sha,
        sha256=sha,
        meta=meta.model_dump(mode="json"),
        parent_version=parent_version,
        source=source,
        why=why,
    )
    session.add(row)
    events.record(
        session,
        actor,
        actor_class,
        "harness.version_created",
        {"harness": row.version},
        why or f"Harness v{row.version} ({meta.framework}) uploaded ({source}).",
        data={"sha256": sha, "size_bytes": len(data), "source": source},
        fleet_id=fleet_id,
    )
    return row, True


def get_code(session: Session, rev: str, fleet_id: str = "default") -> CodeVersion:
    row = session.get(CodeVersion, (fleet_id, rev))
    if row is None:
        raise ApiError(404, "unknown_code_rev", f"Code version {rev!r} does not exist.")
    return row


def create_code(
    session: Session,
    store: BlobStore,
    data: bytes,
    *,
    rev: str | None,
    actor: str,
    actor_class: events.ActorClass,
    why: str = "",
    source: str = "manual",
    parent_rev: str | None = None,
    fleet_id: str = "default",
) -> tuple[CodeVersion, bool]:
    """Store a `.tar.gz` of `device/` as code version `rev` (default: first 7 hex of its sha256)."""
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz"):
            pass
    except (tarfile.TarError, OSError, EOFError) as exc:
        raise ApiError(422, "invalid_package", "The upload is not a readable .tar.gz.") from exc
    sha = hashlib.sha256(data).hexdigest()
    rev = (rev or "").strip() or sha[:7]
    existing = session.get(CodeVersion, (fleet_id, rev))
    if existing is not None:
        if existing.sha256 == sha:
            return existing, False
        raise ApiError(
            409, "version_exists", f"Code version {rev!r} already exists with different bytes."
        )
    _store_blob(session, store, data, fleet_id)
    row = CodeVersion(
        fleet_id=fleet_id,
        rev=rev,
        path=sha,
        sha256=sha,
        size_bytes=len(data),
        parent_rev=parent_rev,
        source=source,
        why=why,
    )
    session.add(row)
    events.record(
        session,
        actor,
        actor_class,
        "code.version_created",
        {"code_rev": rev},
        why or f"Code version {rev} uploaded ({source}).",
        data={"sha256": sha, "size_bytes": len(data), "source": source},
        fleet_id=fleet_id,
    )
    return row, True


def open_package(store: BlobStore, sha256: str, what: str) -> BinaryIO:
    try:
        return store.get(sha256)
    except BlobNotFound as exc:
        raise ApiError(
            503, "package_missing", f"The bytes for {what} are missing from the blob store."
        ) from exc


def build_tarball(files: dict[str, bytes]) -> bytes:
    """A deterministic `.tar.gz` (fixed mtimes and order), so the same files hash the same."""
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as gz:
        with tarfile.open(fileobj=gz, mode="w") as tar:
            for name in sorted(files):
                info = tarfile.TarInfo(name)
                info.size = len(files[name])
                info.mode = 0o644
                tar.addfile(info, io.BytesIO(files[name]))
    return buf.getvalue()


# --- assignment ------------------------------------------------------------------------------


def _most_common(session: Session, column, device_class: str, fleet_id: str) -> int | None:
    stmt = (
        select(column)
        .where(
            Device.fleet_id == fleet_id,
            Device.device_class == device_class,
            Device.role == "normal",
            column.is_not(None),
        )
        .group_by(column)
        .order_by(func.count().desc(), column.desc())
        .limit(1)
    )
    return session.scalar(stmt)


CANDIDATE_SOURCES = ("batch", "review")  # versions made for a candidate (cluster review: §5)


def _released(model, kinds: tuple[str, ...], fleet_id: str):
    """A version that is a release, not an unproven candidate: made by the seed or by hand
    (`source` not in CANDIDATE_SOURCES), or promoted by the gate."""
    promoted = select(Candidate.version).where(
        Candidate.fleet_id == fleet_id,
        Candidate.status == "promoted",
        Candidate.type.in_(kinds),
        Candidate.version.is_not(None),
    )
    return or_(model.source.not_in(CANDIDATE_SOURCES), model.version.in_(promoted))


def default_assignment(
    session: Session, device_class: str, fleet_id: str = "default"
) -> tuple[int | None, int | None]:
    """What a new device should run: what the rest of its class runs, else the newest
    release.

    Never a candidate: candidates are only ever assigned to canaries, so the class's normal
    devices don't run them until promotion; and the fallback (a class with no normal device
    assigned anything) skips batch versions the gate hasn't promoted, or every candidate
    would look stale against itself and be rebuilt forever (PR #18 review, finding 1).
    """
    firmware = _most_common(session, Device.assigned_firmware, device_class, fleet_id)
    if firmware is None:
        firmware = session.scalar(
            select(func.max(FirmwareVersion.version)).where(
                FirmwareVersion.fleet_id == fleet_id,
                FirmwareVersion.device_class == device_class,
                FirmwareVersion.retired.is_(False),
                _released(FirmwareVersion, ("firmware", "code"), fleet_id),
            )
        )
    harness = _most_common(session, Device.assigned_harness, device_class, fleet_id)
    if harness is None:
        harness = session.scalar(
            select(func.max(HarnessVersion.version)).where(
                HarnessVersion.fleet_id == fleet_id,
                HarnessVersion.retired.is_(False),
                _released(HarnessVersion, ("harness",), fleet_id),
            )
        )
    return firmware, harness


def check_assignable(
    session: Session, firmware: int | None, harness: int | None, fleet_id: str = "default"
) -> None:
    """Raise unless each given version exists and is not retired."""
    if firmware is not None and get_firmware(session, firmware, fleet_id).retired:
        raise ApiError(409, "retired", f"Firmware v{firmware} is retired; pick another version.")
    if harness is not None and get_harness(session, harness, fleet_id).retired:
        raise ApiError(409, "retired", f"Harness v{harness} is retired; pick another version.")
