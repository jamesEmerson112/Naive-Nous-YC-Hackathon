"""Operator JSON routes (APIs §4). M1: versions, devices, incidents and their stories.

No auth in v0 (bench network only). Operators may name themselves with `by`; the event's
actor is then `human:<by>`.
"""

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, File, Form, Query, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from romulus_cloud.api.deps import BlobStoreDep, SessionDep
from romulus_cloud.models import CodeVersion, FirmwareVersion, HarnessVersion
from romulus_cloud.services import devices, incidents, registry

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
