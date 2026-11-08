"""Fleet control: the kill switch (D11) and the batch "run now" request (v0 plan §7.4).

`freeze` pins every device to its last good versions and stops the gate; `unfreeze` lets
the gate move again (frozen candidates restart from replay, F5). Each press is a `human`
event, `fleet.frozen` / `fleet.unfrozen`, which is all the dashboard reads. Nothing here
commits.
"""

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy.orm import Session

from romulus_cloud import events
from romulus_cloud.models import FleetState
from romulus_cloud.services import devices


def human_actor(by: str | None) -> str:
    """`human:<name>` from a bare name; a name that already has the prefix is kept as is."""
    name = (by or "").strip() or "operator"
    return name if name.startswith("human:") else f"human:{name}"


def state(session: Session, fleet_id: str = "default") -> FleetState:
    """The fleet's control row, created on first use."""
    row = session.get(FleetState, fleet_id)
    if row is None:
        row = FleetState(fleet_id=fleet_id, frozen=False, batch_requested=False)
        session.add(row)
        session.flush()
    return row


def is_frozen(session: Session, fleet_id: str = "default") -> bool:
    row = session.get(FleetState, fleet_id)
    return bool(row and row.frozen)


@dataclass
class SwitchResult:
    changed: bool
    why: str
    pinned: list[dict] = field(default_factory=list)


def freeze(
    session: Session, by: str | None, why: str, now: datetime, fleet_id: str = "default"
) -> SwitchResult:
    """Freeze the fleet: the gate stops and every device is assigned its last good versions."""
    actor = human_actor(by)
    row = state(session, fleet_id)
    if row.frozen:
        return SwitchResult(
            False, f"The fleet was already frozen by {row.frozen_by}: {row.frozen_reason}"
        )
    pinned = []
    for d in devices.list_devices(session, fleet_id):
        firmware = d.last_good_firmware or d.assigned_firmware
        harness = d.last_good_harness or d.assigned_harness
        if (firmware, harness) != (d.assigned_firmware, d.assigned_harness):
            pinned.append(
                {
                    "device": d.id,
                    "firmware": firmware,
                    "harness": harness,
                    "was": {"firmware": d.assigned_firmware, "harness": d.assigned_harness},
                }
            )
            d.assigned_firmware, d.assigned_harness = firmware, harness
    reason = why.strip() or "no reason given"
    row.frozen, row.frozen_by, row.frozen_at, row.frozen_reason = True, actor, now, reason
    row.updated_at = now
    moved = ", ".join(
        f"{p['device']} to firmware {p['firmware']} and harness {p['harness']}" for p in pinned
    )
    text = (
        f"{actor} froze the fleet: {reason}. Rollouts stop and every device is pinned to its "
        f"last good versions ({'pinned ' + moved if pinned else 'all were already there'})."
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
    """Unfreeze the fleet: the gate moves again and frozen candidates restart from replay."""
    actor = human_actor(by)
    row = state(session, fleet_id)
    if not row.frozen:
        return SwitchResult(False, "The fleet was not frozen; nothing changed.")
    reason = why.strip() or "no reason given"
    row.frozen, row.frozen_by, row.frozen_at, row.frozen_reason = False, None, None, None
    row.updated_at = now
    text = (
        f"{actor} unfroze the fleet: {reason}. Devices stay on their pinned versions; "
        "frozen candidates restart from replay with a fresh canary window."
    )
    events.record(
        session,
        actor,
        "human",
        "fleet.unfrozen",
        {"fleet": fleet_id},
        text,
        data={"reason": reason},
        ts=now,
        fleet_id=fleet_id,
    )
    session.flush()
    return SwitchResult(True, text)


def request_batch(
    session: Session, by: str | None, now: datetime, fleet_id: str = "default"
) -> SwitchResult:
    """Ask the worker to run the batch job on its next tick ("run now")."""
    actor = human_actor(by)
    row = state(session, fleet_id)
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
