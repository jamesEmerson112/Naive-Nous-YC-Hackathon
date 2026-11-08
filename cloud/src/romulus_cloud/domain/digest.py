"""The morning digest: what the platform did in a time window, read only from events.

Pure (architecture §9 rule 3). The digest is built from the Event log alone (v0 plan §4,
D9) and is the autonomy ledger (naive-integration §3.7). Flows F10 asks for "human
actions (target: 0)"; to make that claim honestly the digest shows three things side by
side: the share of **actions** taken with no human, the human actions themselves, and
the work orders **waiting for a person**.

`summarize` accepts any objects with `actor`, `actor_class`, `kind`, `refs`, `why`, an
optional `data`, and a time: `occurred_at`, or `ts` as on the ORM `Event` row.
`EventView` is a ready-made one.

**What counts as an action** (the share's denominator). Only decisions and actions, from
the APIs §5 list (`ACTION_KINDS`): the on-device agent acting on an incident
(`incident.agent_acted`, one per incident it handled), the batch job, the gate, the
coding agent, the kill switch, work orders, dataset snapshots and exports, and the
platform locking in or dropping an overlay. Housekeeping (the disk budget's
`recording.blobs_pruned`) is not counted: no judge counts garbage collection as running
the company. Pure intake (`incident.reported`, `recording.received`,
`label.changed`, `device.*`, `install.reported`, `overlay.held`, `overlay.reverted`) and
unknown kinds don't count, **except** that any event by a human always counts as a
human action, so a person stepping in is never hidden.

**Actor classes** are compared case-insensitively. Anything other than `agent`,
`automation` or `human` is counted as `unknown`, and unknown actions are never counted as
"no human": share = (agent + automation) / actions.

**Work-order status convention (for M5; no new event kinds, APIs §5 unchanged):**
- `workorder.created` carries `refs.status` = `pending` | `blocked_over_cap` |
  `needs_human`, with the `CapDecision.why` as the event's `why`.
- `workorder.status_changed` carries `refs.from` and `refs.to` (and may repeat the new
  status as `refs.status`).
- Both carry `refs.work_order`. A status in `data` (`status` / `to`) is read too.
An order is **waiting for a person** when its latest status (from every event passed in
up to `until`, not just the window) is `blocked_over_cap`, `needs_human` or `stopped`.
So pass events from before `since` too if orders may still be waiting from earlier.

Other counted kinds: incidents `incident.reported`; heals `overlay.held` (the device's fix
held) *(inferred)*; overlays reverted; candidates created / promoted / rolled back; work
orders created / posted; kill switch `fleet.frozen`.
"""

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, tzinfo
from typing import Any

ACTOR_CLASSES = ("agent", "automation", "human")

ACTION_KINDS = frozenset(
    {
        "incident.agent_acted",
        "overlay.locked_in",
        "overlay.dropped",
        "batch.ran",
        "candidate.created",
        "candidate.replayed",
        "candidate.canary_started",
        "candidate.promoted",
        "candidate.rolled_back",
        "candidate.frozen",
        "candidate.awaiting_canary",
        "coder.started",
        "coder.drafted",
        "coder.tests_passed",
        "coder.tests_failed",
        "code.version_created",
        "fleet.frozen",
        "fleet.unfrozen",
        "workorder.created",
        "workorder.card_posted",
        "workorder.status_changed",
        "dataset.snapshotted",
        "export.built",
    }
)
"""Event kinds that count as actions (APIs §5). Human events always count too."""

WAITING_STATUSES = frozenset({"blocked_over_cap", "needs_human", "stopped"})

COUNTED_KINDS = {
    "incidents": "incident.reported",
    "heals": "overlay.held",
    "overlays_reverted": "overlay.reverted",
    "candidates_created": "candidate.created",
    "candidates_promoted": "candidate.promoted",
    "candidates_rolled_back": "candidate.rolled_back",
    "work_orders_created": "workorder.created",
    "work_orders_posted": "workorder.card_posted",
    "freezes": "fleet.frozen",
}


@dataclass(frozen=True)
class EventView:
    """A read-only event, for callers (and tests) that don't have ORM rows."""

    actor: str
    actor_class: str
    kind: str
    occurred_at: datetime
    why: str = ""
    refs: Mapping[str, Any] = field(default_factory=dict)
    data: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class HumanAction:
    occurred_at: datetime
    actor: str
    kind: str
    why: str


@dataclass(frozen=True)
class WaitingOrder:
    work_order: str
    status: str
    since: datetime
    why: str


@dataclass(frozen=True)
class Digest:
    since: datetime
    until: datetime | None
    total_events: int = 0
    actions: int = 0
    actions_by_class: Mapping[str, int] = field(default_factory=dict)
    by_kind: Mapping[str, int] = field(default_factory=dict)
    incidents: int = 0
    heals: int = 0
    overlays_reverted: int = 0
    candidates_created: int = 0
    candidates_promoted: int = 0
    candidates_rolled_back: int = 0
    work_orders_created: int = 0
    work_orders_posted: int = 0
    freezes: int = 0
    human_actions: tuple[HumanAction, ...] = ()
    waiting_for_person: tuple[WaitingOrder, ...] = ()

    @property
    def no_human_share(self) -> float | None:
        """(agent + automation) / actions, 0–1; None when there were no actions."""
        if self.actions == 0:
            return None
        c = self.actions_by_class
        return (c.get("agent", 0) + c.get("automation", 0)) / self.actions


def _when(event: Any) -> datetime:
    when = getattr(event, "occurred_at", None)
    return when if when is not None else event.ts


def _actor_class(event: Any) -> str:
    cls = str(event.actor_class or "").strip().lower()
    return cls if cls in ACTOR_CLASSES else "unknown"


def _status(event: Any) -> str | None:
    """The work-order status an event sets, from `refs.status`/`refs.to` or `data`."""
    for source in (getattr(event, "refs", None), getattr(event, "data", None)):
        if isinstance(source, Mapping):
            value = source.get("status") or source.get("to")
            if isinstance(value, str) and value.strip():
                return value.strip().lower()
    return None


def _waiting(events: list[Any], until: datetime | None) -> tuple[WaitingOrder, ...]:
    latest: dict[str, WaitingOrder] = {}
    for e in events:
        if not e.kind.startswith("workorder.") or (until is not None and _when(e) >= until):
            continue
        refs = getattr(e, "refs", None) or {}
        wo = refs.get("work_order") if isinstance(refs, Mapping) else None
        status = _status(e)
        if wo and status:
            latest[str(wo)] = WaitingOrder(str(wo), status, _when(e), e.why)
    return tuple(w for w in latest.values() if w.status in WAITING_STATUSES)


def summarize(events: Iterable[Any], since: datetime, until: datetime | None = None) -> Digest:
    """Count the events with `since <= time < until` (no upper bound when `until` is None).

    The waiting-for-a-person list uses every event passed in up to `until` (see module doc).
    Times must all be timezone-aware (or all naive); mixing them raises `TypeError`.
    """
    everything = sorted(events, key=_when)
    chosen = [e for e in everything if _when(e) >= since and (until is None or _when(e) < until)]
    actions = [e for e in chosen if e.kind in ACTION_KINDS or _actor_class(e) == "human"]
    classes = Counter({c: 0 for c in (*ACTOR_CLASSES, "unknown")})
    classes.update(_actor_class(e) for e in actions)
    kinds = Counter(e.kind for e in chosen)
    counts = {name: kinds.get(kind, 0) for name, kind in COUNTED_KINDS.items()}
    humans = tuple(
        HumanAction(_when(e), e.actor, e.kind, e.why) for e in chosen if _actor_class(e) == "human"
    )
    return Digest(
        since=since,
        until=until,
        total_events=len(chosen),
        actions=len(actions),
        actions_by_class=dict(classes),
        by_kind=dict(sorted(kinds.items())),
        human_actions=humans,
        waiting_for_person=_waiting(everything, until),
        **counts,
    )


def _fmt(when: datetime | None, tz: tzinfo | None = None) -> str:
    if when is None:
        return "now"
    return (when.astimezone(tz) if tz else when).strftime("%Y-%m-%d %H:%M %Z").strip()


def _hours(d: Digest, now: datetime | None) -> float | None:
    end = d.until or now
    return None if end is None else max((end - d.since).total_seconds(), 0) / 3600


def headline(digest: Digest, now: datetime | None = None, fleet: str | None = None) -> str:
    """One sentence for a demo slide: what happened, on which fleet (say when it is
    simulated, e.g. "4 simulated arms + 0 real"), how many humans it took, and how many
    things are still waiting for one."""
    d = digest
    hours = _hours(d, now)
    span = f"In {hours:.1f} h" if hours is not None else "Since then"
    if fleet:
        span += f" on {fleet}"
    share = d.no_human_share
    no_human = "" if share is None else f" ({share:.0%} of {d.actions} actions with no human)"
    return (
        f"{span}: {d.incidents} incidents, {d.heals} healed on the device, "
        f"{d.candidates_promoted} fleet fixes promoted, {d.candidates_rolled_back} rolled back, "
        f"{d.work_orders_created} work orders; {len(d.human_actions)} human actions, "
        f"{len(d.waiting_for_person)} waiting for a person{no_human}."
    )


def render_text(
    digest: Digest,
    tz: tzinfo | None = None,
    now: datetime | None = None,
    fleet: str | None = None,
) -> str:
    """Plain text for `make digest`, pasteable into a slide. Times shown in `tz` if given;
    `now` sizes an open-ended window in the headline; `fleet` names the fleet there."""
    d = digest
    classes = " · ".join(f"{c} {n}" for c, n in d.actions_by_class.items())
    share = d.no_human_share
    lines = [
        f"Romulus digest: {_fmt(d.since, tz)} → {_fmt(d.until, tz)}",
        headline(d, now, fleet),
        "",
        f"Events: {d.total_events}. Actions (decisions and actions, not intake): {d.actions}"
        + (f" ({classes})" if d.actions else ""),
        "Actions with no human: " + ("n/a" if share is None else f"{share:.1%}"),
        f"Human actions: {len(d.human_actions)} · "
        f"waiting for a person: {len(d.waiting_for_person)}",
        f"Incidents: {d.incidents} · heals (overlays held): {d.heals} · "
        f"overlays reverted: {d.overlays_reverted}",
        f"Candidates: {d.candidates_created} created · {d.candidates_promoted} promoted · "
        f"{d.candidates_rolled_back} rolled back",
        f"Work orders: {d.work_orders_created} created · {d.work_orders_posted} posted",
        f"Kill switch: {d.freezes} freeze(s)",
    ]
    if d.human_actions:
        lines.append("Human actions:")
        lines += [
            f"  {_fmt(h.occurred_at, tz)}  {h.actor}  {h.kind}: {h.why}" for h in d.human_actions
        ]
    if d.waiting_for_person:
        lines.append("Waiting for a person:")
        lines += [
            f"  {w.work_order}  {w.status} since {_fmt(w.since, tz)}: {w.why}"
            for w in d.waiting_for_person
        ]
    return "\n".join(lines) + "\n"
