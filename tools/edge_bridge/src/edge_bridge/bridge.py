"""The bridge: follow the pod healer and report to the cloud. `tick(now)` does it all.

Each tick:
1. asks podd for `status` and `stats` (None while podd is down) and tracks the MCU build;
2. reads what's new in `incidents.jsonl`, `audit.jsonl` and `notifications.jsonl` and
   feeds it to the fault tracker in time order;
3. for each fault that's ready, gathers its evidence, builds the report and queues it in
   the outbox: the incident first, then its recordings (APIs §3);
4. saves its state: file cursors, faults in progress, the MCU build;
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

from edge_bridge import podd, translate
from edge_bridge.faults import Fault, FaultTracker, Reason, Timing
from edge_bridge.podd import Cursor, PoddPaths, PoddSocket
from fake_device.client import DeviceClient
from fake_device.outbox import Outbox, atomic_write

log = logging.getLogger(__name__)

STREAMS = ("incidents", "audit", "notifications")
REPORTED_KEEP = 1000
BOOT_LOOKBACK_S = 24 * 3600


@dataclass
class State:
    """What survives a restart (`state.json`)."""

    since: float
    """Records older than this are ignored: the first start, minus any backfill."""
    cursors: dict[str, Cursor] = field(default_factory=dict)
    faults: list[dict[str, Any]] = field(default_factory=list)
    build: str | None = None
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
            "reported": self.reported[-REPORTED_KEEP:],
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
        timing: Timing = Timing(),
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
        self._track_build(state, status, now)

        for kind, rec in self._new_records(state):
            if kind == "incidents":
                tracker.observe_incident(rec, mcu_build=state.build)
            elif kind == "audit":
                tracker.observe_activity(float(rec["ts"]) + float(rec.get("duration_s") or 0))
            else:
                asks = bool(str(rec.get("actions") or "").strip())
                tracker.observe_activity(float(rec["ts"]), handoff=asks)

        reported = [self._queue(fault, reason, now) for fault, reason in tracker.pop_ready(now)]
        for incident_id, fault in reported:
            state.reported.extend(i.id for i in fault.incidents)
        state.faults = tracker.to_list()
        state.save(self.state_path)

        health, checked_in = self._check_in(status, stats, state.build)
        sent = self.outbox.drain(self.client, translate.utc(now))
        return TickResult(health, checked_in, [i for i, _ in reported], sent)

    # -- steps --------------------------------------------------------------------------

    def _load(self, now: float) -> tuple[State, FaultTracker]:
        if self.state is None or self.tracker is None:
            self.state = State.load(self.state_path) or State(since=now - self.backfill_s)
            faults = [Fault.from_dict(f) for f in self.state.faults]
            self.tracker = FaultTracker(self.timing, faults)
        return self.state, self.tracker

    def _track_build(self, state: State, status: dict[str, Any] | None, now: float) -> None:
        """The MCU build: podd's last boot record; on a first start without one, the newest
        boot in the event log, else the newest deployment."""
        build = translate.build_from_boot((status or {}).get("last_boot"))
        if build:
            state.build = build
        elif state.build is None and not self._looked_for_build:
            self._looked_for_build = True
            events = podd.read_window(self.paths.log("events"), now - BOOT_LOOKBACK_S, now)
            boots = [e for e in events if e.get("ev") == "boot"]
            deployed = [
                d
                for d in podd.read_all(self.paths.deployments)
                if d.get("result") in ("ok", "unverified")
            ]
            state.build = translate.build_from_boot(boots[-1] if boots else None) or (
                translate.build_from_deployment(deployed[-1] if deployed else None)
            )

    def _new_records(self, state: State) -> list[tuple[str, dict[str, Any]]]:
        """New records from the three streams, oldest first. An audit record is placed at
        the end of its call (podd writes it then), the others at their `ts`."""
        out = []
        files = {
            "incidents": self.paths.log("incidents"),
            "audit": self.paths.log("audit"),
            "notifications": self.paths.notifications,
        }
        for kind in STREAMS:
            records, cursor = podd.read_new(files[kind], state.cursors.get(kind))
            if cursor is not None:
                state.cursors[kind] = cursor
            for rec in records:
                if float(rec["ts"]) < state.since:
                    continue
                if kind == "incidents" and rec.get("id") in state.reported:
                    continue
                out.append((kind, rec))

        def at(item: tuple[str, dict[str, Any]]) -> float:
            kind, rec = item
            end = float(rec.get("duration_s") or 0) if kind == "audit" else 0.0
            return float(rec["ts"]) + end

        return sorted(out, key=at)

    def _queue(self, fault: Fault, reason: Reason, now: float) -> tuple[str, Fault]:
        report = translate.report(self.device_id, fault, reason, self._evidence(fault, now))
        self.outbox.add_incident(report.incident)
        for meta, data in report.recordings:
            self.outbox.add_recording(meta, data)
        incident = report.incident
        log.info(
            "queued %s (%s, %s): %s",
            incident.kind,
            reason,
            incident.outcome.suspected_cause if incident.outcome else "unknown",
            incident.summary,
        )
        return str(incident.incident_id), fault

    def _evidence(self, fault: Fault, now: float) -> translate.Evidence:
        lo, hi = translate.window(fault, now)

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
            audit=podd.read_window(self.paths.log("audit"), lo, hi),
            notifications=within(podd.read_all(self.paths.notifications)),
            deployments=deployments,
            risks=self.risks,
            images=images,
            workspace_log=podd.workspace_log(self.paths, fault.start, hi),
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
        except httpx.HTTPError as exc:
            log.info("check-in not sent: %s", exc)
            return body.health, False
        assigned = response.assigned
        if (assigned.firmware or assigned.harness) and not self._warned_assigned:
            self._warned_assigned = True
            log.warning(
                "the cloud assigned firmware %s / harness %s to %s; the bridge installs nothing "
                "(the healer manages the pod's firmware). Register the pod with device_class "
                "sensor_pod so the cloud doesn't treat it as an arm (README).",
                assigned.firmware,
                assigned.harness,
                self.device_id,
            )
        return body.health, True
