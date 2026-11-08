"""An incident's story: its events as a readable, ordered chain (D10, v0 plan §6). Pure.

`build_story` is generic over event kinds. Each step shows who acted, the kind, a
plain-English line and the event's `why`. Later milestones extend a story by recording
more events with the incident in their refs (candidate, gate, work order, evidence), and,
if they want a nicer line or chain label than the default, by adding an entry to
`LINES` / `LABELS` (and `STAGE` for tie order). Nothing here needs rewriting.

Order is by when things happened (`ts`, the device's `occurred_at` for device events),
never by when the server received them (K9). Events with the same `ts` are ordered by
their stage in the loop (`STAGE`), then by the order they were given in.
"""

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol


class EventLike(Protocol):
    """What a story needs from an event (a `models.Event` row fits)."""

    ts: datetime
    actor: str
    actor_class: str
    kind: str
    refs: Mapping[str, Any]
    why: str
    data: Mapping[str, Any] | None


@dataclass(frozen=True)
class Step:
    at: datetime
    actor: str
    actor_class: str
    kind: str
    line: str
    why: str
    label: str
    refs: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Story:
    incident_id: str
    steps: tuple[Step, ...]

    @property
    def chain(self) -> str:
        """One line for the timeline: `camera_occluded → agent acted → overlay ov-12 → held`."""
        return " → ".join(s.label for s in self.steps)

    def to_dict(self) -> dict[str, Any]:
        return {
            "incident_id": self.incident_id,
            "chain": self.chain,
            "steps": [
                {
                    "at": s.at.isoformat(),
                    "actor": s.actor,
                    "actor_class": s.actor_class,
                    "kind": s.kind,
                    "line": s.line,
                    "why": s.why,
                    "label": s.label,
                    "refs": s.refs,
                }
                for s in self.steps
            ],
        }


def _data(e: EventLike) -> Mapping[str, Any]:
    return e.data or {}


def _device(e: EventLike) -> str:
    return str(e.refs.get("device") or e.actor)


def _overlay_line(verb: str) -> Callable[[EventLike], str]:
    return lambda e: f"Overlay {e.refs.get('overlay')} {verb} on {_device(e)}."


def _actions(e: EventLike) -> str:
    acts = [a if isinstance(a, str) else str(a) for a in _data(e).get("actions") or []]
    return ", ".join(acts) if acts else "no recorded actions"


def _agent_line(e: EventLike) -> str:
    d = _data(e)
    outcome = "resolved it" if d.get("resolved") else "did not resolve it"
    by = f" with {d['resolving_action']}" if d.get("resolving_action") else ""
    cause = d.get("suspected_cause") or "unknown"
    return (
        f"The agent on {_device(e)} did: {_actions(e)}. It {outcome}{by}; suspected cause: {cause}."
    )


def _applied_line(e: EventLike) -> str:
    d = _data(e)
    parts = []
    if d.get("rules"):
        parts.append("rules " + ", ".join(d["rules"]))
    if d.get("params"):
        parts.append("params " + ", ".join(d["params"]))
    if d.get("code_paths"):
        parts.append("code " + ", ".join(d["code_paths"]))
    what = "; ".join(parts) if parts else "no changes listed"
    safety = " It touches the safety layer or visor." if e.refs.get("touches_safety") else ""
    overlay = e.refs.get("overlay")
    return f"The agent patched {_device(e)} locally as overlay {overlay}: {what}.{safety}"


# Plain-English line per kind. Unknown kinds fall back to `_default_line`.
LINES: dict[str, Callable[[EventLike], str]] = {
    "incident.reported": lambda e: (
        f"{_device(e)} reported {_data(e).get('kind', 'an incident')}: "
        f"{_data(e).get('summary') or 'no summary'}"
    ),
    "incident.agent_acted": _agent_line,
    "overlay.applied": _applied_line,
    "overlay.held": _overlay_line("held"),
    "overlay.reverted": _overlay_line("was reverted"),
    "overlay.locked_in": _overlay_line("was locked into a fleet version"),
    "overlay.dropped": _overlay_line("was dropped"),
    "overlay.superseded": _overlay_line("was superseded by a new version"),
}

# Short label per kind for the one-line chain. Unknown kinds use the kind itself.
LABELS: dict[str, Callable[[EventLike], str]] = {
    "incident.reported": lambda e: str(_data(e).get("kind", "incident")),
    "incident.agent_acted": lambda e: "agent acted",
    "overlay.applied": lambda e: f"overlay {e.refs.get('overlay')}",
    "overlay.held": lambda e: "held",
    "overlay.reverted": lambda e: "reverted",
    "overlay.locked_in": lambda e: "locked in",
    "overlay.dropped": lambda e: "dropped",
    "overlay.superseded": lambda e: "superseded",
}

# Where a kind sits in the loop, to order events that share a timestamp (an incident,
# the agent's actions and its overlay all carry the incident's `occurred_at`).
STAGE: dict[str, int] = {
    "incident.reported": 0,
    "incident.agent_acted": 1,
    "overlay.applied": 2,
}
_DEFAULT_STAGE = 50


def _default_line(e: EventLike) -> str:
    return f"{e.actor}: {e.kind.replace('.', ' ').replace('_', ' ')}."


def build_story(events: Iterable[EventLike], incident_id: str) -> Story:
    """The incident's events as ordered, readable steps. Pass every event that refs it."""
    indexed = list(enumerate(events))
    indexed.sort(key=lambda p: (p[1].ts, STAGE.get(p[1].kind, _DEFAULT_STAGE), p[0]))
    steps = tuple(
        Step(
            at=e.ts,
            actor=e.actor,
            actor_class=e.actor_class,
            kind=e.kind,
            line=LINES.get(e.kind, _default_line)(e),
            why=e.why,
            label=LABELS.get(e.kind, lambda ev: ev.kind)(e),
            refs=dict(e.refs),
        )
        for _, e in indexed
    )
    return Story(incident_id=incident_id, steps=steps)
