"""Fleet control: the kill switch (D11) and the batch "run now" request (v0 plan §7.4).

- `freeze` stops the gate and pins every device to its last good versions, except that a
  canary running an in-flight candidate goes back to what it had before that candidate
  (`last_good` can be the unproven candidate itself).
- `unfreeze` puts every device back on its class's promoted release, so a freeze right
  after a promotion can't split the fleet for good, and lets the gate move again (frozen
  candidates restart from replay with a fresh window, F5).

Each press is a `human` event, `fleet.frozen` / `fleet.unfrozen`, which is all the
dashboard reads. Both lock the `fleet_state` row (`SELECT … FOR UPDATE`), as the gate tick
does, so a freeze never lands in the middle of a tick. Nothing here commits.
"""

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from romulus_cloud import events
from romulus_cloud.models import Candidate, Device, FleetState
from romulus_cloud.services import devices

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


def _canary_pins(session: Session, fleet_id: str) -> dict[tuple[str, str], int]:
    """{(device, type): version} for canaries running an in-flight candidate."""
    pins: dict[tuple[str, str], int] = {}
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
        assigned = getattr(device, f"assigned_{kind}") if device else None
        if assigned == c.version and c.canary_prev_version is not None:
            pins[(c.canary_device, kind)] = c.canary_prev_version
    return pins


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


def _reassign(session: Session, fleet_id: str, target) -> list[dict]:
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
    canary = _canary_pins(session, fleet_id)
    pinned = _reassign(
        session,
        fleet_id,
        lambda d, k: canary.get((d.id, k)) or getattr(d, f"last_good_{k}"),
    )
    reason = why.strip() or "no reason given"
    row.frozen, row.frozen_by, row.frozen_at, row.frozen_reason = True, actor, now, reason
    row.updated_at = now
    text = (
        f"{actor} froze the fleet: {reason}. Rollouts stop and every device is pinned to its "
        f"last good versions, a canary to what it ran before its candidate "
        f"({'pinned ' + _moved(pinned) if pinned else 'all were already there'})."
    )
    events.record(
        session,
        actor,
        "human",
        "fleet.frozen",
        {"fleet": fleet_id},
        text,
        data={"reason": reason, "pinned": pinned},
        ts=now,
        fleet_id=fleet_id,
    )
    session.flush()
    return SwitchResult(True, text, pinned)


def unfreeze(
    session: Session, by: str | None, why: str, now: datetime, fleet_id: str = "default"
) -> SwitchResult:
    """Unfreeze: devices go back to the promoted release; frozen candidates restart."""
    actor = human_actor(by)
    row = state(session, fleet_id, lock=True)
    if not row.frozen:
        return SwitchResult(False, "The fleet was not frozen; nothing changed.")
    reason = why.strip() or "no reason given"
    row.frozen, row.frozen_by, row.frozen_at, row.frozen_reason = False, None, None, None
    row.updated_at = now
    release = releases(session, fleet_id)
    moved = _reassign(session, fleet_id, lambda d, k: release.get((d.device_class, k)))
    text = (
        f"{actor} unfroze the fleet: {reason}. "
        + (f"Back on the promoted release: {_moved(moved)}. " if moved else "")
        + "Frozen candidates restart from replay with a fresh canary window."
    )
    events.record(
        session,
        actor,
        "human",
        "fleet.unfrozen",
        {"fleet": fleet_id},
        text,
        data={"reason": reason, "reassigned": moved},
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
