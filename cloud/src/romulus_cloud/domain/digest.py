"""The morning digest: what the platform did in a time window, read only from events.

Pure (architecture §9 rule 3). The digest is built from the Event log alone (v0 plan §4,
D9) and is the autonomy ledger (naive-integration §3.7): every event's `actor_class` is
`agent`, `automation` or `human`, and the headline is the share of actions with no human
(flows F10: "human actions (target: 0)").

`summarize` accepts any objects with `actor`, `actor_class`, `kind`, `refs`, `why` and a
time: `occurred_at`, or `ts` as on the ORM `Event` row. `EventView` is a ready-made one.

Counted kinds (APIs §5):
- incidents: `incident.reported`
- heals: `overlay.held` (the device's fix held) *(inferred: a heal is a held overlay)*
- overlays reverted: `overlay.reverted`
- candidates: `candidate.created`, `candidate.promoted`, `candidate.rolled_back`
- work orders: `workorder.created`, `workorder.card_posted`
- kill switch: `fleet.frozen`
Unknown kinds are still counted in `total`, `by_actor_class` and `by_kind`.
"""

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

ACTOR_CLASSES = ("agent", "automation", "human")

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


@dataclass(frozen=True)
class HumanAction:
    occurred_at: datetime
    actor: str
    kind: str
    why: str


@dataclass(frozen=True)
class Digest:
    since: datetime
    until: datetime | None
    total: int = 0
    by_actor_class: Mapping[str, int] = field(default_factory=dict)
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

    @property
    def no_human_share(self) -> float | None:
        """Share of actions taken by an agent or automation (0–1); None with no events."""
        if self.total == 0:
            return None
        return (self.total - self.by_actor_class.get("human", 0)) / self.total


def _when(event: Any) -> datetime:
    when = getattr(event, "occurred_at", None)
    return when if when is not None else event.ts


def summarize(events: Iterable[Any], since: datetime, until: datetime | None = None) -> Digest:
    """Count the events with `since <= time < until` (no upper bound when `until` is None).

    Times must all be timezone-aware (or all naive); mixing them raises `TypeError`.
    """
    chosen = sorted(
        (e for e in events if _when(e) >= since and (until is None or _when(e) < until)),
        key=_when,
    )
    classes = Counter({c: 0 for c in ACTOR_CLASSES})
    classes.update(e.actor_class for e in chosen)
    kinds = Counter(e.kind for e in chosen)
    counts = {name: kinds.get(kind, 0) for name, kind in COUNTED_KINDS.items()}
    humans = tuple(
        HumanAction(_when(e), e.actor, e.kind, e.why) for e in chosen if e.actor_class == "human"
    )
    return Digest(
        since=since,
        until=until,
        total=len(chosen),
        by_actor_class=dict(classes),
        by_kind=dict(sorted(kinds.items())),
        human_actions=humans,
        **counts,
    )


def _fmt(when: datetime | None) -> str:
    return when.strftime("%Y-%m-%d %H:%M %Z").strip() if when else "now"


def render_text(digest: Digest) -> str:
    """Plain text for `make digest`."""
    d = digest
    classes = " · ".join(f"{c} {n}" for c, n in d.by_actor_class.items())
    share = d.no_human_share
    lines = [
        f"Romulus digest: {_fmt(d.since)} → {_fmt(d.until)}",
        f"Actions: {d.total} ({classes})" if d.total else "Actions: 0 (no events in this window)",
        "Ran without a human: " + ("n/a" if share is None else f"{share:.1%} of actions"),
        f"Incidents: {d.incidents} · heals (overlays held): {d.heals} · "
        f"overlays reverted: {d.overlays_reverted}",
        f"Candidates: {d.candidates_created} created · {d.candidates_promoted} promoted · "
        f"{d.candidates_rolled_back} rolled back",
        f"Work orders: {d.work_orders_created} created · {d.work_orders_posted} posted",
        f"Kill switch: {d.freezes} freeze(s)",
        f"Human actions: {len(d.human_actions)}",
    ]
    lines += [f"  {_fmt(h.occurred_at)}  {h.actor}  {h.kind}: {h.why}" for h in d.human_actions]
    return "\n".join(lines) + "\n"
