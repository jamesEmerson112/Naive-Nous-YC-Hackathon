"""The ONLY way to write the Event log (D9, architecture §9 rule 4).

`record` adds the event to the caller's session and flushes it, but never commits: the
event lands in the same transaction as the change it explains, or not at all.

Standard `refs` keys (values are ids as strings):
    device      "arm-1"
    incident    the device's incident UUID
    incidents   a list of incident UUIDs, on events about a candidate folded from several
                (`incident` then names the first); match with {"incidents": [id]}
    overlay     the device's overlay id; always together with `device` (ids are per device)
    candidate   "cand_…"
    work_order  "wo_…"
    recording   the device's recording UUID
    dataset     "ds_…"     export  "exp_…"
    firmware / harness  version int;  code_rev  "a1b2c3d"
    touches_safety  true, on overlay/candidate events whose diff touches safety or visor paths
    ask         "ask_…", a Fleet Doctor L1 ask (it has no table: its `doctor.asked` and
                `doctor.advised` events are the record)
    consult     "cons_…", a Fleet Doctor consult (`doctor_consults`)
    proposal    "prop_…", a Fleet Doctor proposal (`doctor_proposals`)
    agent_run   a heal-agent run id made on the device (`agent_runs`, with `device`)
    technician_case  "tc_…", a Naïve Technician case (`technician_cases`)
    naive_session    "ses_…", the Naïve session behind it
Always include `incident` when it is known, so an incident's story is one `find` query.
Query refs with `find` (or `Event.refs.contains(...)`, i.e. `@>`), which uses the GIN
index; `Event.refs["incident"].astext == ...` does not.
"""

from datetime import datetime
from typing import Any, Literal, get_args

from sqlalchemy import Select, select
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


def find_query(refs: dict[str, Any], *, kinds: list[str] | None = None) -> Select[tuple[Event]]:
    """Events whose refs contain all of `refs` (`refs @> …`, GIN-indexed), oldest first."""
    stmt = select(Event).where(Event.refs.contains(refs))
    if kinds:
        stmt = stmt.where(Event.kind.in_(kinds))
    return stmt.order_by(Event.ts, Event.recorded_at, Event.id)


def find(session: Session, refs: dict[str, Any], *, kinds: list[str] | None = None) -> list[Event]:
    """Run `find_query`, e.g. `find(session, {"incident": incident_id})` for a story."""
    return list(session.scalars(find_query(refs, kinds=kinds)))
