"""Group podd's incidents into faults, and decide when each fault is ready to report.

podd opens one incident per triage finding, so one fault often opens several: scenario 01
opens `crash`, `reboot_loop` and `safe_mode` within about 5 s (HARNESS.md §7). The cloud
wants one incident per fault, and a report it can't amend later (it's idempotent on
`incident_id`), so a fault is reported once, when its story is over:

- **Grouping.** An `open` record joins the current fault while that fault still has an
  open incident, or within `merge_s` of its last incident record. Otherwise it starts a new
  fault. (HARNESS.md suggests the same ~10 s debounce for waking the agent.)
- **Activity.** Heal-tool calls and notifications that land while a fault is in progress
  keep it in progress, so a report isn't sent while the healer is still working.
- **Ready** when one of these holds:
  - `resolved`: every incident resolved, and `settle_s` passed with no new activity;
  - `handed_off`: still unresolved, the healer asked a person to act (`notify_human` with
    instructions), and `quiet_s` passed with no new activity;
  - `timed_out`: still unresolved after `max_open_s`.
  A resolved fault is also reported once `max_open_s` has passed, so constant tool calls
  can't hold a report back forever.

Pure: no I/O and no clock. Times are podd's epoch seconds.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

Reason = Literal["resolved", "handed_off", "timed_out"]

SEVERITY_RANK = {"critical": 0, "warn": 1, "info": 2}


@dataclass(frozen=True)
class Timing:
    merge_s: float = 10.0
    settle_s: float = 15.0
    quiet_s: float = 60.0
    max_open_s: float = 900.0

    def __post_init__(self) -> None:
        # A fault must still be in progress for as long as an incident may join it.
        if self.settle_s < self.merge_s:
            raise ValueError("settle_s must be at least merge_s")


DEFAULT_TIMING = Timing()


@dataclass
class PoddIncident:
    """One podd incident (an `incidents.jsonl` open record, plus its resolve record)."""

    id: str
    kind: str
    severity: str
    summary: str
    opened: float
    first_evidence: dict[str, Any] = field(default_factory=dict)
    resolved_at: float | None = None
    duration_s: float | None = None

    @classmethod
    def from_open(cls, rec: dict[str, Any]) -> PoddIncident:
        return cls(
            id=str(rec["id"]),
            kind=str(rec.get("kind", "unknown")),
            severity=str(rec.get("severity", "warn")),
            summary=str(rec.get("summary", "")),
            opened=float(rec.get("opened", rec["ts"])),
            first_evidence=rec.get("first_evidence") or {},
        )


@dataclass
class Fault:
    """podd incidents that belong to one fault, and what happened while it was open."""

    key: str
    """The first incident's podd id: stable, so the fault's cloud ids are too."""
    start: float
    incidents: list[PoddIncident]
    last_incident_ts: float
    last_activity: float
    handoff_at: float | None = None
    mcu_build: str | None = None
    """The MCU build running when the fault opened."""

    @property
    def unresolved(self) -> bool:
        return any(i.resolved_at is None for i in self.incidents)

    @property
    def primary(self) -> PoddIncident:
        """The most severe incident, the earliest of equals: the fault's name."""
        return min(self.incidents, key=lambda i: (SEVERITY_RANK.get(i.severity, 3), i.opened))

    def end(self, now: float) -> float:
        """When the fault was over: its last activity if resolved, else `now`."""
        return now if self.unresolved else self.last_activity

    def ready(self, now: float, timing: Timing) -> Reason | None:
        quiet_for = now - self.last_activity
        if self.unresolved:
            if self.handoff_at is not None and quiet_for >= timing.quiet_s:
                return "handed_off"
            if now - self.start >= timing.max_open_s:
                return "timed_out"
            return None
        if quiet_for >= timing.settle_s or now - self.start >= timing.max_open_s:
            return "resolved"
        return None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Fault:
        incidents = [PoddIncident(**i) for i in data["incidents"]]
        return cls(**(data | {"incidents": incidents}))


class FaultTracker:
    """Feed it podd records in time order; it hands back the faults that are ready."""

    def __init__(self, timing: Timing = DEFAULT_TIMING, faults: list[Fault] | None = None) -> None:
        self.timing = timing
        self.faults: list[Fault] = faults or []

    def observe_incident(self, rec: dict[str, Any], mcu_build: str | None = None) -> None:
        """One `incidents.jsonl` record: `status` is `open` or `resolved`.

        A fault that was already ready at the record's `ts` is final: it takes no new
        incidents and no resolves, as if the bridge had reported it the moment it was ready.
        """
        ts = float(rec["ts"])
        if rec.get("status") == "open":
            incident = PoddIncident.from_open(rec)
            current = self.faults[-1] if self.faults else None
            if current is not None and self._joins(current, ts):
                current.incidents.append(incident)
                current.last_incident_ts = ts
                current.last_activity = max(current.last_activity, ts)
            else:
                self.faults.append(Fault(incident.id, ts, [incident], ts, ts, mcu_build=mcu_build))
        elif rec.get("status") == "resolved":
            for fault in self.faults:
                for incident in fault.incidents:
                    if (
                        incident.id == rec.get("id")
                        and incident.resolved_at is None
                        and fault.ready(ts, self.timing) is None
                    ):
                        incident.resolved_at = ts
                        incident.duration_s = rec.get("duration_s")
                        fault.last_incident_ts = ts
                        fault.last_activity = max(fault.last_activity, ts)

    def _joins(self, fault: Fault, ts: float) -> bool:
        in_progress = fault.ready(ts, self.timing) is None
        recent = ts - fault.last_incident_ts <= self.timing.merge_s
        return in_progress and (fault.unresolved or recent)

    def observe_activity(self, ts: float, handoff: bool = False) -> None:
        """A heal-tool call that ended at `ts`, or a notification (`handoff` when it asks a
        person to act). It counts for the newest fault that started by `ts` if that fault
        was still in progress at `ts` (not yet ready), so the outcome doesn't depend on
        when the bridge happens to tick. Otherwise it belongs to no fault."""
        for fault in reversed(self.faults):
            if fault.start <= ts:
                if fault.ready(ts, self.timing) is None:
                    fault.last_activity = max(fault.last_activity, ts)
                    if handoff and fault.handoff_at is None:
                        fault.handoff_at = ts
                return

    def pop_ready(self, now: float) -> list[tuple[Fault, Reason]]:
        """Remove and return the faults that are ready, oldest first, with why."""
        ready, waiting = [], []
        for fault in self.faults:
            reason = fault.ready(now, self.timing)
            (ready if reason else waiting).append((fault, reason))
        self.faults = [f for f, _ in waiting]
        return [(f, r) for f, r in ready if r is not None]

    def to_list(self) -> list[dict[str, Any]]:
        return [f.to_dict() for f in self.faults]
