"""Device-facing routes (APIs §3): check-in, pulls, installs, incidents, overlays.

Recordings (3.7) come in M4. Bodies are read as raw JSON first, so the verbatim payload
can be stored, and the contract version is checked before the shape: a device on an
unsupported contract gets one clear error instead of a confusing validation failure.
Retries are safe: a resent incident, install result or overlay status answers 200 and
changes nothing.
"""

from typing import Any

from fastapi import APIRouter, Body, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError

from romulus_cloud.api.deps import BlobStoreDep, SessionDep, SettingsDep
from romulus_cloud.errors import ApiError
from romulus_cloud.ids import utcnow
from romulus_cloud.services import devices, incidents, registry
from romulus_contracts import (
    SHA256_HEADER,
    SUPPORTED_VERSIONS,
    CheckIn,
    IncidentReport,
    InstallResult,
    OverlayStatus,
    is_supported,
)

router = APIRouter(prefix="/v1", tags=["device"])

RawBody = Body(...)
DUPLICATE = {"status": "already_received"}


def check_contract(raw: dict[str, Any], *, required: bool) -> None:
    """Accept the supported contract versions; refuse anything else with a clear sentence."""
    version = raw.get("contract_version")
    if version is None and not required:
        return
    if not is_supported(version):
        raise ApiError(
            400,
            "unsupported_contract_version",
            f"This cloud accepts contract_version {', '.join(SUPPORTED_VERSIONS)}; the request "
            f"sent {version!r}. Update romulus-contracts on the device or the cloud so they match.",
        )


def parse[M: BaseModel](model: type[M], raw: dict[str, Any]) -> M:
    try:
        return model.model_validate(raw)
    except ValidationError as exc:
        err = exc.errors()[0]
        where = ".".join(str(p) for p in err.get("loc", ())) or "body"
        raise ApiError(422, "invalid_request", f"{where}: {err.get('msg', 'invalid')}") from exc


@router.post("/devices/{device_id}/checkin")
def checkin(device_id: str, session: SessionDep, raw: dict[str, Any] = RawBody) -> dict:
    check_contract(raw, required=True)
    body = parse(CheckIn, raw)
    response = devices.check_in(session, device_id, body, raw, utcnow())
    session.commit()
    return response.model_dump(mode="json")


@router.get("/firmware/{version}")
def firmware(version: int, session: SessionDep) -> dict:
    """The manifest exactly as stored, and the sha256 of its canonical JSON (APIs §3.2)."""
    row = registry.get_firmware(session, version)
    return {"manifest": row.manifest, "sha256": row.sha256}


def _package(sha256: str, store, what: str) -> Response:
    """The package bytes with their sha256 in `X-Sha256`. Packages are small; read whole."""
    with registry.open_package(store, sha256, what) as stream:
        content = stream.read()
    return Response(
        content, media_type=registry.TARBALL_MEDIA_TYPE, headers={SHA256_HEADER: sha256}
    )


@router.get("/harness/{version}/package")
def harness_package(version: int, session: SessionDep, store: BlobStoreDep) -> Response:
    row = registry.get_harness(session, version)
    return _package(row.sha256, store, f"harness v{version}")


@router.get("/code/{rev}/package")
def code_package(rev: str, session: SessionDep, store: BlobStoreDep) -> Response:
    row = registry.get_code(session, rev)
    return _package(row.sha256, store, f"code {rev}")


@router.post("/devices/{device_id}/installs", status_code=204, response_model=None)
def installs(device_id: str, session: SessionDep, raw: dict[str, Any] = RawBody):
    check_contract(raw, required=False)
    body = parse(InstallResult, raw)
    created = devices.report_install(session, device_id, body, raw, utcnow())
    session.commit()
    return Response(status_code=204) if created else JSONResponse(DUPLICATE, status_code=200)


@router.post("/devices/{device_id}/incidents", status_code=201)
def report_incident(
    device_id: str, session: SessionDep, settings: SettingsDep, raw: dict[str, Any] = RawBody
):
    check_contract(raw, required=False)
    body = parse(IncidentReport, raw)
    incident, created = incidents.report(
        session, device_id, body, raw, utcnow(), safety_paths=settings.safety_paths
    )
    session.commit()
    return JSONResponse({"incident_id": incident.id}, status_code=201 if created else 200)


@router.post("/devices/{device_id}/overlays/{overlay_id}", status_code=204, response_model=None)
def overlay_status(
    device_id: str, overlay_id: str, session: SessionDep, raw: dict[str, Any] = RawBody
):
    check_contract(raw, required=False)
    body = parse(OverlayStatus, raw)
    changed = incidents.set_overlay_status(session, device_id, overlay_id, body, raw, utcnow())
    session.commit()
    return Response(status_code=204) if changed else JSONResponse(DUPLICATE, status_code=200)
