"""Incidents, overlays and stories (APIs §2.5–2.6, §4.2; D8, D10).

An incident report writes up to three events, all at the incident's `occurred_at`:
`incident.reported` (what broke), `incident.agent_acted` (what the agent did and why) and
`overlay.applied` (the local patch). Overlay status reports add `overlay.held` or
`overlay.reverted`. Retries are no-ops. Nothing here commits.
"""

import json
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
# `lockin_rolled_back`: its candidate failed replay or canary; `lockin_error`: locking it
# in raised. Both keep the device's local fix and are never re-batched.
CLOUD_STATUSES = ("locked_in", "dropped", "lockin_rolled_back", "lockin_error")


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
    limit: int | None = None,
    fleet_id: str = "default",
) -> list[Incident]:
    """Newest first by when they happened (`occurred_at`), so a late report lands in place.
    `limit` caps how many rows (and raw payloads) are loaded."""
    stmt = select(Incident).where(Incident.fleet_id == fleet_id)
    if device:
        stmt = stmt.where(Incident.device_id == device)
    if kind:
        stmt = stmt.where(Incident.kind == kind)
    if since:
        stmt = stmt.where(Incident.occurred_at >= since)
    stmt = stmt.order_by(Incident.occurred_at.desc(), Incident.id)
    if limit is not None:
        stmt = stmt.limit(limit)
    return list(session.scalars(stmt))


def _detected_why(body: IncidentReport) -> str:
    """What caught the fault: health tier, the last log line, the signals, the healer's outcome.

    The story's line already says what broke, so this says how it was noticed.
    """
    parts = []
    if body.tier is not None:
        parts.append(f"tier {body.tier} health check")
    if body.log_tail:
        last = body.log_tail[-1]
        parts.append(f"log: {last if isinstance(last, str) else json.dumps(last)}")
    if body.signals:
        parts.append("signals " + ", ".join(sorted(body.signals)))
    if body.outcome and body.outcome.category:
        parts.append(f"healer outcome {body.outcome.category}")
    return f"Detected by {'; '.join(parts)}." if parts else "Reported by the device's healer."


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
        _detected_why(body),
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


def requeue_marks(session: Session, overlay: Overlay) -> dict[str, Any]:
    """The marks on every `overlay.requeued` event the gate wrote for this overlay, merged:
    `solo` (bisected out of a failed fold: batch it alone), `retried` (it already had
    its one retry after an unproven failure) and `stale` (how many times its candidates
    went stale; capped by the gate). Empty if it was never re-queued."""
    refs = {"device": overlay.device_id, "overlay": overlay.id}
    marks: dict[str, Any] = {}
    for ev in session.scalars(events.find_query(refs, kinds=["overlay.requeued"])):
        marks.update((ev.data or {}).get("marks") or {})
    return marks


def _after_superseded(overlay: Overlay, last_sent: str | None) -> bool:
    """The overlay was kept `held` when its device reported `superseded`."""
    return overlay.status == "held" and last_sent == SUPERSEDED


def _ignored(overlay: Overlay) -> str | None:
    """The status of the last device report ignored after `superseded` (for retries)."""
    return ((overlay.raw or {}).get("ignored_status") or {}).get("status")


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
      so nothing counts it as a failed fix or locks it in. After it, a later `held` or
      `reverted` is recorded as an event but never moves the overlay: the device no
      longer runs it.
    - A cloud status (`locked_in`, `dropped`) is sticky: a later `superseded` or `reverted`
      is recorded as an event but leaves the status alone (in F3 the origin device sends
      `superseded` when it installs the promoted version); a late `held` is ignored.
    - **A `held` overlay survives `superseded`:** it is recorded but stays `held`. The
      only version that contains an overlay is one built from it, and then the overlay is
      `locked_in`, not `held`; so the device dropped this fix for a version without it, and
      the cloud, which keeps the diff, re-batches it on the new base (where
      `base_compatible` decides). Otherwise the fix would be lost on both sides.
    - `superseded` for an overlay still `active` (the device hadn't kept it yet) is
      terminal: an unproven fix, not a lost one.
    - A status for an overlay the cloud hasn't seen yet makes a placeholder row; the
      incident fills it in when it arrives.
    """
    device = get_device(session, device_id)
    status = body.status
    overlay = session.get(Overlay, (device_id, overlay_id))
    unknown = overlay is None
    last_sent = None
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
            or (_after_superseded(overlay, last_sent) and status == _ignored(overlay))
            or (sticky and status == "held")
        ):
            return False

    before = overlay.status
    sticky = before in CLOUD_STATUSES
    keep_held = status == SUPERSEDED and before == "held"
    # Kept held after `superseded`: a later device status is recorded but moves nothing,
    # and `last_status` stays `superseded` (the body is kept as `ignored_status`).
    ignored = not unknown and _after_superseded(overlay, last_sent)
    if not sticky and not keep_held and not ignored:
        overlay.status = status
        overlay.status_reason = body.reason
    overlay.updated_at = now
    key = "ignored_status" if ignored else "last_status"
    overlay.raw = {**(overlay.raw or {}), key: raw}
    session.flush()

    refs: dict[str, Any] = {"device": device_id, "overlay": overlay_id}
    if overlay.incident_id:
        refs["incident"] = overlay.incident_id
    if overlay.touches_safety:
        refs["touches_safety"] = True
    reason = body.reason or "no reason given"
    if ignored:
        why = (
            f"{device_id} sent {status} for overlay {overlay_id} ({reason}) after reporting "
            "it superseded. Ignored: the device no longer runs it, and the overlay stays "
            "held for the next batch."
        )
    elif keep_held:
        why = (
            f"Overlay {overlay_id} on {device_id} was superseded by a new version: {reason}. "
            "Not a failed fix. It stays held for the next batch: it was never locked in, so "
            "the version that replaced it on the device was not built from it, and the "
            "cloud keeps its diff to re-batch on the new base."
        )
    elif status == SUPERSEDED:
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
    elif before not in (None, "active") and not keep_held and not ignored:
        why += f" It was {before} before."
    events.record(
        session,
        f"device:{device_id}",
        "automation",
        f"overlay.{status}",
        refs,
        why,
        data={
            "status": status,
            "reason": body.reason,
            "before": before,
            "after": overlay.status,
        },
        ts=body.occurred_at or now,
        fleet_id=device.fleet_id,
    )
    return True


def story(session: Session, incident_id: str) -> tuple[Incident, Story]:
    incident = session.get(Incident, incident_id)
    if incident is None:
        raise ApiError(404, "unknown_incident", f"Incident {incident_id} does not exist.")
    found = {e.id: e for e in events.find(session, {"incident": incident_id})}
    # A candidate folded from several incidents lists them all under `incidents` (M3).
    found.update({e.id: e for e in events.find(session, {"incidents": [incident_id]})})
    if incident.overlay_id:
        # A status that arrived before its incident carries no incident ref; include the
        # overlay's own `overlay.*` events.
        overlay_refs = {"device": incident.device_id, "overlay": incident.overlay_id}
        found.update(
            {e.id: e for e in events.find(session, overlay_refs) if e.kind.startswith("overlay.")}
        )
    ordered = sorted(found.values(), key=lambda e: (e.ts, e.recorded_at))
    return incident, build_story(ordered, incident_id)
