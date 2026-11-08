"""The overnight flag in the database (build plan K7, M7).

`start` records when the overnight run began in `fleet_state.overnight_started_at`;
while it is set, `cli reset` refuses (unless FORCE=1), on top of the `OVERNIGHT_STARTED`
env flag, and the digest counts from that moment by default. Nothing here commits.
"""

from datetime import datetime

from sqlalchemy.orm import Session

from romulus_cloud import events
from romulus_cloud.models import FleetState

STARTED = "fleet.overnight_started"
STOPPED = "fleet.overnight_stopped"


def _state(session: Session, fleet_id: str) -> FleetState:
    state = session.get(FleetState, fleet_id)
    if state is None:
        state = FleetState(fleet_id=fleet_id)
        session.add(state)
        session.flush()
    return state


def started_at(session: Session, fleet_id: str = "default") -> datetime | None:
    state = session.get(FleetState, fleet_id)
    return state.overnight_started_at if state else None


def start(session: Session, now: datetime, by: str, fleet_id: str = "default") -> datetime:
    """Mark the overnight run as started (once); return when it started."""
    state = _state(session, fleet_id)
    if state.overnight_started_at is not None:
        return state.overnight_started_at
    state.overnight_started_at = now
    state.updated_at = now
    events.record(
        session,
        f"human:{by}",
        "human",
        STARTED,
        {},
        f"{by} started the overnight run; reset is refused until it is stopped, and the "
        "digest counts from here.",
        ts=now,
        fleet_id=fleet_id,
    )
    return now


def stop(session: Session, now: datetime, by: str, fleet_id: str = "default") -> bool:
    """Clear the flag; return whether it was set."""
    state = session.get(FleetState, fleet_id)
    if state is None or state.overnight_started_at is None:
        return False
    began = state.overnight_started_at
    state.overnight_started_at = None
    state.updated_at = now
    events.record(
        session,
        f"human:{by}",
        "human",
        STOPPED,
        {},
        f"{by} stopped the overnight run that began {began.isoformat()}; reset is allowed again.",
        data={"started_at": began.isoformat()},
        ts=now,
        fleet_id=fleet_id,
    )
    return True
