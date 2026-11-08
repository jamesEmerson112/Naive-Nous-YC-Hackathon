"""Operator JSON routes (APIs §4). M1: versions, devices, incidents and their stories.
M4: one recording's metadata and its bytes (no listing route yet; telemetry §10).

No auth in v0 (bench network only). Operators may name themselves with `by`; the event's
actor is then `human:<by>`.
"""

import re
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, File, Form, Query, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, StringConstraints

from romulus_cloud import events
from romulus_cloud.api.deps import BlobStoreDep, SessionDep, SettingsDep
from romulus_cloud.domain.story import STAGE
from romulus_cloud.errors import ApiError
from romulus_cloud.ids import utcnow
from romulus_cloud.models import Candidate, CodeVersion, FirmwareVersion, HarnessVersion
from romulus_cloud.services import (
    devices,
    fleet,
    gate,
    incidents,
    registry,
    telemetry,
    workorders,
)
from romulus_contracts import SHA256_HEADER

router = APIRouter(prefix="/v1", tags=["ops"])


def _actor(by: str | None) -> str:
    return f"human:{(by or '').strip() or 'operator'}"


def _firmware_view(r: FirmwareVersion) -> dict[str, Any]:
    return {
        "version": r.version,
        "device_class": r.device_class,
        "code_rev": r.manifest.get("code_rev"),
        "sha256": r.sha256,
        "source": r.source,
        "why": r.why,
        "retired": r.retired,
    }


def _harness_view(r: HarnessVersion) -> dict[str, Any]:
    return {
        "version": r.version,
        "sha256": r.sha256,
        "meta": r.meta,
        "source": r.source,
        "why": r.why,
    }


def _code_view(r: CodeVersion) -> dict[str, Any]:
    return {
        "rev": r.rev,
        "sha256": r.sha256,
        "size_bytes": r.size_bytes,
        "source": r.source,
        "why": r.why,
    }


# --- versions --------------------------------------------------------------------------------


class NewFirmware(BaseModel):
    manifest: dict[str, Any]
    why: str = ""
    by: str | None = None
    parent_version: int | None = None


@router.post("/firmware", status_code=201)
def create_firmware(body: NewFirmware, session: SessionDep) -> dict:
    row = registry.create_firmware(
        session,
        body.manifest,
        actor=_actor(body.by),
        actor_class="human",
        why=body.why,
        parent_version=body.parent_version,
    )
    session.commit()
    return _firmware_view(row)


@router.get("/firmware")
def list_firmware(session: SessionDep) -> list[dict]:
    return [_firmware_view(r) for r in registry.list_firmware(session)]


@router.post("/harness", status_code=201)
async def create_harness(
    session: SessionDep,
    store: BlobStoreDep,
    file: Annotated[UploadFile, File()],
    why: Annotated[str, Form()] = "",
    by: Annotated[str | None, Form()] = None,
):
    row, created = registry.create_harness(
        session, store, await file.read(), actor=_actor(by), actor_class="human", why=why
    )
    session.commit()
    return JSONResponse(_harness_view(row), status_code=201 if created else 200)


@router.post("/code", status_code=201)
async def create_code(
    session: SessionDep,
    store: BlobStoreDep,
    file: Annotated[UploadFile, File()],
    rev: Annotated[str | None, Form()] = None,
    why: Annotated[str, Form()] = "",
    by: Annotated[str | None, Form()] = None,
    parent_rev: Annotated[str | None, Form()] = None,
):
    row, created = registry.create_code(
        session,
        store,
        await file.read(),
        rev=rev,
        actor=_actor(by),
        actor_class="human",
        why=why,
        parent_rev=parent_rev,
    )
    session.commit()
    return JSONResponse(_code_view(row), status_code=201 if created else 200)


# --- devices ---------------------------------------------------------------------------------


class NewDevice(BaseModel):
    id: str
    kind: str = "real"
    role: str = "normal"
    device_class: str = "so101"
    by: str | None = None
    why: str = ""


class AssignedPatch(BaseModel):
    firmware: int | None = None
    harness: int | None = None


class DevicePatch(BaseModel):
    assigned: AssignedPatch | None = None
    role: str | None = None
    device_class: str | None = None
    by: str | None = None
    why: str = ""


@router.get("/devices")
def list_devices(session: SessionDep) -> list[dict]:
    return [devices.view(d) for d in devices.list_devices(session)]


@router.get("/devices/{device_id}")
def get_device(device_id: str, session: SessionDep) -> dict:
    return devices.view(devices.get_device(session, device_id))


@router.post("/devices", status_code=201)
def register_device(body: NewDevice, session: SessionDep) -> dict:
    device = devices.register(
        session,
        body.id,
        kind=body.kind,
        role=body.role,
        device_class=body.device_class,
        actor=_actor(body.by),
        actor_class="human",
        why=body.why,
    )
    session.commit()
    return devices.view(device)


@router.patch("/devices/{device_id}")
def update_device(device_id: str, body: DevicePatch, session: SessionDep) -> dict:
    assigned = body.assigned or AssignedPatch()
    device = devices.update(
        session,
        device_id,
        firmware=assigned.firmware,
        harness=assigned.harness,
        role=body.role,
        device_class=body.device_class,
        actor=_actor(body.by),
        actor_class="human",
        why=body.why,
    )
    session.commit()
    return devices.view(device)


# --- incidents -------------------------------------------------------------------------------


@router.get("/incidents")
def list_incidents(
    session: SessionDep,
    device: str | None = None,
    kind: str | None = None,
    since: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[dict]:
    rows = incidents.list_incidents(session, device=device, kind=kind, since=since, limit=limit)
    return [incidents.view(i) for i in rows]


@router.get("/incidents/{incident_id}/story")
def incident_story(incident_id: str, session: SessionDep) -> dict:
    incident, story = incidents.story(session, incident_id)
    return {**incidents.view(incident), **story.to_dict()}


# --- work orders (M5, D19b) ------------------------------------------------------------------


Named = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
"""Non-blank after stripping: D19b wants a named person and a reason."""


class Resolve(BaseModel):
    action: Literal["approve", "deny"]
    by: Named
    why: Named


@router.get("/work-orders")
def list_work_orders(
    session: SessionDep,
    settings: SettingsDep,
    status: str | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[dict]:
    rows = workorders.list_orders(session, status=status, limit=limit, fleet_id=settings.fleet_id)
    return [workorders.view(o) for o in rows]


@router.get("/work-orders/{order_id}")
def get_work_order(order_id: str, session: SessionDep) -> dict:
    order = workorders.get_order(session, order_id)
    return {**workorders.view(order), "events": workorders.history(session, order_id)}


@router.post("/work-orders/{order_id}/resolve")
def resolve_work_order(
    order_id: str, body: Resolve, session: SessionDep, settings: SettingsDep
) -> dict:
    """A person approves (re-queue, or accept a finished card's outcome) or denies (close)
    an order that waits for one (D19b). The event's actor is `human:<by>`."""
    order = workorders.resolve(
        session, order_id, body.action, body.by, body.why, utcnow(), naive_mode=settings.naive_mode
    )
    session.commit()
    return workorders.view(order)


# --- fleet (kill switch, D11) ----------------------------------------------------------------


class FleetSwitch(BaseModel):
    """`by` is a bare name (`kent`) or `human:kent`; `why` (or `reason`, APIs §4) says why."""

    by: str | None = None
    why: str = ""
    reason: str = ""


def _switch_view(session, result) -> dict:
    row = fleet.state(session)
    return {
        "frozen": row.frozen,
        "changed": result.changed,
        "why": result.why,
        "pinned": result.pinned,
    }


@router.post("/fleet/freeze")
def freeze_fleet(session: SessionDep, body: FleetSwitch | None = None) -> dict:
    body = body or FleetSwitch()
    result = fleet.freeze(session, body.by, body.why or body.reason, utcnow())
    session.commit()
    return _switch_view(session, result)


@router.post("/fleet/unfreeze")
def unfreeze_fleet(session: SessionDep, body: FleetSwitch | None = None) -> dict:
    body = body or FleetSwitch()
    result = fleet.unfreeze(session, body.by, body.why or body.reason, utcnow())
    session.commit()
    return _switch_view(session, result)


# --- batch and candidates (APIs §4.2) ---------------------------------------------------------


class BatchRun(BaseModel):
    by: str | None = None


@router.post("/batch/run")
def run_batch(session: SessionDep, body: BatchRun | None = None) -> dict:
    """Ask the worker to run the batch job on its next tick."""
    result = fleet.request_batch(session, (body or BatchRun()).by, utcnow())
    session.commit()
    return {"requested": True, "changed": result.changed, "why": result.why}


@router.get("/candidates")
def list_candidates(session: SessionDep) -> list[dict]:
    return [gate.view(c) for c in gate.list_candidates(session)]


@router.get("/candidates/{candidate_id}")
def get_candidate(candidate_id: str, session: SessionDep) -> dict:
    cand = session.get(Candidate, candidate_id)
    if cand is None:
        raise ApiError(404, "unknown_candidate", f"Candidate {candidate_id} does not exist.")
    # Oldest first; events written in one tick share a `ts`, so order those by loop stage.
    found = sorted(
        events.find(session, {"candidate": candidate_id}),
        key=lambda e: (e.ts, STAGE.get(e.kind, 50)),
    )
    return {
        **gate.view(cand),
        "events": [
            {
                "at": e.ts.isoformat(),
                "actor": e.actor,
                "actor_class": e.actor_class,
                "kind": e.kind,
                "why": e.why,
                "refs": e.refs,
            }
            for e in found
        ],
    }


# --- recordings ------------------------------------------------------------------------------


@router.get("/recordings/{recording_id}")
def get_recording(recording_id: str, session: SessionDep) -> dict:
    return telemetry.view(session, telemetry.get_recording(session, recording_id))


@router.get("/recordings/{recording_id}/blob")
def download_recording(recording_id: str, session: SessionDep, store: BlobStoreDep):
    """The recording's bytes exactly as uploaded, with their sha256 in `X-Sha256`."""
    rec = telemetry.get_recording(session, recording_id)
    stream = telemetry.open_blob(session, store, rec)

    def chunks():
        with stream:
            while chunk := stream.read(1024 * 1024):
                yield chunk

    ext = re.sub(r"[^A-Za-z0-9_.-]", "_", rec.format) or "bin"  # format is device-chosen
    filename = f"{rec.id}.{ext}"
    return StreamingResponse(
        chunks(),
        media_type="application/octet-stream",
        headers={
            SHA256_HEADER: rec.sha256,
            "Content-Length": str(store.size(rec.sha256)),
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Content-Type-Options": "nosniff",
        },
    )
