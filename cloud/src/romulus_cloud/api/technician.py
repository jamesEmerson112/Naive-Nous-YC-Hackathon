"""Naïve Technician routes (docs/naive-technician/design.md §9; APIs §4.5).

JSON under `/v1`: open a case on a work order (`?mode=live|replay|fake`), stop one, read
one. Pages: `/technician/{case_id}` and its 2 s partial render `services.technician.view`
as `v`; `/technician` redirects to the newest case. Approval is the existing
`POST /v1/work-orders/{id}/resolve`.
"""

from typing import Annotated, Literal

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel

from romulus_cloud.api.deps import SessionDep, SettingsDep
from romulus_cloud.api.web import _fleet, templates
from romulus_cloud.ids import utcnow
from romulus_cloud.services import technician

router = APIRouter(prefix="/v1", tags=["technician"])
pages = APIRouter(tags=["web"], include_in_schema=False)


class StopBody(BaseModel):
    by: str = "operator"


@router.post("/work-orders/{order_id}/technician")
def open_case(
    order_id: str,
    session: SessionDep,
    settings: SettingsDep,
    mode: Annotated[Literal["live", "replay", "fake"] | None, Query()] = None,
) -> dict:
    """Send a work order to the Naïve technician (manual). Safe to repeat: the existing case
    comes back; a failed one is replaced (e.g. by a replay)."""
    case = technician.open_case(session, order_id, utcnow(), mode=mode, settings=settings)
    session.commit()
    return {**technician.case_json(session, case), "page_url": f"/technician/{case.id}"}


@router.post("/technician/cases/{case_id}/stop")
def stop_case(
    case_id: str, session: SessionDep, settings: SettingsDep, body: StopBody | None = None
):
    by = ((body.by if body else "") or "operator").strip().removeprefix("human:") or "operator"
    case = technician.stop(session, case_id, by, utcnow(), settings=settings)
    session.commit()
    return technician.case_json(session, case)


@router.get("/technician/cases/{case_id}")
def get_case(case_id: str, session: SessionDep) -> dict:
    return technician.case_json(session, technician.get_case(session, case_id))


@pages.get("/technician")
def latest(request: Request, session: SessionDep, settings: SettingsDep):
    case = technician.latest_case(session, settings.fleet_id)
    if case is None:
        return templates.TemplateResponse(
            request,
            "technician_empty.html",
            {
                "fleet": _fleet(session, settings.fleet_id),
                "tz": settings.display_tz,
                "mode": settings.technician_mode,
            },
        )
    return RedirectResponse(f"/technician/{case.id}", status_code=302)


@pages.get("/technician/{case_id}", response_class=HTMLResponse)
def case_page(case_id: str, request: Request, session: SessionDep, settings: SettingsDep):
    case = technician.get_case(session, case_id)
    v = technician.view(session, case, settings=settings)
    return templates.TemplateResponse(
        request,
        "technician.html",
        {"v": v, "fleet": _fleet(session, settings.fleet_id), "tz": settings.display_tz},
    )


@pages.get("/technician/{case_id}/partial", response_class=HTMLResponse)
def case_partial(case_id: str, request: Request, session: SessionDep, settings: SettingsDep):
    case = technician.get_case(session, case_id)
    v = technician.view(session, case, settings=settings)
    return templates.TemplateResponse(request, "partials/technician_case.html", {"v": v})
