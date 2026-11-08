"""Heal-agent run reports (APIs §3.11): store the newest report per run, and put the news on
the incident's story.

A device sends a run's whole report again whenever it changes, so `report()` is an upsert.
The newest by `updated_at` wins, and a `finished` run never goes back to `running`. The
report itself is kept verbatim (`AgentRun.report`) for the pages; events record only what
changed, so a story reads as news, not as one line per upload:

- `agent.run_started`: the first report of a run;
- `agent.help_requested`: each new request for help (a person or a stronger model);
- `agent.run_finished`: the run stopped, with its outcome (fixed, broken, unknown);
- `agent.outcome_changed`: a later report changed a finished run's outcome (the bench
  checked it after the agent stopped);
- `agent.run_linked`: an incident was tied to the run after its first report.

Every event carries `refs.agent_run` and `refs.device`, and `refs.incident` (plus
`refs.incidents` when there are several) once the run is tied to an incident. The device
names the incidents; when it can't, it sends a time window and the incidents of that device
that occurred in it are taken. An incident that arrives later is tied by `link_incident`.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from romulus_cloud import events
from romulus_cloud.models import AgentRun, Incident
from romulus_cloud.services import devices
from romulus_contracts import AgentRunReport

OUTCOMES = ("working", "fixed", "broken", "unknown")
OUTCOME_WORDS = {
    "working": "is still working",
    "fixed": "left it fixed",
    "broken": "left it still broken",
    "unknown": "couldn't say whether it works",
}
UNLINKED_LIMIT = 20


def _status(body: AgentRunReport) -> str:
    status = str((body.outcome or {}).get("status") or "")
    if body.state == "running":
        return "working"
    return status if status in OUTCOMES and status != "working" else "unknown"


def _window_incidents(session: Session, device_id: str, window: list[datetime] | None) -> list[str]:
    if not window or len(window) != 2:
        return []
    lo, hi = sorted(window)
    rows = session.scalars(
        select(Incident.id)
        .where(Incident.device_id == device_id, Incident.occurred_at.between(lo, hi))
        .order_by(Incident.occurred_at)
    )
    return list(rows)


def _refs(device_id: str, run_id: str, ids: list[str]) -> dict[str, Any]:
    refs: dict[str, Any] = {"device": device_id, "agent_run": run_id}
    if ids:
        refs["incident"] = ids[0]
    if len(ids) > 1:
        refs["incidents"] = ids
    return refs


def _model(body: AgentRunReport) -> str:
    return str((body.agent or {}).get("model") or "the agent")


def _problem(body: AgentRunReport) -> str:
    kinds = [
        str(i.get("kind")) for i in (body.problem or {}).get("incidents") or [] if i.get("kind")
    ]
    return ", ".join(dict.fromkeys(kinds)) or "the device"


def report(
    session: Session, device_id: str, body: AgentRunReport, raw: dict[str, Any], now: datetime
) -> tuple[AgentRun, bool, bool]:
    """Store one run report. Returns (row, created, stored); `stored` is False when the
    report was older than the one held (or would take a finished run back to running)."""
    device = devices.get_device(session, device_id)
    row = session.get(AgentRun, (device_id, body.run_id))
    created = row is None
    if row is not None:
        older = body.updated_at and row.updated_at and body.updated_at < row.updated_at
        if older or (row.state == "finished" and body.state == "running"):
            return row, False, False

    ids = list(dict.fromkeys(str(i) for i in body.incident_ids))
    if not ids:
        ids = _window_incidents(session, device_id, body.incident_window)
    if row is not None:
        ids = list(dict.fromkeys([*row.incident_ids, *ids]))  # links are never dropped
    status = _status(body)
    before = (
        None
        if created
        else {
            "state": row.state,
            "status": row.status,
            "help": len((row.report or {}).get("help_requests") or []),
            "ids": list(row.incident_ids or []),
        }
    )
    if row is None:
        row = AgentRun(device_id=device_id, run_id=body.run_id, fleet_id=device.fleet_id)
        session.add(row)
    row.state = body.state
    row.status = status
    row.sim = body.sim
    row.incident_ids = ids
    row.started_at = body.started_at
    row.ended_at = body.ended_at
    row.updated_at = body.updated_at or now
    row.received_at = now
    row.report = raw
    session.flush()

    refs = _refs(device_id, body.run_id, ids)
    actor = f"device:{device_id}"
    fleet = device.fleet_id
    outcome = body.outcome or {}
    if created:
        events.record(
            session,
            actor,
            "agent",
            "agent.run_started",
            refs,
            f"The heal agent on {device_id} ({_model(body)}) started working on {_problem(body)}"
            + (" (a simulated rehearsal)." if body.sim else "."),
            data={"model": _model(body), "sim": body.sim},
            ts=body.started_at,
            fleet_id=fleet,
        )
    elif before and ids and not before["ids"]:
        events.record(
            session,
            "romulus",
            "automation",
            "agent.run_linked",
            refs,
            f"Agent run {body.run_id} on {device_id} was tied to this incident.",
            ts=body.started_at,
            fleet_id=fleet,
        )

    asked = body.help_requests[before["help"] if before else 0 :]
    for h in asked:
        to = "a person" if h.get("to") == "person" else "a stronger model"
        harness = h.get("source") == "harness"
        summary = str(h.get("summary") or "").strip() or "no details given"
        at = _when(h.get("at")) or body.ended_at or body.updated_at or now
        events.record(
            session,
            "harness" if harness else actor,
            "automation" if harness else "agent",
            "agent.help_requested",
            refs,
            (
                f"The run needs {to}: {summary}"
                if harness
                else f"The agent asked {to} for help: {summary}"
            ),
            data={"to": h.get("to"), "source": h.get("source"), "summary": summary[:500]},
            ts=at,
            fleet_id=fleet,
        )

    finished_now = body.state == "finished" and (created or before["state"] != "finished")
    data = {
        "status": status,
        "headline": outcome.get("headline"),
        "verified_by": outcome.get("verified_by"),
        "steps": len(body.steps),
        "flashes": sum(1 for f in body.firmware if f.get("flashed")),
        "bad_images": sum(1 for f in body.firmware if f.get("bad")),
        "help_requests": len(body.help_requests),
    }
    if finished_now:
        secs = (body.stats or {}).get("seconds")
        took = f" after {len(body.steps)} steps" + (f" ({round(float(secs))} s)" if secs else "")
        events.record(
            session,
            actor,
            "agent",
            "agent.run_finished",
            refs,
            f"The agent finished{took} and {OUTCOME_WORDS[status]}: "
            f"{outcome.get('headline') or 'no outcome given'}",
            data=data,
            ts=body.ended_at or body.updated_at or now,
            fleet_id=fleet,
        )
    elif body.state == "finished" and before and before["status"] != status:
        by = outcome.get("verified_by")
        events.record(
            session,
            "harness" if by == "bench" else actor,
            "automation",
            "agent.outcome_changed",
            refs,
            ("The bench checked the result: " if by == "bench" else "The outcome changed: ")
            + str(outcome.get("headline") or status),
            data=data,
            ts=body.updated_at or now,
            fleet_id=fleet,
        )
    return row, created, True


def _when(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def link_incident(session: Session, incident: Incident) -> int:
    """Tie a just-reported incident to the device's runs that are waiting for it: runs with
    no incident whose report's `incident_window` holds the incident's `occurred_at`."""
    rows = session.scalars(
        select(AgentRun).where(AgentRun.device_id == incident.device_id, AgentRun.sim.is_(False))
    )
    linked = 0
    for row in rows:
        if row.incident_ids:
            continue
        window = [_when(w) for w in (row.report or {}).get("incident_window") or []]
        if len(window) != 2 or None in window:
            continue
        lo, hi = sorted(window)
        if not lo <= incident.occurred_at <= hi:
            continue
        row.incident_ids = [incident.id]
        events.record(
            session,
            "romulus",
            "automation",
            "agent.run_linked",
            _refs(row.device_id, row.run_id, [incident.id]),
            f"Agent run {row.run_id} on {row.device_id} was tied to this incident.",
            ts=row.started_at,
            fleet_id=row.fleet_id,
        )
        linked += 1
    return linked


def for_incident(session: Session, incident_id: str) -> list[AgentRun]:
    """The runs that worked on this incident, oldest first."""
    return list(
        session.scalars(
            select(AgentRun)
            .where(AgentRun.incident_ids.contains([incident_id]))
            .order_by(AgentRun.started_at)
        )
    )


def get(session: Session, device_id: str, run_id: str) -> AgentRun | None:
    return session.get(AgentRun, (device_id, run_id))


def unlinked(session: Session, fleet_id: str, limit: int = UNLINKED_LIMIT) -> list[AgentRun]:
    """Recent runs tied to no incident (rehearsals, runs started with nothing open), newest
    first."""
    rows = session.scalars(
        select(AgentRun)
        .where(AgentRun.fleet_id == fleet_id)
        .order_by(AgentRun.started_at.desc())
        .limit(limit * 3)
    )
    return [r for r in rows if not r.incident_ids][:limit]


def view(row: AgentRun) -> dict[str, Any]:
    """A run as JSON for operators: the stored report plus where the cloud filed it."""
    return {
        **(row.report or {}),
        "device_id": row.device_id,
        "run_id": row.run_id,
        "state": row.state,
        "status": row.status,
        "incident_ids": list(row.incident_ids or []),
        "received_at": row.received_at.isoformat() if row.received_at else None,
    }
