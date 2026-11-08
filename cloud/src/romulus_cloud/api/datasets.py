"""M6 routes: labels, recording search, datasets, snapshots, exports (APIs §4.3) and the Data
page (`GET /data`, APIs §4.5). Two routers, mounted in `main.py`: `router` (JSON under /v1)
and `pages` (HTML). Every write commits once, after its service has recorded the event.

The Data page's forms post plain HTML forms and redirect back (303), so it needs no script.
Jinja autoescapes every value, so device-chosen kinds and labels render inert.
"""

from datetime import datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field

from romulus_cloud.api.deps import SessionDep, SettingsDep
from romulus_cloud.api.web import _fleet, _page, _tz, clock, day
from romulus_cloud.errors import ApiError
from romulus_cloud.ids import utcnow
from romulus_cloud.services import datasets, telemetry

router = APIRouter(prefix="/v1", tags=["datasets"])
pages = APIRouter(tags=["web"], include_in_schema=False)

# How many recordings the Data page and the search route show at most.
PAGE_RECORDINGS = 100


def _actor(by: str | None) -> str:
    return f"human:{(by or '').strip() or 'operator'}"


# --- labels and search -----------------------------------------------------------------------


class LabelChange(BaseModel):
    add: list[str] = Field(default_factory=list)
    remove: list[str] = Field(default_factory=list)
    by: str | None = None
    why: str = ""


@router.post("/recordings/{recording_id}/labels")
def change_labels(recording_id: str, body: LabelChange, session: SessionDep) -> dict:
    labels = datasets.change_labels(
        session,
        recording_id,
        add=body.add,
        remove=body.remove,
        actor=_actor(body.by),
        why=body.why,
        now=utcnow(),
    )
    session.commit()
    return {"recording_id": recording_id, "labels": labels}


@router.delete("/recordings/{recording_id}/labels/{label}")
def remove_label(
    recording_id: str, label: str, session: SessionDep, by: str | None = None, why: str = ""
) -> dict:
    labels = datasets.change_labels(
        session, recording_id, add=[], remove=[label], actor=_actor(by), why=why, now=utcnow()
    )
    session.commit()
    return {"recording_id": recording_id, "labels": labels}


@router.get("/recordings")
def search_recordings(
    session: SessionDep,
    settings: SettingsDep,
    device: str | None = None,
    kind: str | None = None,
    label: Annotated[list[str] | None, Query()] = None,
    since: str | None = None,
    until: str | None = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = PAGE_RECORDINGS,
) -> list[dict]:
    f = datasets.parse_filter(
        {"device": device, "kind": kind, "labels": label, "since": since, "until": until}
    )
    rows = datasets.find_recordings(session, f, fleet_id=settings.fleet_id, limit=limit)
    return [telemetry.view(session, r) for r in rows]


# --- datasets --------------------------------------------------------------------------------


class NewDataset(BaseModel):
    name: str
    purpose: str
    filter: dict[str, Any] = Field(default_factory=dict)
    by: str | None = None


class Who(BaseModel):
    by: str | None = None


class NewExport(BaseModel):
    format: str
    redact: bool = True  # telemetry §10: traces are exported redacted unless asked otherwise
    by: str | None = None


class NewExportFor(NewExport):
    dataset_id: str
    n: int


@router.post("/datasets", status_code=201)
def create_dataset(body: NewDataset, session: SessionDep, settings: SettingsDep) -> dict:
    ds = datasets.create_dataset(
        session,
        name=body.name,
        purpose=body.purpose,
        filter_=body.filter,
        actor=_actor(body.by),
        fleet_id=settings.fleet_id,
        now=utcnow(),
    )
    session.commit()
    return datasets.dataset_view(session, ds)


@router.get("/datasets")
def list_datasets(session: SessionDep, settings: SettingsDep) -> list[dict]:
    rows = datasets.list_datasets(session, settings.fleet_id)
    return [datasets.dataset_view(session, d) for d in rows]


@router.get("/datasets/{dataset_id}")
def get_dataset(dataset_id: str, session: SessionDep) -> dict:
    return datasets.dataset_view(session, datasets.get_dataset(session, dataset_id))


@router.post("/datasets/{dataset_id}/versions", status_code=201)
def snapshot(dataset_id: str, session: SessionDep, body: Who | None = None) -> dict:
    version = datasets.snapshot(session, dataset_id, actor=_actor(body and body.by), now=utcnow())
    session.commit()
    return datasets.version_view(version)


@router.get("/datasets/{dataset_id}/versions/{n}")
def get_version(dataset_id: str, n: int, session: SessionDep) -> dict:
    return datasets.version_view(datasets.get_version(session, dataset_id, n))


def _request(session, dataset_id: str, n: int, body: NewExport) -> dict:
    exp = datasets.request_export(
        session,
        dataset_id,
        n,
        body.format,
        redact=body.redact,
        actor=_actor(body.by),
        now=utcnow(),
    )
    session.commit()
    return datasets.export_view(exp)


@router.post("/datasets/{dataset_id}/versions/{n}/exports", status_code=202)
def request_export(dataset_id: str, n: int, body: NewExport, session: SessionDep) -> dict:
    return _request(session, dataset_id, n, body)


@router.post("/exports", status_code=202)
def request_export_flat(body: NewExportFor, session: SessionDep) -> dict:
    return _request(session, body.dataset_id, body.n, body)


@router.get("/exports/{export_id}")
def get_export(export_id: str, session: SessionDep) -> dict:
    return datasets.export_view(datasets.get_export(session, export_id))


@router.get("/exports/{export_id}/file")
def download_export(export_id: str, session: SessionDep):
    exp = datasets.get_export(session, export_id)
    if exp.status != "built" or not exp.path:
        raise ApiError(
            409, "export_not_built", f"Export {exp.id} is {exp.status}; download it once built."
        )
    if not Path(exp.path).is_file():
        raise ApiError(503, "export_missing", f"Export {exp.id}'s file is missing from disk.")
    return FileResponse(
        exp.path,
        media_type="application/x-tar",
        filename=f"{exp.format}_{exp.dataset_id}_v{exp.n}.tar",
        headers={"X-Sha256": exp.sha256 or "", "X-Content-Type-Options": "nosniff"},
    )


# --- the Data page ---------------------------------------------------------------------------


def _when(at: datetime | None, tz) -> str:
    return f"{day(at, tz)} {clock(at, tz)}" if at else ""


@pages.get("/data", response_class=HTMLResponse)
def data_page(request: Request, session: SessionDep, settings: SettingsDep):
    tz = _tz(settings)
    f = datasets.parse_filter({})
    labels = datasets.labels_by_recording(session, settings.fleet_id)
    recs = datasets.find_recordings(session, f, fleet_id=settings.fleet_id, limit=PAGE_RECORDINGS)
    rows = [
        {
            "id": r.id,
            "kind": r.kind,
            "format": r.format,
            "device": r.device_id,
            "incident": r.incident_id,
            "when": _when(r.started_at or r.received_at, tz),
            "labels": labels.get(r.id, []),
        }
        for r in recs
    ]
    sets = datasets.list_datasets(session, settings.fleet_id)
    sets = [datasets.dataset_view(session, d) for d in sets]
    exports = [datasets.export_view(e) for e in datasets.list_exports(session, settings.fleet_id)]
    return _page(
        request,
        "data.html",
        recordings=rows,
        datasets=sets,
        exports=exports,
        formats=datasets.dd.FORMATS,
        fleet=_fleet(session, settings.fleet_id),
        tz=settings.display_tz,
    )


def _back() -> RedirectResponse:
    return RedirectResponse("/data", status_code=303)


@pages.post("/data/recordings/{recording_id}/labels")
def data_label(
    recording_id: str,
    session: SessionDep,
    label: Annotated[str, Form()],
    action: Annotated[str, Form()] = "add",
    by: Annotated[str, Form()] = "",
):
    add, remove = ([label], []) if action == "add" else ([], [label])
    datasets.change_labels(
        session,
        recording_id,
        add=add,
        remove=remove,
        actor=_actor(by),
        why="changed on the Data page",
        now=utcnow(),
    )
    session.commit()
    return _back()


@pages.post("/data/datasets/{dataset_id}/versions")
def data_snapshot(dataset_id: str, session: SessionDep, by: Annotated[str, Form()] = ""):
    datasets.snapshot(session, dataset_id, actor=_actor(by), now=utcnow())
    session.commit()
    return _back()


@pages.post("/data/datasets/{dataset_id}/versions/{n}/exports")
def data_export(
    dataset_id: str,
    n: int,
    session: SessionDep,
    format: Annotated[str, Form()],
    by: Annotated[str, Form()] = "",
):
    datasets.request_export(
        session, dataset_id, n, format, redact=True, actor=_actor(by), now=utcnow()
    )
    session.commit()
    return _back()
