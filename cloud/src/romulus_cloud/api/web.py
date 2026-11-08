"""HTML pages (APIs §4.5, D9, D10): the incident timeline and one incident's story.

Pages are built only from the event log (stories, fleet events) and the services' read
functions; nothing here decides anything. Each page polls small partials with htmx, so a
new incident or step shows within a few seconds without a reload. Jinja autoescapes every
value, so device-supplied text renders inert.
"""

from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from romulus_cloud import events
from romulus_cloud.api.deps import SessionDep, SettingsDep
from romulus_cloud.services import devices, incidents

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
STATIC_DIR = WEB_DIR / "static"
templates = Jinja2Templates(directory=WEB_DIR / "templates")

router = APIRouter(tags=["web"], include_in_schema=False)

# How many incidents the timeline shows (newest first).
TIMELINE_LIMIT = 50

# Words a reader sees for the step that ends an incident's story so far, and its tone
# (good / bad / wait / info) for the badge colour. Unknown kinds fall back to the label.
STATUS: dict[str, tuple[str, str]] = {
    "incident.reported": ("open", "bad"),
    "incident.agent_acted": ("agent acted", "wait"),
    "overlay.applied": ("overlay on trial", "wait"),
    "overlay.held": ("overlay held", "good"),
    "overlay.reverted": ("overlay reverted", "bad"),
    "overlay.locked_in": ("locked in", "good"),
    "overlay.dropped": ("overlay dropped", "info"),
    "overlay.superseded": ("superseded", "info"),
    "candidate.promoted": ("in the fleet", "good"),
    "candidate.rolled_back": ("rolled back", "bad"),
    "candidate.frozen": ("frozen", "bad"),
    "candidate.awaiting_canary": ("awaiting canary", "wait"),
    "workorder.created": ("work order", "wait"),
    "workorder.card_posted": ("work order posted", "wait"),
    "workorder.status_changed": ("work order updated", "wait"),
}


def _tz(settings) -> ZoneInfo:
    return ZoneInfo(settings.display_tz)


def clock(at: datetime, tz: ZoneInfo) -> str:
    """`5:42:07 PM`."""
    return at.astimezone(tz).strftime("%-I:%M:%S %p")


def day(at: datetime, tz: ZoneInfo) -> str:
    """`Sat Nov 7`."""
    return at.astimezone(tz).strftime("%a %b %-d")


def gap(seconds: float) -> str:
    """`12 s later`, `3 min later`, `2 h 5 min later`, or `same moment`."""
    s = int(round(seconds))
    if s <= 0:
        return "same moment"
    if s < 60:
        return f"{s} s later"
    if s < 3600:
        m, rest = divmod(s, 60)
        return f"{m} min {rest} s later" if rest and m < 10 else f"{m} min later"
    h, rest = divmod(s, 3600)
    return f"{h} h {rest // 60} min later" if rest >= 60 else f"{h} h later"


def _status(story) -> tuple[str, str]:
    if not story.steps:
        return ("no events", "info")
    last = story.steps[-1]
    return STATUS.get(last.kind, (last.label, "info"))


def _fleet(session) -> dict[str, Any]:
    """Fleet strip: devices (services read) and whether the fleet is frozen (latest
    fleet.frozen / fleet.unfrozen event, so the page reads events, not state)."""
    switches = events.find(session, {}, kinds=["fleet.frozen", "fleet.unfrozen"])
    last = switches[-1] if switches else None
    return {
        "devices": [devices.view(d) for d in devices.list_devices(session)],
        "frozen": bool(last and last.kind == "fleet.frozen"),
        "frozen_why": last.why if last and last.kind == "fleet.frozen" else None,
    }


def _timeline(session, tz: ZoneInfo) -> list[dict[str, Any]]:
    rows = []
    for incident in incidents.list_incidents(session)[:TIMELINE_LIMIT]:
        _, story = incidents.story(session, incident.id)
        status, tone = _status(story)
        rows.append(
            {
                "id": incident.id,
                "device": incident.device_id,
                "kind": incident.kind,
                "summary": incident.summary,
                "status": status,
                "tone": tone,
                "chain": story.chain,
                "day": day(incident.occurred_at, tz),
                "clock": clock(incident.occurred_at, tz),
                "touches_safety": any(s.refs.get("touches_safety") for s in story.steps),
            }
        )
    return rows


def _story_view(session, incident_id: str, tz: ZoneInfo) -> dict[str, Any]:
    incident, story = incidents.story(session, incident_id)
    steps, previous = [], None
    for s in story.steps:
        steps.append(
            {
                "day": day(s.at, tz),
                "clock": clock(s.at, tz),
                "gap": gap((s.at - previous).total_seconds()) if previous else None,
                "iso": s.at.isoformat(),
                "actor": s.actor,
                "actor_class": s.actor_class,
                "kind": s.kind,
                "line": s.line,
                "why": s.why,
                "touches_safety": bool(s.refs.get("touches_safety")),
                "recording": s.refs.get("recording"),
                "refs": {k: v for k, v in s.refs.items() if k not in ("touches_safety",)},
            }
        )
        previous = s.at
    status, tone = _status(story)
    return {
        "incident": incidents.view(incident),
        "day": day(incident.occurred_at, tz),
        "clock": clock(incident.occurred_at, tz),
        "chain": story.chain,
        "status": status,
        "tone": tone,
        "steps": steps,
        "touches_safety": any(st["touches_safety"] for st in steps),
        "recordings": sorted({st["recording"] for st in steps if st["recording"]}),
    }


def _page(request: Request, name: str, **context) -> HTMLResponse:
    return templates.TemplateResponse(request, name, context)


@router.get("/", response_class=HTMLResponse)
def timeline(request: Request, session: SessionDep, settings: SettingsDep):
    tz = _tz(settings)
    return _page(
        request,
        "timeline.html",
        incidents=_timeline(session, tz),
        fleet=_fleet(session),
        tz=settings.display_tz,
    )


@router.get("/incidents/{incident_id}", response_class=HTMLResponse)
def incident_page(incident_id: str, request: Request, session: SessionDep, settings: SettingsDep):
    return _page(
        request,
        "incident.html",
        story=_story_view(session, incident_id, _tz(settings)),
        fleet=_fleet(session),
        tz=settings.display_tz,
    )


@router.get("/partials/incidents", response_class=HTMLResponse)
def incidents_partial(request: Request, session: SessionDep, settings: SettingsDep):
    return _page(request, "partials/incidents.html", incidents=_timeline(session, _tz(settings)))


@router.get("/partials/fleet", response_class=HTMLResponse)
def fleet_partial(request: Request, session: SessionDep):
    return _page(request, "partials/fleet.html", fleet=_fleet(session))


@router.get("/partials/killswitch", response_class=HTMLResponse)
def killswitch_partial(request: Request, session: SessionDep):
    return _page(request, "partials/killswitch.html", fleet=_fleet(session))


@router.get("/partials/incidents/{incident_id}", response_class=HTMLResponse)
def story_partial(incident_id: str, request: Request, session: SessionDep, settings: SettingsDep):
    return _page(
        request, "partials/story.html", story=_story_view(session, incident_id, _tz(settings))
    )
