"""The ONLY way to write the Event log (D9, architecture §9 rule 4).

`record` adds the event to the caller's session and flushes it, but never commits: the
event lands in the same transaction as the change it explains, or not at all.
"""

from datetime import datetime
from typing import Any, Literal, get_args

from sqlalchemy.orm import Session

from romulus_cloud.ids import ensure_utc, new_id, utcnow
from romulus_cloud.models import Event

# Who acted, for the autonomy ledger and the overnight digest (naive-integration §3.7):
# agent = device agents, the batch LLM, Naïve sessions; automation = gate, worker;
# human = kill switch, dashboard actions, people resolving cards.
ActorClass = Literal["agent", "automation", "human"]
ACTOR_CLASSES: tuple[str, ...] = get_args(ActorClass)


def record(
    session: Session,
    actor: str,
    actor_class: ActorClass,
    kind: str,
    refs: dict[str, Any],
    why: str,
    *,
    data: dict[str, Any] | None = None,
    ts: datetime | None = None,
    fleet_id: str = "default",
) -> Event:
    """Add one event to `session`'s transaction and return it.

    `refs` names the objects involved, e.g. {"incident": "0b6c…", "device": "arm-1"};
    `why` is the plain-English sentence the timeline shows. `ts` is when it happened
    (pass the device's `occurred_at` for device events); it defaults to now.
    """
    if actor_class not in ACTOR_CLASSES:
        raise ValueError(f"actor_class must be one of {ACTOR_CLASSES}, got {actor_class!r}")
    if not actor or not kind:
        raise ValueError("actor and kind are required")
    if not why or not why.strip():
        raise ValueError("every event needs a plain-English why")
    event = Event(
        id=new_id("evt"),
        fleet_id=fleet_id,
        ts=ensure_utc(ts) if ts else utcnow(),
        actor=actor,
        actor_class=actor_class,
        kind=kind,
        refs=dict(refs),
        why=why.strip(),
        data=data,
    )
    session.add(event)
    session.flush()
    return event
