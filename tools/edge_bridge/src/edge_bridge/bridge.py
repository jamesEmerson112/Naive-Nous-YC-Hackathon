"""The bridge: follow the pod healer and report to the cloud. `tick(now)` does it all.

Each tick:
1. asks podd for `status` and `stats` (None while podd is down);
2. reads what's new in four streams and feeds it to the fault tracker in time order:
   `incidents.jsonl`, `audit.jsonl` (each call placed where it ended), `notifications.jsonl`,
   and from `events.jsonl` only the pod's boot records and podd's flash start and end.
   Boot records keep the MCU build current in stream time, so each fault is tagged with
   the build that was running when it opened, not when the bridge happened to look;
3. for each fault that's ready, gathers its evidence, builds the report and queues it in
   the outbox: the incident first, then its recordings (APIs §3);
4. saves its state: file cursors, faults in progress, the MCU builds;
5. checks in live (a check-in isn't queued: an old one is worthless) and drains the outbox.

At least once: a report is queued before the state that says it was sent is saved, so a
crash in between queues it again with the same ids, and the cloud answers "already
received". The bridge talks to the cloud only through `fake_device`'s DeviceClient and
Outbox (the reference device client), and to the healer only through `podd.py`.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from pydantic import ValidationError

from edge_bridge import podd, translate
from edge_bridge.faults import DEFAULT_TIMING, Fault, FaultTracker, Ready, Timing
from edge_bridge.podd import Cursor, PoddPaths, PoddSocket
from fake_device.client import DeviceClient
from fake_device.outbox import Outbox, atomic_write

log = logging.getLogger(__name__)

REPORTED_KEEP = 1000
AUDIT_DISORDER_S = 300.0
"""How far out of order audit records can be: a call is written when it returns."""
EVENTS_KEEP = (b'"boot"', b'"flash_start"', b'"flash_done"')
"""A cheap filter for the event-log lines the tracker needs; `_observe` checks `ev` exactly."""


@dataclass
class State:
    """What survives a restart (`state.json`)."""

    since: float
    """Records older than this are ignored: the first start, minus any backfill."""
    cursors: dict[str, Cursor] = field(default_factory=dict)
    faults: list[dict[str, Any]] = field(default_factory=list)
    build: str | None = None
    """The MCU build for check-ins: what's running now."""
    stream_build: str | None = None
    """The MCU build as of the last boot record read from the event log."""
    reported: list[str] = field(default_factory=list)
    """podd incident ids already reported (the newest `REPORTED_KEEP`)."""

    @classmethod
    def load(cls, path: Path) -> State | None:
        try:
            data = json.loads(path.read_text())
        except FileNotFoundError:
            return None
        cursors = {k: Cursor.from_dict(v) for k, v in data.get("cursors", {}).items()}
        return cls(**(data | {"cursors": cursors}))

    def save(self, path: Path) -> None:
        data = {
            "since": self.since,
            "cursors": {k: c.to_dict() for k, c in self.cursors.items()},
            "faults": self.faults,
            "build": self.build,
            "stream_build": self.stream_build,
            "reported": self.reported,
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(path, json.dumps(data, indent=1).encode())


@dataclass(frozen=True)
class TickResult:
    health: str
    checked_in: bool
    reported: list[str]
    """Cloud incident ids queued this tick."""
    sent: int
    """Outbox entries the cloud accepted this tick."""


class Bridge:
    def __init__(
        self,
        paths: PoddPaths,
        device_id: str,
        client: DeviceClient,
        outbox: Outbox,
        sock: PoddSocket,
        state_path: Path,
        timing: Timing = DEFAULT_TIMING,
        backfill_s: float = 0.0,
    ) -> None:
        self.paths = paths
        self.device_id = device_id
        self.client = client
        self.outbox = outbox
        self.sock = sock
        self.state_path = state_path
        self.timing = timing
        self.backfill_s = backfill_s
        self.risks = podd.tool_risks(paths)
        self.state: State | None = None
        self.tracker: FaultTracker | None = None
        self._last_health: str | None = None
        self._looked_for_build = False
        self._warned_assigned = False

    def tick(self, now: float) -> TickResult:
        state, tracker = self._load(now)
        status = self.sock.call("status")
        stats = self.sock.call("stats", seconds=10) if status else None

        for kind, rec in self._new_records(state):
            self._observe(state, tracker, kind, rec)
        self._track_build(state, status)

        reported = []
        for ready in tracker.pop_ready(now):
            reported.append(self._queue(ready, now))
            state.reported.extend(i.id for i in ready.fault.incidents)
        state.reported = state.reported[-REPORTED_KEEP:]
        state.faults = tracker.to_list()
        state.save(self.state_path)

        health, checked_in = self._check_in(status, stats, state.build)
        sent = self.outbox.drain(self.client, translate.utc(now))
        return TickResult(health, checked_in, reported, sent)

    # -- reading ------------------------------------------------------------------------

    def _load(self, now: float) -> tuple[State, FaultTracker]:
        if self.state is None or self.tracker is None:
            self.state = State.load(self.state_path) or State(since=now - self.backfill_s)
            faults = [Fault.from_dict(f) for f in self.state.faults]
            self.tracker = FaultTracker(self.timing, faults)
        return self.state, self.tracker

    def _new_records(self, state: State) -> list[tuple[str, dict[str, Any]]]:
        """New records from the four streams, oldest first. Boot records count even before
        `since`, because they say what's running; everything else from before is ignored."""
        streams = {
            "incidents": (self.paths.log("incidents"), None),
            "audit": (self.paths.log("audit"), None),
            "notifications": (self.paths.notifications, None),
            "events": (self.paths.log("events"), EVENTS_KEEP),
        }
        out = []
        for kind, (path, keep) in streams.items():
            records, cursor = podd.read_new(path, state.cursors.get(kind), keep)
            if cursor is not None:
                state.cursors[kind] = cursor
            for rec in records:
                is_boot = kind == "events" and rec.get("ev") == "boot"
                if float(rec["ts"]) < state.since and not is_boot:
                    continue
                if kind == "incidents" and rec.get("id") in state.reported:
                    continue
                out.append((kind, rec))
        return sorted(out, key=_stream_time)

    def _observe(self, state: State, tracker: FaultTracker, kind: str, rec: dict) -> None:
        if kind == "incidents":
            tracker.observe_incident(rec, mcu_build=state.stream_build or state.build)
        elif kind == "audit":
            tracker.observe_activity(_stream_time((kind, rec)))
        elif kind == "notifications":
            asks = bool(str(rec.get("actions") or "").strip())
            tracker.observe_activity(float(rec["ts"]), handoff=asks)
        elif rec.get("ev") == "boot":
            state.stream_build = translate.build_from_boot(rec) or state.stream_build
        elif rec.get("ev") in ("flash_start", "flash_done"):
            tracker.observe_flash(float(rec["ts"]), started=rec.get("ev") == "flash_start")

    def _track_build(self, state: State, status: dict[str, Any] | None) -> None:
        """The MCU build for check-ins: podd's last boot record; else the newest boot read
        from the event log; on a first start without either, the newest deployment."""
        build = translate.build_from_boot((status or {}).get("last_boot")) or state.stream_build
        if build:
            state.build = build
        elif state.build is None and not self._looked_for_build:
            self._looked_for_build = True
            deployed = [
                d
                for d in podd.read_all(self.paths.deployments)
                if d.get("result") in ("ok", "unverified")
            ]
            state.build = translate.build_from_deployment(deployed[-1] if deployed else None)

    # -- reporting ----------------------------------------------------------------------

    def _queue(self, ready: Ready, now: float) -> str:
        ev = self._evidence(ready, now)
        report = translate.report(self.device_id, ready.fault, ready.reason, ev)
        self.outbox.add_incident(report.incident)
        for meta, data in report.recordings:
            self.outbox.add_recording(meta, data)
        incident = report.incident
        log.info(
            "queued %s (%s, %s): %s",
            incident.kind,
            ready.reason,
            incident.outcome.suspected_cause if incident.outcome else "unknown",
            incident.summary,
        )
        return str(incident.incident_id)

    def _evidence(self, ready: Ready, now: float) -> translate.Evidence:
        fault = ready.fault
        lo, hi = translate.window(fault, ready.at, now)

        def within(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
            return [r for r in rows if lo <= float(r["ts"]) <= hi]

        logs = {
            name: podd.read_window(self.paths.log(name), lo, hi)
            for name in ("telemetry", "health", "events")
        }
        deployments = within(podd.read_all(self.paths.deployments))
        images = {}
        for d in deployments:
            crc = translate.normalize_crc(d.get("crc"))
            if d.get("result") == "ok" and crc and (meta := podd.image(self.paths, crc)):
                images[crc] = meta
        return translate.Evidence(
            lo=lo,
            hi=hi,
            logs=logs,
            audit=podd.read_window(self.paths.log("audit"), lo, hi, AUDIT_DISORDER_S),
            notifications=within(podd.read_all(self.paths.notifications)),
            deployments=deployments,
            risks=self.risks,
            images=images,
            workspace_log=podd.workspace_log(self.paths, fault.start, min(hi, ready.until)),
            until=ready.until,
        )

    def _check_in(
        self, status: dict[str, Any] | None, stats: dict[str, Any] | None, build: str | None
    ) -> tuple[str, bool]:
        body = translate.checkin(status, stats, build)
        if body.health != self._last_health:
            log.info("pod health %s (%s)", body.health, body.health_detail)
            self._last_health = body.health
        try:
            response = self.client.checkin(body)
        except (httpx.HTTPError, ValidationError, ValueError) as exc:
            log.info("check-in not sent or not understood: %s", exc)
            return body.health, False
        # The bridge pulls and installs nothing: the healer manages the pod's firmware.
        # Firmware is assigned per device class, so firmware here means the cloud took the
        # pod for an arm. (Harness versions aren't per class, so one may be assigned anyway.)
        if response.assigned.firmware is not None and not self._warned_assigned:
            self._warned_assigned = True
            log.warning(
                "the cloud assigned arm firmware %s to %s; the bridge installs nothing. "
                "Register the pod with device_class sensor_pod (README).",
                response.assigned.firmware,
                self.device_id,
            )
        return body.health, True


def _stream_time(item: tuple[str, dict[str, Any]]) -> float:
    """Where a record sits in time: an audit record at the end of its call, others at `ts`."""
    kind, rec = item
    end = float(rec.get("duration_s") or 0) if kind == "audit" else 0.0
    return float(rec["ts"]) + end
