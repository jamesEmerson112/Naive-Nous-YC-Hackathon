"""HTML pages (APIs §4.5, D9, D10): the incident timeline, one incident's story, and one
heal-agent run (APIs §3.11).

Pages are built only from the event log (stories, fleet events) and the services' read
functions; nothing here decides anything. Each page polls small partials with htmx, so a
new incident or step shows within a few seconds without a reload. Jinja autoescapes every
value, so device-supplied text renders inert.

An incident page leads with what the device's heal agent did (its runs: outcome, requests
for help, the firmware it flashed, its write-up and every step), then the story's steps,
where bookkeeping steps (`story.BOOKKEEPING`) are one quiet line each.
"""

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from markupsafe import Markup
from sqlalchemy import select

from romulus_cloud import events
from romulus_cloud.api.deps import SessionDep, SettingsDep
from romulus_cloud.domain.story import (
    AGENT_OUTCOME_STATUS,
    STATUS,
    WORK_ORDER_STATUS,
    recording_name,
)
from romulus_cloud.errors import ApiError
from romulus_cloud.ids import utcnow
from romulus_cloud.models import AgentRun, Event, TechnicianCase
from romulus_cloud.services import agent_runs, devices, incidents, learning

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
STATIC_DIR = WEB_DIR / "static"
templates = Jinja2Templates(directory=WEB_DIR / "templates")

router = APIRouter(tags=["web"], include_in_schema=False)

# How many incidents the timeline shows (newest first).
TIMELINE_LIMIT = 50

# Statuses that wait on a person read in the `you` tone (sun) on the page. A presentation
# mapping only: the domain keeps "bad" for them, and its tests pin that
# (docs/impeccable-redesign/BUILD.md, "Tones").
YOU_STATUSES: frozenset[tuple[str, str]] = frozenset(
    {
        STATUS["technician.diagnosed"],
        WORK_ORDER_STATUS["needs_human"],
        WORK_ORDER_STATUS["blocked_over_cap"],
    }
)


def page_tone(status: tuple[str, str]) -> str:
    """The tone a story status shows in: `you` when it waits on a person, else its own."""
    words, tone = status
    return "you" if (words, tone) in YOU_STATUSES else tone


# A prefixed id (`wo_`, `tc_`, `ses_`, `cand_`, …) anywhere in the text, tail over 10 characters.
_ID = re.compile(r"\b([a-z]{2,5}_)([A-Za-z0-9]{6})[A-Za-z0-9]{5,}\b")


def shortid(value: Any) -> str:
    """Shorten every long prefixed id in the text: `wo_3d7cd6464e2e4980a69acd0a3e031264` ->
    `wo_3d7cd6…`, also inside a sentence ("case tc_a0177e9cb12f… opened" -> "case
    tc_a0177e… opened"). Ids with a tail of 10 characters or fewer, and text with no id,
    pass through. Put the full text in a `title` wherever this is shown."""
    if value is None:
        return ""
    out = _ID.sub(r"\1\2…", str(value))
    return Markup(out) if isinstance(value, Markup) else out


def kindword(value: Any) -> str:
    """`joint_stall` -> `Joint stall`, `technician.diagnosed` -> `Technician diagnosed`."""
    if not value:
        return ""
    text = " ".join(str(value).replace(".", " ").replace("_", " ").split())
    return text[:1].upper() + text[1:]


templates.env.filters["shortid"] = shortid
templates.env.filters["kindword"] = kindword


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


def who(actor: str) -> str:
    """`device:arm-1` → `arm-1`, `human:kent` → `kent`, `gate` → `gate`."""
    return actor.split(":", 1)[-1] or actor


def what(kind: str) -> str:
    """`incident.reported` → `incident reported`, `overlay.locked_in` → `overlay locked in`."""
    return kind.replace(".", " ").replace("_", " ")


def doctor_flags(hypothesis: dict[str, Any]) -> list[str]:
    """Honesty labels for a Fleet Doctor hypothesis: `stub` when its event says so, `fake
    model` when the model is a fake (`fake-templated`), so a demo never passes them off."""
    flags = ["stub"] if hypothesis.get("stub") else []
    if str(hypothesis.get("model") or "").lower().startswith("fake"):
        flags.append("fake model")
    return flags


def ago(seconds: float) -> str:
    """`just now`, `12 s ago`, `3 min ago`, `2 h ago`."""
    s = int(round(seconds))
    if s < 3:
        return "just now"
    if s < 60:
        return f"{s} s ago"
    if s < 3600:
        return f"{s // 60} min ago"
    return f"{s // 3600} h ago"


def duration(seconds: Any) -> str:
    """`42 s`, `3 min 52 s`, `1 h 4 min`."""
    if not isinstance(seconds, int | float) or isinstance(seconds, bool):
        return "–"
    s = int(round(seconds))
    if s < 60:
        return f"{s} s"
    if s < 3600:
        return f"{s // 60} min {s % 60} s" if s % 60 else f"{s // 60} min"
    return f"{s // 3600} h {(s % 3600) // 60} min"


def tokens(n: Any) -> str:
    """`490k`, `9.9k`, `812`."""
    if not isinstance(n, int | float) or isinstance(n, bool):
        return "–"
    if n >= 10_000:
        return f"{round(n / 1000)}k"
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(int(n))


def _parse_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value:
        return None
    try:
        at = datetime.fromisoformat(value)
    except ValueError:
        return None
    return at if at.tzinfo else at.replace(tzinfo=UTC)


PHASE_WORDS = {
    "investigate": "investigate",
    "stabilize": "stabilize",
    "repair": "repair",
    "verify": "verify",
    "escalate": "ask for help",
    "note": "note",
}
FIRMWARE_WORDS = {
    "running_at_start": "on the chip at the start",
    "flashed": "flashed",
    "rolled_back": "rolled back to",
    "flash_failed": "flash failed",
    "auto_rolled_back": "auto-rollback",
    "refused": "flash refused",
    "not_flashed": "not flashed",
    "built": "built",
    "build_failed": "build failed",
}


def _run_view(row: AgentRun, tz: ZoneInfo, now: datetime) -> dict[str, Any]:
    """One agent run, ready for the template: the stored report with local times and words."""
    r = row.report or {}
    outcome = r.get("outcome") or {}
    status = row.status if row.status in AGENT_OUTCOME_STATUS else "unknown"
    words, tone = AGENT_OUTCOME_STATUS[status]

    def local(value: Any) -> str:
        at = _parse_time(value)
        return clock(at, tz) if at else ""

    steps = []
    for st in r.get("steps") or []:
        if not isinstance(st, dict):
            continue
        steps.append(
            {
                **st,
                "clock": local(st.get("at")),
                "phase_word": PHASE_WORDS.get(str(st.get("phase")), str(st.get("phase") or "")),
            }
        )
    firmware = [
        {
            **f,
            "clock": local(f.get("at")),
            "event_word": FIRMWARE_WORDS.get(str(f.get("event")), str(f.get("event") or "")),
            "image": f.get("image") or {},
        }
        for f in r.get("firmware") or []
        if isinstance(f, dict)
    ]
    help_requests = [
        {**h, "clock": local(h.get("at"))}
        for h in r.get("help_requests") or []
        if isinstance(h, dict)
    ]
    stats = r.get("stats") or {}
    agent = r.get("agent") or {}
    writeup = r.get("writeup") or {}
    summary = [p for p in writeup.get("summary") or [] if isinstance(p, dict) and p.get("text")]
    started = _parse_time(r.get("started_at")) or row.started_at
    return {
        "device_id": row.device_id,
        "run_id": row.run_id,
        "url": f"/agent-runs/{row.device_id}/{row.run_id}",
        "dom": "run-" + "".join(c if c.isalnum() else "-" for c in row.run_id),
        "state": row.state,
        "status": status,
        "status_words": words,
        "tone": tone,
        "headline": outcome.get("headline") or words,
        "reasons": outcome.get("reasons") or [],
        "problems": outcome.get("problems") or [],
        "caveats": outcome.get("caveats") or [],
        "verified_by": {
            "bench": "checked on the bench",
            "device_check": "checked on the device after the run",
            "agent": "from the agent's own last checks",
        }.get(str(outcome.get("verified_by")), ""),
        "running": outcome.get("running") or {},
        "sim": row.sim,
        "model": agent.get("model") or "agent",
        "driver": agent.get("driver") or "",
        "label": agent.get("label") or "",
        "day": day(started, tz),
        "clock": clock(started, tz),
        "ended": local(r.get("ended_at")),
        "updated_ago": ago((now - row.received_at).total_seconds()) if row.received_at else "",
        "problem": (r.get("problem") or {}).get("text") or "",
        "summary": summary,
        "report": writeup.get("report") or "",
        "stop_text": writeup.get("stop_text") or "",
        "steps": steps,
        "firmware": firmware,
        "flashed": sum(1 for f in firmware if f.get("flashed")),
        "bad_images": sum(1 for f in firmware if f.get("bad")),
        "help_requests": help_requests,
        "stats": {
            "steps": len(steps),
            "turns": stats.get("turns"),
            "refused": stats.get("refused") or 0,
            "duration": duration(stats.get("seconds")),
            "tokens_in": tokens(stats.get("tokens_in")),
            "tokens_out": tokens(stats.get("tokens_out")),
            "flashes": stats.get("flashes"),
            "arm_runs": stats.get("arm_runs"),
        },
        "incident_ids": list(row.incident_ids or []),
    }


def _run_badge(run: dict[str, Any]) -> dict[str, Any]:
    """What a timeline row shows about an incident's latest run."""
    return {
        "status": run["status"],
        "words": run["status_words"],
        "tone": run["tone"],
        "headline": run["headline"],
        "flashed": run["flashed"],
        "bad_images": run["bad_images"],
        "help": len(run["help_requests"]),
        "steps": run["stats"]["steps"],
        "model": run["model"],
        "url": run["url"],
    }


def _device_now(session, device_id: str, now: datetime) -> dict[str, Any] | None:
    """The device as its latest check-in describes it, for "right now" panels."""
    try:
        d = devices.get_device(session, device_id)
    except ApiError:
        return None
    seen = d.last_seen_at
    stale = seen is None or (now - seen).total_seconds() > 60
    return {
        "id": d.id,
        "health": d.health or "unknown",
        "build": d.running_mcu_build or (f"fw {d.running_firmware}" if d.running_firmware else "–"),
        "seen": ago((now - seen).total_seconds()) if seen else "never",
        "stale": stale,
    }


def _fleet(session, fleet_id: str) -> dict[str, Any]:
    """Fleet strip: devices (services read) and whether the fleet is frozen (latest
    fleet.frozen / fleet.unfrozen event, so the page reads events, not state)."""
    query = events.find_query({}, kinds=["fleet.frozen", "fleet.unfrozen"])
    switches = session.scalars(query.where(Event.fleet_id == fleet_id)).all()
    last = switches[-1] if switches else None
    return {
        "devices": [devices.view(d) for d in devices.list_devices(session, fleet_id)],
        "frozen": bool(last and last.kind == "fleet.frozen"),
        "frozen_why": last.why if last and last.kind == "fleet.frozen" else None,
    }


def _turn(session, incident_id: str) -> dict[str, Any]:
    """Where a row waiting on a person sends them: its newest technician case (one indexed
    lookup on `technician_cases.incident_id`) and the part it staged, if any."""
    case = session.scalars(
        select(TechnicianCase)
        .where(TechnicianCase.incident_id == incident_id)
        .order_by(TechnicianCase.created_at.desc())
        .limit(1)
    ).first()
    if case is None:
        return {"case_url": None, "ask": None, "part_staged": False}
    part = (case.actions or {}).get("part") or {}
    staged = bool(part.get("staged") and part.get("title"))
    ask = None
    if staged:
        price = f", ${part['unit_usd']}" if part.get("unit_usd") else ""
        vendor = f" at {part['vendor_label']}" if part.get("vendor_label") else ""
        ask = f"Approve a spare part: {part['title']}{price}{vendor}"
    return {"case_url": f"/technician/{case.id}", "ask": ask, "part_staged": staged}


def _timeline(session, tz: ZoneInfo, fleet_id: str) -> list[dict[str, Any]]:
    rows = []
    now = utcnow()
    for incident in incidents.list_incidents(session, limit=TIMELINE_LIMIT, fleet_id=fleet_id):
        _, story = incidents.story(session, incident.id)
        status, tone = story.status[0], page_tone(story.status)
        runs = [_run_view(r, tz, now) for r in agent_runs.for_incident(session, incident.id)]
        rows.append(
            {
                "id": incident.id,
                "device": incident.device_id,
                "kind": incident.kind,
                "summary": incident.summary,
                "status": status,
                "tone": tone,
                "chain": story.chain,
                "links": story.links,
                "day": day(incident.occurred_at, tz),
                "clock": clock(incident.occurred_at, tz),
                "touches_safety": any(s.refs.get("touches_safety") for s in story.steps),
                "agent": _run_badge(runs[-1]) if runs else None,
                "help": sum(len(r["help_requests"]) for r in runs),
                **(_turn(session, incident.id) if tone == "you" else {}),
            }
        )
    return rows


def _overview(rows: list[dict[str, Any]], fleet: dict[str, Any]) -> dict[str, Any]:
    """The numbers across the top of the timeline, plus what the status sentence picks from:
    frozen, the first row waiting on a person (the your-turn card), the first still-broken
    and the first working row, and whether every other arm is healthy."""
    agents = [r["agent"] for r in rows if r["agent"]]
    you = [r for r in rows if r["tone"] == "you"]
    others = [r for r in rows if r["tone"] != "you"]
    bad = [r for r in others if r["tone"] == "bad" or (r["agent"] or {}).get("status") == "broken"]
    working = [
        r for r in others if r["tone"] == "wait" or (r["agent"] or {}).get("status") == "working"
    ]
    devices = fleet.get("devices") or []
    waiting_on = {r.get("device") for r in you}
    rest = [d for d in devices if d.get("id") not in waiting_on]
    return {
        "frozen": bool(fleet.get("frozen")),
        "frozen_why": fleet.get("frozen_why"),
        "turn": you[0] if you else None,
        "first_bad": bad[0] if bad else None,
        "first_working": working[0] if working else None,
        "open": sum(1 for r in rows if r["tone"] == "bad"),
        "busy": len(working),
        "checked": sum(1 for d in devices if d.get("health")),
        "rest_healthy": not bad and all(d.get("health") == "ok" for d in rest),
        "devices": len(fleet["devices"]),
        "healthy": sum(1 for d in fleet["devices"] if d.get("health") == "ok"),
        "incidents": len(rows),
        "fixed": sum(1 for a in agents if a["status"] == "fixed"),
        "broken": sum(1 for a in agents if a["status"] == "broken"),
        "working": sum(1 for a in agents if a["status"] == "working"),
        "help": sum(r["help"] for r in rows),
        "you": sum(1 for r in rows if r["tone"] == "you"),
    }


def _unlinked(session, tz: ZoneInfo, fleet_id: str) -> list[dict[str, Any]]:
    now = utcnow()
    return [_run_view(r, tz, now) for r in agent_runs.unlinked(session, fleet_id)]


def _story_view(session, incident_id: str, tz: ZoneInfo) -> dict[str, Any]:
    incident, story = incidents.story(session, incident_id)
    now = utcnow()
    steps, previous = [], None
    for s in story.steps:
        steps.append(
            {
                "day": day(s.at, tz),
                "clock": clock(s.at, tz),
                "gap": gap((s.at - previous).total_seconds()) if previous else None,
                "iso": s.at.isoformat(),
                "actor": s.actor,
                "who": who(s.actor),
                "what": what(s.kind),
                "actor_class": s.actor_class,
                "kind": s.kind,
                "line": s.line,
                "why": s.why,
                "touches_safety": bool(s.refs.get("touches_safety")),
                "recording": s.refs.get("recording"),
                "technician_case": s.refs.get("technician_case"),
                "refs": {k: v for k, v in s.refs.items() if k not in ("touches_safety",)},
                "quiet": s.bookkeeping,
                "dom": f"step-{len(steps)}",
            }
        )
        previous = s.at
    ledger = [
        {
            **h,
            "day": day(h["at"], tz),
            "clock": clock(h["at"], tz),
            "iso": h["at"].isoformat(),
            "ref": h.get(h["source"]),
            "flags": doctor_flags(h),
        }
        for h in story.ledger
    ]
    status, tone = story.status[0], page_tone(story.status)
    return {
        "incident": incidents.view(incident),
        "day": day(incident.occurred_at, tz),
        "clock": clock(incident.occurred_at, tz),
        "chain": story.chain,
        "links": story.links,
        "status": status,
        "tone": tone,
        "steps": steps,
        "touches_safety": any(st["touches_safety"] for st in steps),
        "recordings": sorted({st["recording"] for st in steps if st["recording"]}),
        "evidence": [
            {**ev, "name": recording_name(ev.get("kind")).capitalize()} for ev in story.evidence
        ],
        "ledger": ledger,
        "runs": [_run_view(r, tz, now) for r in agent_runs.for_incident(session, incident_id)],
        "device": _device_now(session, incident.device_id, now),
    }


def _page(request: Request, name: str, **context) -> HTMLResponse:
    return templates.TemplateResponse(request, name, context)


@router.get("/", response_class=HTMLResponse)
def timeline(request: Request, session: SessionDep, settings: SettingsDep):
    tz = _tz(settings)
    rows = _timeline(session, tz, settings.fleet_id)
    fleet = _fleet(session, settings.fleet_id)
    return _page(
        request,
        "timeline.html",
        incidents=rows,
        fleet=fleet,
        overview=_overview(rows, fleet),
        unlinked=_unlinked(session, tz, settings.fleet_id),
        tz=settings.display_tz,
    )


@router.get("/incidents/{incident_id}", response_class=HTMLResponse)
def incident_page(incident_id: str, request: Request, session: SessionDep, settings: SettingsDep):
    return _page(
        request,
        "incident.html",
        story=_story_view(session, incident_id, _tz(settings)),
        fleet=_fleet(session, settings.fleet_id),
        tz=settings.display_tz,
    )


@router.get("/partials/incidents", response_class=HTMLResponse)
def incidents_partial(request: Request, session: SessionDep, settings: SettingsDep):
    tz = _tz(settings)
    rows = _timeline(session, tz, settings.fleet_id)
    return _page(
        request,
        "partials/incidents.html",
        incidents=rows,
        unlinked=_unlinked(session, tz, settings.fleet_id),
    )


@router.get("/partials/overview", response_class=HTMLResponse)
def overview_partial(request: Request, session: SessionDep, settings: SettingsDep):
    rows = _timeline(session, _tz(settings), settings.fleet_id)
    fleet = _fleet(session, settings.fleet_id)
    return _page(request, "partials/overview.html", overview=_overview(rows, fleet))


@router.get("/partials/fleet", response_class=HTMLResponse)
def fleet_partial(request: Request, session: SessionDep, settings: SettingsDep):
    return _page(request, "partials/fleet.html", fleet=_fleet(session, settings.fleet_id))


@router.get("/partials/killswitch", response_class=HTMLResponse)
def killswitch_partial(request: Request, session: SessionDep, settings: SettingsDep):
    fleet = _fleet(session, settings.fleet_id)
    return _page(request, "partials/killswitch.html", fleet=fleet)


@router.get("/partials/incidents/{incident_id}", response_class=HTMLResponse)
def story_partial(incident_id: str, request: Request, session: SessionDep, settings: SettingsDep):
    return _page(
        request, "partials/story.html", story=_story_view(session, incident_id, _tz(settings))
    )


def _run_page_view(session, device_id: str, run_id: str, tz: ZoneInfo) -> dict[str, Any]:
    row = agent_runs.get(session, device_id, run_id)
    if row is None:
        raise ApiError(404, "not_found", f"No agent run {run_id!r} on {device_id!r}.")
    now = utcnow()
    return {"run": _run_view(row, tz, now), "device": _device_now(session, device_id, now)}


@router.get("/agent-runs/{device_id}/{run_id}", response_class=HTMLResponse)
def run_page(
    device_id: str, run_id: str, request: Request, session: SessionDep, settings: SettingsDep
):
    return _page(
        request,
        "agent_run.html",
        **_run_page_view(session, device_id, run_id, _tz(settings)),
        fleet=_fleet(session, settings.fleet_id),
        tz=settings.display_tz,
    )


@router.get("/partials/agent-runs/{device_id}/{run_id}", response_class=HTMLResponse)
def run_partial(
    device_id: str, run_id: str, request: Request, session: SessionDep, settings: SettingsDep
):
    view = _run_page_view(session, device_id, run_id, _tz(settings))
    return _page(request, "partials/run_page.html", **view)


def _technician_css() -> Markup:
    """The Technician page's scoped `<style>` block (.tc-*), as it stands in
    technician.html. /learning reuses .tc-head, .tc-stages and .tc-verdict, so the two
    features read as one product and a restyle of that page reaches both. Empty once the
    .tc-* rules move into romulus.css."""
    source, _, _ = templates.env.loader.get_source(templates.env, "technician.html")
    start = source.find("<style>")
    end = source.find("</style>", start)
    return Markup(source[start : end + len("</style>")] if 0 <= start < end else "")


@router.get("/learning", response_class=HTMLResponse)
def learning_page(request: Request, session: SessionDep, settings: SettingsDep):
    """Fleet learning (docs/recursive-self-improvement/demo.md §4): the newest cluster
    review, the probe's numbers and the fleet's own labelled data."""
    return _page(
        request,
        "learning.html",
        v=learning.view(session, settings, now=utcnow()),
        tc_css=_technician_css(),
        fleet=_fleet(session, settings.fleet_id),
        tz=settings.display_tz,
    )


@router.get("/partials/learning", response_class=HTMLResponse)
def learning_partial(request: Request, session: SessionDep, settings: SettingsDep):
    return _page(
        request, "partials/learning.html", v=learning.view(session, settings, now=utcnow())
    )
