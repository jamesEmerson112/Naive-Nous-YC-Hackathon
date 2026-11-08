"""Incidents, overlays and stories (APIs §2.5–2.6, §4.2; D8, D10).

An incident report writes up to three events, all at the incident's `occurred_at`:
`incident.reported` (what broke), `incident.agent_acted` (what the agent did and why) and
`overlay.applied` (the local patch). Overlay status reports add `overlay.held` or
`overlay.reverted`. Retries are no-ops. Nothing here commits.
"""

from collections.abc import Iterable
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from romulus_cloud import events
from romulus_cloud.domain.story import Story, build_story
from romulus_cloud.errors import ApiError
from romulus_cloud.models import Incident, Overlay
from romulus_cloud.services.devices import get_device
from romulus_contracts import IncidentReport, OverlayStatus, summarize_code_patch, touches_safety

# A new version replaced the overlay (D7). Terminal.
SUPERSEDED = "superseded"
# Statuses the cloud sets (M3). A late `held` from the device never moves an overlay back.
CLOUD_STATUSES = ("locked_in", "dropped")


def view(i: Incident) -> dict[str, Any]:
    return {
        "incident_id": i.id,
        "device_id": i.device_id,
        "occurred_at": i.occurred_at.isoformat(),
        "received_at": i.received_at.isoformat(),
        "kind": i.kind,
        "summary": i.summary,
        "overlay_id": i.overlay_id,
        "resolved": i.resolved,
        "resolving_action": i.resolving_action,
        "suspected_cause": i.suspected_cause,
    }


def list_incidents(
    session: Session,
    *,
    device: str | None = None,
    kind: str | None = None,
    since: datetime | None = None,
    fleet_id: str = "default",
) -> list[Incident]:
    """Newest first by when they happened (`occurred_at`), so a late report lands in place."""
    stmt = select(Incident).where(Incident.fleet_id == fleet_id)
    if device:
        stmt = stmt.where(Incident.device_id == device)
    if kind:
        stmt = stmt.where(Incident.kind == kind)
    if since:
        stmt = stmt.where(Incident.occurred_at >= since)
    return list(session.scalars(stmt.order_by(Incident.occurred_at.desc(), Incident.id)))


def report(
    session: Session,
    device_id: str,
    body: IncidentReport,
    raw: dict[str, Any],
    now: datetime,
    *,
    safety_paths: Iterable[str],
) -> tuple[Incident, bool]:
    """Store an incident (and its overlay). Returns (incident, created); a retry is a no-op."""
    device = get_device(session, device_id)
    incident_id = str(body.incident_id)
    agent, outcome, overlay = body.agent, body.outcome, body.overlay
    # ON CONFLICT DO NOTHING: two copies of one report racing in never raise; the loser
    # waits for the winner's commit and then reads its row as a duplicate.
    inserted = (
        session.execute(
            pg_insert(Incident)
            .values(
                id=incident_id,
                fleet_id=device.fleet_id,
                device_id=device_id,
                occurred_at=body.occurred_at,
                received_at=now,
                kind=body.kind,
                summary=body.summary,
                running=body.running.model_dump(mode="json") if body.running else None,
                agent_reasoning=agent.reasoning if agent else None,
                actions=list(agent.actions) if agent else None,
                overlay_id=overlay.overlay_id if overlay else None,
                resolved=outcome.resolved if outcome else False,
                resolving_action=outcome.resolving_action if outcome else None,
                suspected_cause=outcome.suspected_cause if outcome else "unknown",
                signals=body.signals,
                recording_ids=[str(r) for r in body.recordings],
                raw=raw,
            )
            .on_conflict_do_nothing(index_elements=[Incident.id])
            .returning(Incident.id)
        ).scalar()
        is not None
    )
    incident = session.get(Incident, incident_id, populate_existing=True)
    assert incident is not None
    if not inserted:
        if incident.device_id != device_id:
            raise ApiError(
                409,
                "incident_id_conflict",
                f"Incident {incident_id} was already reported "
                f"by {incident.device_id}; incident ids must be unique UUIDs.",
            )
        return incident, False

    actor, ts, fleet = f"device:{device_id}", body.occurred_at, device.fleet_id
    refs = {"incident": incident_id, "device": device_id}
    events.record(
        session,
        actor,
        "automation",
        "incident.reported",
        refs,
        f"{device_id} detected {body.kind}: {body.summary or 'no summary given'}.",
        data={"kind": body.kind, "summary": body.summary, "tier": body.tier},
        ts=ts,
        fleet_id=fleet,
    )
    if agent and (agent.reasoning or agent.actions):
        events.record(
            session,
            actor,
            "agent",
            "incident.agent_acted",
            refs,
            agent.reasoning or "The agent gave no reasoning.",
            data={
                "actions": list(agent.actions),
                "resolved": incident.resolved,
                "resolving_action": incident.resolving_action,
                "suspected_cause": incident.suspected_cause,
                "category": outcome.category if outcome else None,
            },
            ts=ts,
            fleet_id=fleet,
        )
    if overlay:
        _apply_overlay(session, device_id, fleet, incident, body, now, safety_paths)
    return incident, True


def _apply_overlay(
    session: Session,
    device_id: str,
    fleet_id: str,
    incident: Incident,
    body: IncidentReport,
    now: datetime,
    safety_paths: Iterable[str],
) -> None:
    overlay = body.overlay
    assert overlay is not None
    row = session.get(Overlay, (device_id, overlay.overlay_id))
    if row is not None and row.incident_id is not None:
        return  # Already known from an earlier incident; the first report owns it.
    if row is None:
        row = Overlay(
            device_id=device_id, id=overlay.overlay_id, fleet_id=fleet_id, status="active"
        )
        session.add(row)
    # else: a placeholder made by a status that arrived first; fill it in, keep its status.
    running = body.running
    safety = touches_safety(overlay.code_patch, safety_paths)
    summary = summarize_code_patch(overlay.code_patch) if overlay.code_patch else None
    row.incident_id = incident.id
    row.base_firmware = running.firmware if running else None
    row.base_harness = running.harness if running else None
    row.base_code_rev = running.code_rev if running else None
    row.diff = overlay.diff
    row.code_patch = overlay.code_patch
    row.touches_safety = safety
    row.occurred_at = body.occurred_at
    row.received_at = row.received_at or now
    row.raw = {**overlay.model_dump(mode="json"), **_last_status(row)}
    session.flush()
    refs: dict[str, Any] = {
        "incident": incident.id,
        "device": device_id,
        "overlay": overlay.overlay_id,
    }
    if safety:
        refs["touches_safety"] = True
    data = {
        "rules": sorted((overlay.diff.get("rules") or {}).keys()),
        "params": sorted((overlay.diff.get("params") or {}).keys()),
        "code_paths": list(summary.paths) if summary else [],
        "code_patch_problems": list(summary.problems) if summary else [],
    }
    why = (
        f"The agent patched {device_id} locally to stop {incident.kind} without waiting for "
        "a fleet release; the overlay is watched on the device and kept or reverted."
    )
    if safety:
        why += (
            " The patch touches the safety layer or visor, so any candidate made from it"
            " goes to a simulated canary first (K17)."
        )
    events.record(
        session,
        f"device:{device_id}",
        "agent",
        "overlay.applied",
        refs,
        why,
        data=data,
        ts=body.occurred_at,
        fleet_id=fleet_id,
    )


def _last_status(row: Overlay) -> dict[str, Any]:
    """The `last_status` entry kept in an overlay's raw (the last status body the device sent)."""
    return {"last_status": row.raw["last_status"]} if row.raw and "last_status" in row.raw else {}


def set_overlay_status(
    session: Session,
    device_id: str,
    overlay_id: str,
    body: OverlayStatus,
    raw: dict[str, Any],
    now: datetime,
) -> bool:
    """Record `held`, `reverted` or `superseded` for one device's overlay. Returns whether
    anything was recorded (False: a retry, or nothing to say).

    - The same status the device last sent is a no-op (a retry).
    - `superseded` (a new healthy version replaced the overlay, D7) has its own event kind,
      so nothing counts it as a failed fix or locks it in. It is terminal.
    - A cloud status (`locked_in`, `dropped`) is sticky: a later `superseded` or `reverted`
      is recorded as an event but leaves the status alone (in F3 the origin device sends
      `superseded` when it installs the promoted version); a late `held` is ignored.
    - A status for an overlay the cloud hasn't seen yet makes a placeholder row; the
      incident fills it in when it arrives.
    """
    device = get_device(session, device_id)
    status = body.status
    overlay = session.get(Overlay, (device_id, overlay_id))
    unknown = overlay is None
    if overlay is None:
        overlay = Overlay(
            device_id=device_id, id=overlay_id, fleet_id=device.fleet_id, received_at=now
        )
        session.add(overlay)
    else:
        last_sent = _last_status(overlay).get("last_status", {}).get("status")
        sticky = overlay.status in CLOUD_STATUSES
        if (
            status in (overlay.status, last_sent)
            or overlay.status == SUPERSEDED
            or (sticky and status == "held")
        ):
            return False

    before = overlay.status
    sticky = before in CLOUD_STATUSES
    if not sticky:
        overlay.status = status
        overlay.status_reason = body.reason
    overlay.updated_at = now
    overlay.raw = {**(overlay.raw or {}), "last_status": raw}
    session.flush()

    refs: dict[str, Any] = {"device": device_id, "overlay": overlay_id}
    if overlay.incident_id:
        refs["incident"] = overlay.incident_id
    if overlay.touches_safety:
        refs["touches_safety"] = True
    reason = body.reason or "no reason given"
    if status == SUPERSEDED:
        why = (
            f"Overlay {overlay_id} on {device_id} was superseded by a new version: {reason}. "
            "Not a failed fix; nothing to lock in."
        )
    else:
        verb = "kept" if status == "held" else "reverted"
        why = f"{device_id} {verb} overlay {overlay_id}: {reason}."
    if unknown:
        why += " The cloud has not seen this overlay's incident yet."
    elif sticky:
        why += f" The overlay stays {before}: the cloud already decided its fate."
    elif before not in (None, "active"):
        why += f" It was {before} before."
    events.record(
        session,
        f"device:{device_id}",
        "automation",
        f"overlay.{status}",
        refs,
        why,
        data={"status": status, "reason": body.reason, "before": before},
        ts=body.occurred_at or now,
        fleet_id=device.fleet_id,
    )
    return True


def story(session: Session, incident_id: str) -> tuple[Incident, Story]:
    incident = session.get(Incident, incident_id)
    if incident is None:
        raise ApiError(404, "unknown_incident", f"Incident {incident_id} does not exist.")
    found = {e.id: e for e in events.find(session, {"incident": incident_id})}
    if incident.overlay_id:
        # A status that arrived before its incident carries no incident ref; include the
        # overlay's own `overlay.*` events.
        overlay_refs = {"device": incident.device_id, "overlay": incident.overlay_id}
        found.update(
            {e.id: e for e in events.find(session, overlay_refs) if e.kind.startswith("overlay.")}
        )
    ordered = sorted(found.values(), key=lambda e: (e.ts, e.recorded_at))
    return incident, build_story(ordered, incident_id)
