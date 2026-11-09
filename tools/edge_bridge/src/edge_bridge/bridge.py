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
5. checks in live (a check-in isn't queued: an old one is worthless) and drains the outbox;
6. reports the heal agent's runs (`firmware/harness/runs/<run>/`): for each run whose files
   changed since the cloud last accepted its report, builds the whole report again
   (`agent_runs.py`) and `PUT`s it. Reports are full snapshots and idempotent, so they skip
   the outbox: one that isn't accepted is simply built and sent again next tick. A run is
   tied to its incident through podd's incident ids (the harness records them at the start)
   and the faults those joined; without them the cloud matches by time.

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

from edge_bridge import agent_runs, podd, translate
from edge_bridge.faults import DEFAULT_TIMING, Fault, FaultTracker, Ready, Timing
from edge_bridge.podd import Cursor, PoddPaths, PoddSocket
from fake_device.client import DeviceClient
from fake_device.outbox import Outbox, atomic_write
from romulus_contracts import AgentRunReport

log = logging.getLogger(__name__)

REPORTED_KEEP = 1000
AUDIT_DISORDER_S = 300.0
"""How far out of order audit records can be: a call is written when it returns."""
EVENTS_KEEP = (b'"boot"', b'"flash_start"', b'"flash_done"')
"""A cheap filter for the event-log lines the tracker needs; `_observe` checks `ev` exactly."""
RUNS_KEEP = 300
FAULT_INDEX_KEEP = 2000


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
    fault_index: dict[str, list[Any]] = field(default_factory=dict)
    """podd incident id → [its fault's key, the fault's start]: ties an agent run to the
    cloud incident of the fault it worked on."""
    runs: dict[str, dict[str, Any]] = field(default_factory=dict)
    """Agent run id → what the cloud last accepted: `sig` (the files), `state`, `status`."""

    @classmethod
    def load(cls, path: Path) -> State | None:
        """The saved state; None if there's none. A file that can't be read (damaged, or
        from an incompatible version) is moved aside, so the bridge starts fresh instead of
        failing every tick."""
        try:
            data = json.loads(path.read_text())
            cursors = {k: Cursor.from_dict(v) for k, v in data.get("cursors", {}).items()}
            return cls(**(data | {"cursors": cursors}))
        except FileNotFoundError:
            return None
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            aside = path.with_name(f"{path.name}.unreadable")
            path.replace(aside)
            log.warning(
                "state %s is unreadable (%s); moved to %s, starting fresh", path, exc, aside
            )
            return None

    def save(self, path: Path) -> None:
        data = {
            "since": self.since,
            "cursors": {k: c.to_dict() for k, c in self.cursors.items()},
            "faults": self.faults,
            "build": self.build,
            "stream_build": self.stream_build,
            "reported": self.reported,
            "fault_index": self.fault_index,
            "runs": self.runs,
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
    runs: int = 0
    """Agent run reports the cloud accepted this tick."""


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
        runs_dir: Path | None = None,
    ) -> None:
        self.paths = paths
        self.device_id = device_id
        self.client = client
        self.outbox = outbox
        self.sock = sock
        self.state_path = state_path
        self.timing = timing
        self.backfill_s = backfill_s
        self.runs_dir = runs_dir if runs_dir is not None else paths.harness_runs
        self._run_errors: set[tuple[str, str]] = set()
        self.risks = podd.tool_risks(paths)
        self.state: State | None = None
        self.tracker: FaultTracker | None = None
        self._last_health: str | None = None
        self._looked_for_build = False
        self._warned_assigned = False

    def tick(self, now: float) -> TickResult:
        try:
            return self._tick(now)
        except Exception:
            # Forget what this tick read and popped; the next one reloads the last saved
            # state, so nothing is skipped (re-queued reports keep their ids).
            self.state = self.tracker = None
            raise

    def _tick(self, now: float) -> TickResult:
        state, tracker, fresh = self._load(now)
        status = self.sock.call("status")
        stats = self.sock.call("stats", seconds=10) if status else None

        for kind, rec in self._new_records(state, status if fresh else None):
            self._observe(state, tracker, kind, rec)
        self._track_build(state, status)

        for fault in tracker.faults:
            self._index(state, fault)
        reported = []
        for ready in tracker.pop_ready(now):
            self._index(state, ready.fault)
            reported.append(self._queue(ready, now))
            state.reported.extend(i.id for i in ready.fault.incidents)
        state.reported = state.reported[-REPORTED_KEEP:]
        state.faults = tracker.to_list()
        state.save(self.state_path)

        health, checked_in = self._check_in(status, stats, state.build)
        sent = self.outbox.drain(self.client, translate.utc(now))
        runs = self._report_runs(state, now) if checked_in else 0
        if runs:
            state.save(self.state_path)
        return TickResult(health, checked_in, reported, sent, runs)

    # -- reading ------------------------------------------------------------------------

    def _load(self, now: float) -> tuple[State, FaultTracker, bool]:
        """The state and tracker, loading them if needed; `True` when starting fresh."""
        fresh = False
        if self.state is None or self.tracker is None:
            loaded = State.load(self.state_path)
            fresh = loaded is None
            self.state = loaded or State(since=now - self.backfill_s)
            faults = [Fault.from_dict(f) for f in self.state.faults]
            self.tracker = FaultTracker(self.timing, faults)
        return self.state, self.tracker, fresh

    def _new_records(
        self, state: State, status: dict[str, Any] | None = None
    ) -> list[tuple[str, dict[str, Any]]]:
        """New records from the four streams, oldest first. Records from before `since` are
        ignored, except boot records (they say what's running) and, on the first start
        (`status` given), the open records of incidents podd says are still open, so a
        fault already in progress when the bridge starts is reported too."""
        still_open = {i.get("id") for i in (status or {}).get("open_incidents") or []}
        reported = set(state.reported)
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
                in_progress = kind == "incidents" and rec.get("id") in still_open
                if float(rec["ts"]) < state.since and not (is_boot or in_progress):
                    continue
                if kind == "incidents" and rec.get("id") in reported:
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

    # -- agent runs ---------------------------------------------------------------------

    @staticmethod
    def _index(state: State, fault: Fault) -> None:
        for i in fault.incidents:
            state.fault_index[i.id] = [fault.key, fault.start]
        while len(state.fault_index) > FAULT_INDEX_KEEP:
            state.fault_index.pop(next(iter(state.fault_index)))

    def _report_runs(self, state: State, now: float) -> int:
        """Send the report of every run whose files changed since the cloud last accepted it.
        Runs that started before `since` (the bridge's first start, minus any backfill) are
        left alone."""
        accepted = 0
        for run in podd.harness_runs(self.runs_dir, since=state.since):
            seen = state.runs.get(run.run_id) or {}
            if seen.get("skip") or seen.get("sig") == list(run.sig):
                continue
            records = podd.read_transcript(run.transcript)
            if not records or records[0].get("type") != "run_start":
                continue
            started, _ = agent_runs.span(records)
            if started < state.since:
                state.runs[run.run_id] = {"skip": "started before the bridge's first start"}
                continue
            ids, window = self._link(state, run.run_id, records, now)
            body = agent_runs.build(
                run.run_id,
                records,
                score=podd.read_json(run.score),
                score_at=run.score_mtime,
                incident_ids=ids,
            )
            if window is not None:
                body["incident_window"] = [agent_runs.iso(window[0]), agent_runs.iso(window[1])]
            try:
                report = AgentRunReport.model_validate(body)
            except ValidationError as exc:
                log.warning("agent run %s doesn't fit the contract, not sent: %s", run.run_id, exc)
                state.runs[run.run_id] = {"skip": "invalid report"}
                continue
            try:
                self.client.put_agent_run(report)
            except (httpx.HTTPError, ValidationError, ValueError) as exc:
                key = (run.run_id, type(exc).__name__)
                if key not in self._run_errors:  # once per run and kind of error; retried each tick
                    self._run_errors.add(key)
                    log.info("agent run %s not accepted yet (%s); will retry", run.run_id, exc)
                continue
            if (
                seen.get("state") != body["state"]
                or seen.get("status") != body["outcome"]["status"]
            ):
                log.info(
                    "agent run %s: %s, %s (%d steps, %d firmware events, %d help requests)",
                    run.run_id,
                    body["state"],
                    body["outcome"]["status"],
                    len(body["steps"]),
                    len(body["firmware"]),
                    len(body["help_requests"]),
                )
            state.runs[run.run_id] = {
                "sig": list(run.sig),
                "state": body["state"],
                "status": body["outcome"]["status"],
            }
            accepted += 1
        while len(state.runs) > RUNS_KEEP:
            state.runs.pop(next(iter(state.runs)))
        return accepted

    def _link(
        self, state: State, run_id: str, records: list[dict[str, Any]], now: float
    ) -> tuple[list[str], tuple[float, float] | None]:
        """The cloud incident ids of the faults this run worked on, from podd's incident ids
        at its start. A rehearsal (`--sim`) or a run that started with nothing open is tied to
        none. Without podd's ids (an older transcript, or ids from before the bridge knew the
        faults), a time window for the cloud to match instead."""
        if "sim" in run_id.split("-"):
            return [], None
        opened = agent_runs.podd_incidents(records)
        started, ended = agent_runs.span(records)
        window = (started - agent_runs.LINK_BEFORE_S, ended if ended is not None else now)
        if opened is None:
            return [], window
        keys = [state.fault_index[i["id"]][0] for i in opened if i.get("id") in state.fault_index]
        ids = [str(translate.incident_uuid(self.device_id, k)) for k in dict.fromkeys(keys)]
        if opened and not ids:
            return [], window
        return ids, None

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
