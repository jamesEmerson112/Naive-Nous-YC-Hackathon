"""Group podd's incidents into faults, and decide when each fault is ready to report.

podd opens one incident per triage finding, so one fault often opens several: scenario 01
opens `crash`, `reboot_loop` and `safe_mode` within about 5 s (HARNESS.md §7). The cloud
wants one incident per fault, and a report it can't amend later (it's idempotent on
`incident_id`), so a fault is reported once, when its story is over:

- **Grouping.** An `open` record joins the current fault while that fault still has an
  open incident, or within `merge_s` of its last incident record. Otherwise it starts a new
  fault. (HARNESS.md suggests the same ~10 s debounce for waking the agent.)
- **Activity.** Heal-tool calls and notifications that land while a fault is in progress
  keep it in progress, so a report isn't sent while the healer is still working. A flash
  in progress (`flash_start` without its `flash_done`) holds the fault for up to
  `busy_max_s`: podd writes a tool's audit record only when the call returns, and
  `fw_flash` can run for a minute.
- **Ready** at `due()`, for one of these reasons:
  - `resolved`: every incident resolved, and `settle_s` passed with no new activity;
  - `handed_off`: still unresolved, the healer asked a person to act (`notify_human` with
    instructions), and `quiet_s` passed with no new activity;
  - `timed_out`: still unresolved after `max_open_s`.
  `max_open_s` also caps a resolved or busy fault, so constant tool calls can't hold a
  report back forever.
- **Final once ready.** A fault that was ready at a record's time takes no part in it, as
  if the bridge had reported it that moment. So the report doesn't depend on when the
  bridge happens to tick, or on how long it was down.

Pure: no I/O and no clock. Times are podd's epoch seconds.
"""

from __future__ import annotations

import math
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
    busy_max_s: float = 180.0

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
    busy_since: float | None = None
    """When the flash in progress started; None when no flash is running."""
    mcu_build: str | None = None
    """The MCU build running when the fault opened."""

    @property
    def unresolved(self) -> bool:
        return any(i.resolved_at is None for i in self.incidents)

    @property
    def primary(self) -> PoddIncident:
        """The most severe incident, the earliest of equals: the fault's name."""
        return min(self.incidents, key=lambda i: (SEVERITY_RANK.get(i.severity, 3), i.opened))

    def due(self, timing: Timing) -> tuple[float, Reason]:
        """When the fault is (or will be) ready on what's known so far, and why."""
        cap = self.start + timing.max_open_s
        if not self.unresolved:
            at, reason = self.last_activity + timing.settle_s, "resolved"
        elif self.handoff_at is not None:
            at, reason = self.last_activity + timing.quiet_s, "handed_off"
        else:
            at, reason = math.inf, "timed_out"
        if self.busy_since is not None:
            at = max(at, self.busy_since + timing.busy_max_s)
        if at >= cap:
            return cap, ("resolved" if reason == "resolved" else "timed_out")
        return at, reason

    def ready(self, now: float, timing: Timing) -> Reason | None:
        at, reason = self.due(timing)
        return reason if now >= at else None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Fault:
        incidents = [PoddIncident(**i) for i in data["incidents"]]
        return cls(**(data | {"incidents": incidents}))


@dataclass(frozen=True)
class Ready:
    """A fault that's ready to report."""

    fault: Fault
    reason: Reason
    at: float
    """When it became ready: its story ends here."""
    until: float
    """Where its activity ends: `at`, or the next fault's start if that's sooner."""


class FaultTracker:
    """Feed it podd records in time order; it hands back the faults that are ready."""

    def __init__(self, timing: Timing = DEFAULT_TIMING, faults: list[Fault] | None = None) -> None:
        self.timing = timing
        self.faults: list[Fault] = faults or []

    def observe_incident(self, rec: dict[str, Any], mcu_build: str | None = None) -> None:
        """One `incidents.jsonl` record: `status` is `open` or `resolved`."""
        ts = float(rec["ts"])
        if rec.get("status") == "open":
            if any(i.id == rec.get("id") for f in self.faults for i in f.incidents):
                return  # read twice (a truncated file is read from the start again)
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

    def _active(self, ts: float) -> Fault | None:
        """The newest fault that started by `ts`, if it was still in progress at `ts`."""
        for fault in reversed(self.faults):
            if fault.start <= ts:
                return fault if fault.ready(ts, self.timing) is None else None
        return None

    def observe_activity(self, ts: float, handoff: bool = False) -> None:
        """A heal-tool call that ended at `ts`, or a notification (`handoff` when it asks a
        person to act). Activity outside any fault in progress belongs to no fault."""
        fault = self._active(ts)
        if fault is not None:
            fault.last_activity = max(fault.last_activity, ts)
            if handoff and fault.handoff_at is None:
                fault.handoff_at = ts

    def observe_flash(self, ts: float, started: bool) -> None:
        """podd's `flash_start` (`started`) or `flash_done` host event."""
        fault = self._active(ts)
        if fault is not None:
            fault.busy_since = ts if started else None
            fault.last_activity = max(fault.last_activity, ts)

    def pop_ready(self, now: float) -> list[Ready]:
        """Remove and return the faults that are ready, oldest first."""
        ready, waiting = [], []
        for i, fault in enumerate(self.faults):
            at, reason = fault.due(self.timing)
            if now < at:
                waiting.append(fault)
                continue
            following = self.faults[i + 1].start if i + 1 < len(self.faults) else math.inf
            ready.append(Ready(fault, reason, at, min(at, following)))
        self.faults = waiting
        return ready

    def to_list(self) -> list[dict[str, Any]]:
        return [f.to_dict() for f in self.faults]
