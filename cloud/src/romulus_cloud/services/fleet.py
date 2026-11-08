"""Fleet control: the kill switch (D11) and the batch "run now" request (v0 plan §7.4).

- `freeze` stops the gate and pins every device to its last good versions, except that a
  canary running an in-flight candidate goes back to what it had before that candidate
  (`last_good` can be the unproven candidate itself). With no previous version on record,
  that canary is pinned to its class's promoted release instead (else to what the class's
  normal devices run), and the why says so. The `fleet.frozen` event keeps a snapshot:
  per device and type, what to `restore` on unfreeze (what it was assigned before the
  freeze; for such a canary, the same pin) and what freeze `pinned`.
- `unfreeze` puts every device back on its snapshot's `restore` version, so a freeze right
  after a promotion can't split the fleet for good, and a release a person backed out
  before the freeze stays backed out. **A device a person reassigned during the freeze
  keeps that choice:** a `human` `device.assigned` event since the freeze that moved a
  type off its pin means unfreeze leaves that type alone, and the why names it. The
  registry's automatic fill (`automation`) is not a person's choice. A
  device with no snapshot (registered during the freeze, or frozen before snapshots
  existed) goes to its class's promoted release. Then the gate moves again: frozen
  candidates restart from replay with a fresh window (F5).

Each press is a `human` event, `fleet.frozen` / `fleet.unfrozen`, which is all the
dashboard reads. Both lock the `fleet_state` row (`SELECT … FOR UPDATE`), as the gate tick
does, so a freeze never lands in the middle of a tick. Nothing here commits.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from romulus_cloud import events
from romulus_cloud.models import Candidate, Device, Event, FleetState
from romulus_cloud.services import devices, registry

LIVE = ("replaying", "awaiting_canary", "canary", "frozen")
KINDS = ("firmware", "harness")


def human_actor(by: str | None) -> str:
    """`human:<name>` from a bare name; a name that already has the prefix is kept as is."""
    name = (by or "").strip() or "operator"
    return name if name.startswith("human:") else f"human:{name}"


def state(session: Session, fleet_id: str = "default", *, lock: bool = False) -> FleetState:
    """The fleet's control row, created on first use; `lock` takes `SELECT … FOR UPDATE`."""
    row = session.get(FleetState, fleet_id, with_for_update=lock or None)
    if row is None:
        session.execute(
            pg_insert(FleetState)
            .values(fleet_id=fleet_id, frozen=False, batch_requested=False)
            .on_conflict_do_nothing(index_elements=[FleetState.fleet_id])
        )
        row = session.get(FleetState, fleet_id, with_for_update=lock or None)
        assert row is not None
    return row


def is_frozen(session: Session, fleet_id: str = "default") -> bool:
    row = session.get(FleetState, fleet_id)
    return bool(row and row.frozen)


@dataclass
class SwitchResult:
    changed: bool
    why: str
    pinned: list[dict] = field(default_factory=list)


def install_type(c: Candidate) -> str:
    """What a device installs for a candidate: a code candidate arrives as firmware."""
    return "harness" if c.type == "harness" else "firmware"


def _fallback(session: Session, c: Candidate, fleet_id: str) -> tuple[int | None, str]:
    """A version for a canary whose candidate has no `canary_prev_version`: the class's
    promoted release, else what its normal devices run. Never the candidate itself."""
    kind = install_type(c)
    version = releases(session, fleet_id).get((c.device_class, kind))
    if version is not None and version != c.version:
        return version, f"the {c.device_class} class's promoted release"
    firmware, harness = registry.default_assignment(session, c.device_class, fleet_id)
    version = harness if kind == "harness" else firmware
    if version is not None and version != c.version:
        return version, f"what the {c.device_class} class's devices run (nothing promoted yet)"
    return None, ""


def _canary_pins(
    session: Session, fleet_id: str
) -> tuple[dict[tuple[str, str], int | None], list[str]]:
    """{(device, type): version} for canaries running an in-flight candidate, and a note
    for each canary pinned to a fallback because it had no previous version on record."""
    pins: dict[tuple[str, str], int | None] = {}
    notes: list[str] = []
    live = session.scalars(
        select(Candidate).where(
            Candidate.fleet_id == fleet_id,
            Candidate.status.in_(LIVE),
            Candidate.canary_device.is_not(None),
        )
    )
    for c in live:
        device = session.get(Device, c.canary_device)
        kind = install_type(c)
        if device is None or getattr(device, f"assigned_{kind}") != c.version:
            continue
        if c.canary_prev_version is not None:
            pins[(device.id, kind)] = c.canary_prev_version
            continue
        version, source = _fallback(session, c, fleet_id)
        pins[(device.id, kind)] = version  # None: nothing else exists; left as is
        what = f"{c.type} v{c.version}"
        notes.append(
            f"{device.id} had no {kind} version on record from before {what}, so it is "
            f"pinned to {kind} {version}, {source}"
            if version is not None
            else f"{device.id} has no {kind} version other than {what} to go back to; left as is"
        )
    return pins, notes


def releases(session: Session, fleet_id: str = "default") -> dict[tuple[str, str], int]:
    """{(device_class, type): version}: each class's latest promoted release per type."""
    out: dict[tuple[str, str], int] = {}
    promoted = session.scalars(
        select(Candidate)
        .where(Candidate.fleet_id == fleet_id, Candidate.status == "promoted")
        .order_by(Candidate.updated_at, Candidate.created_at)
    )
    for c in promoted:
        if c.version is not None:
            out[(c.device_class, install_type(c))] = c.version
    return out


def _reassign(
    session: Session, fleet_id: str, target: Callable[[Device, str], int | None]
) -> list[dict]:
    """Set each device's assignment to `target(device, kind)`; return what moved."""
    moved = []
    for d in devices.list_devices(session, fleet_id):
        was = {k: getattr(d, f"assigned_{k}") for k in KINDS}
        new = {k: target(d, k) or was[k] for k in KINDS}
        if new != was:
            for k in KINDS:
                setattr(d, f"assigned_{k}", new[k])
            moved.append({"device": d.id, **new, "was": was})
    return moved


def _moved(moved: list[dict]) -> str:
    return ", ".join(
        f"{m['device']} to firmware {m['firmware']} and harness {m['harness']}" for m in moved
    )


def _snapshot(session: Session, fleet_id: str, canary: dict) -> dict[str, dict[str, Any]]:
    """{device: {type: {"restore": v, "pinned": v}}} before freeze moves anything."""
    snap: dict[str, dict[str, Any]] = {}
    for d in devices.list_devices(session, fleet_id):
        snap[d.id] = {}
        for k in KINDS:
            was = getattr(d, f"assigned_{k}")
            if (d.id, k) in canary:  # never restore an in-flight candidate
                pinned = canary[(d.id, k)] or was
                snap[d.id][k] = {"restore": pinned, "pinned": pinned}
            else:
                snap[d.id][k] = {"restore": was, "pinned": getattr(d, f"last_good_{k}") or was}
    return snap


def _last_freeze(session: Session, fleet_id: str) -> Event | None:
    """The latest `fleet.frozen` event (it keeps the snapshot)."""
    return session.scalars(
        events.find_query({"fleet": fleet_id}, kinds=["fleet.frozen"])
        .where(Event.fleet_id == fleet_id)
        .order_by(None)
        .order_by(Event.ts.desc(), Event.recorded_at.desc())
        .limit(1)
    ).first()


def _changed_by_people(
    session: Session, frozen: Event, snap: dict[str, dict[str, Any]]
) -> set[tuple[str, str]]:
    """(device, type) pairs a person reassigned since the freeze: a `human`
    `device.assigned` event (PATCH /devices) that moved that type off what it had, starting
    from the pin. The registry's automatic fill is `automation`, so it never counts."""
    rows = session.scalars(
        select(Event)
        .where(
            Event.fleet_id == frozen.fleet_id,
            Event.kind == "device.assigned",
            Event.actor_class == "human",
            Event.recorded_at > frozen.recorded_at,
        )
        .order_by(Event.recorded_at, Event.id)
    )
    last: dict[tuple[str, str], Any] = {}
    changed: set[tuple[str, str]] = set()
    for ev in rows:
        device, data = (ev.refs or {}).get("device"), ev.data or {}
        for k in KINDS:
            if k not in data:
                continue
            before = last.get((device, k), ((snap.get(device) or {}).get(k) or {}).get("pinned"))
            if data[k] != before:
                changed.add((device, k))
            last[(device, k)] = data[k]
    return changed


def freeze(
    session: Session, by: str | None, why: str, now: datetime, fleet_id: str = "default"
) -> SwitchResult:
    """Freeze the fleet: the gate stops and devices are pinned (module docstring)."""
    actor = human_actor(by)
    row = state(session, fleet_id, lock=True)
    if row.frozen:
        return SwitchResult(
            False, f"The fleet was already frozen by {row.frozen_by}: {row.frozen_reason}"
        )
    canary, notes = _canary_pins(session, fleet_id)
    snap = _snapshot(session, fleet_id, canary)
    pinned = _reassign(session, fleet_id, lambda d, k: snap[d.id][k]["pinned"])
    reason = why.strip() or "no reason given"
    row.frozen, row.frozen_by, row.frozen_at, row.frozen_reason = True, actor, now, reason
    row.updated_at = now
    text = (
        f"{actor} froze the fleet: {reason}. Rollouts stop and every device is pinned to its "
        f"last good versions, a canary to what it ran before its candidate "
        f"({'pinned ' + _moved(pinned) if pinned else 'all were already there'})."
    )
    if notes:
        text += " " + "; ".join(notes) + "."  # device ids stay lowercase
    events.record(
        session,
        actor,
        "human",
        "fleet.frozen",
        {"fleet": fleet_id},
        text,
        data={"reason": reason, "pinned": pinned, "snapshot": snap},
        ts=now,
        fleet_id=fleet_id,
    )
    session.flush()
    return SwitchResult(True, text, pinned)


def unfreeze(
    session: Session, by: str | None, why: str, now: datetime, fleet_id: str = "default"
) -> SwitchResult:
    """Unfreeze: devices go back to their pre-freeze assignments, except ones a person
    reassigned while frozen; frozen candidates restart (module docstring)."""
    actor = human_actor(by)
    row = state(session, fleet_id, lock=True)
    if not row.frozen:
        return SwitchResult(False, "The fleet was not frozen; nothing changed.")
    reason = why.strip() or "no reason given"
    row.frozen, row.frozen_by, row.frozen_at, row.frozen_reason = False, None, None, None
    row.updated_at = now
    frozen = _last_freeze(session, fleet_id)
    snap = ((frozen.data or {}).get("snapshot") if frozen else None) or {}
    by_people = _changed_by_people(session, frozen, snap) if frozen and snap else set()
    release = releases(session, fleet_id)
    kept: list[str] = []

    def target(d: Device, k: str) -> int | None:
        entry = (snap.get(d.id) or {}).get(k)
        if entry is None:
            return release.get((d.device_class, k))
        if (d.id, k) in by_people:
            current = getattr(d, f"assigned_{k}")
            kept.append(f"{d.id} on {k} {current}")
            return current
        return entry["restore"]

    moved = _reassign(session, fleet_id, target)
    where = "their pre-freeze assignments" if snap else "the promoted release"
    text = (
        f"{actor} unfroze the fleet: {reason}. "
        + (f"Back on {where}: {_moved(moved)}. " if moved else "")
        + (
            f"A person reassigned these while frozen, so they stay: {', '.join(kept)}. "
            if kept
            else ""
        )
        + "Frozen candidates restart from replay with a fresh canary window."
    )
    events.record(
        session,
        actor,
        "human",
        "fleet.unfrozen",
        {"fleet": fleet_id},
        text,
        data={"reason": reason, "reassigned": moved, "kept": kept},
        ts=now,
        fleet_id=fleet_id,
    )
    session.flush()
    return SwitchResult(True, text, moved)


def request_batch(
    session: Session, by: str | None, now: datetime, fleet_id: str = "default"
) -> SwitchResult:
    """Ask the worker to run the batch job on its next tick ("run now")."""
    actor = human_actor(by)
    row = state(session, fleet_id, lock=True)
    if row.batch_requested:
        return SwitchResult(False, "A batch run is already requested; the worker runs it next.")
    row.batch_requested = True
    row.updated_at = now
    text = f"{actor} asked for a batch run now; the worker locks in held overlays next tick."
    events.record(
        session,
        actor,
        "human",
        "batch.requested",
        {"fleet": fleet_id},
        text,
        ts=now,
        fleet_id=fleet_id,
    )
    return SwitchResult(True, text)
