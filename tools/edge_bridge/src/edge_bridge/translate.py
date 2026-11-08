"""Translate the pod healer's records into the Romulus contract (`romulus_contracts`).

Pure: no I/O, no clock. podd's records keep their own shape; this module only decides
what each one means to the cloud. In one place:

- **Check-in health** from podd `status`: `failing` if podd is unreachable, the link is
  down, silent or garbled, or a critical incident is open; `degraded` for a warning; else
  `ok`. The pod is real hardware, so it says `kind: real` (K17).
- **`running.mcu_build`** from the last boot record (`E ev=boot build=… crc=…`), e.g.
  `v1.1@32ACFDAF`: the version from the build id, plus the CRC the image registry uses.
- **Check-in metrics** from podd `status` and `stats`, all named `pod_*`.
- **One incident per fault** (`faults.py` groups podd's incidents): `kind` and `summary`
  from the most severe incident, `occurred_at` when the first one opened.
- **`agent.actions`**: heal-tool calls whose `tools.json` risk isn't `read`.
  **`agent.reasoning`**: the agent's notes (`ev=agent_note`).
- **`outcome`:**
  - a verified flash (`deployments.jsonl` result `ok`) resolves it by `fw_flash` when the
    image was built from the workspace (`fix-code`), else by `fw_rollback` (`ops`);
  - otherwise a runtime tool (`pod_cmd`, `chip_reset`, …) resolves it (`ops`);
  - `notify_human` with instructions for a person means `suspected_cause: hardware`, and a
    fault still unresolved with that cause becomes a work order in the cloud.
- **`firmware_patch`**: the flashed image's source diff and the workspace commits, as
  evidence only. Never an `overlay`, so the gate and the coding agent never try to roll a
  pod patch out to the arms (D29).
- **Recordings:** a `log_window` (telemetry, health and event logs around the fault) and an
  `agent_trace` (the heal-tool calls and notes during it; no model messages), both JSONL.

Ids are UUIDv5s of podd's own ids, so a resent report or recording is harmless (D8).
"""

from __future__ import annotations

import hashlib
import json
import math
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from edge_bridge import __version__
from edge_bridge.faults import Fault, Reason
from romulus_contracts import (
    CONTRACT_VERSION,
    Agent,
    CheckIn,
    IncidentReport,
    Outcome,
    RecordingMeta,
    Running,
)

NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "romulus:edge-bridge")
PRE_S = 30.0
"""Evidence starts this long before the fault opened."""
POST_S = 10.0
"""…and ends this long after it was over. Keep it under `Timing.settle_s`, so the logs
already hold it when a resolved fault is reported."""
MAX_LOG_LINES = 20_000
MAX_SIGNAL_SAMPLES = 120
LOG_TAIL = 50
MAX_TEXT = 64_000
LINK_FAILING = {"down", "silent", "garbled"}
TRACE_SOURCE = (
    "healer audit.jsonl and agent notes: tool calls with their arguments; "
    "no model messages or tool results"
)


# -- ids, times, builds ------------------------------------------------------------------


def incident_uuid(device_id: str, fault_key: str) -> uuid.UUID:
    return uuid.uuid5(NAMESPACE, f"{device_id}/{fault_key}")


def recording_uuid(incident_id: uuid.UUID, kind: str) -> uuid.UUID:
    return uuid.uuid5(incident_id, kind)


def utc(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, UTC)


def normalize_crc(crc: Any) -> str | None:
    """`0x32acfdaf`, `32ACFDAF` → `32ACFDAF`, the image registry's key (images.py)."""
    if crc is None or isinstance(crc, bool):
        return None
    if isinstance(crc, int):
        return f"{crc:08X}"
    text = str(crc).strip().upper().removeprefix("0X")
    return text.zfill(8) if text else None


def mcu_build(build_id: str | None, crc: Any) -> str | None:
    """`v1.1@32ACFDAF`: the version from a build id (`pod v1.1 2026-…`) plus the CRC."""
    crc = normalize_crc(crc)
    parts = (build_id or "").split()
    version = parts[1] if len(parts) > 1 else None
    if version and crc:
        return f"{version}@{crc}"
    return crc or version


def build_from_boot(rec: dict[str, Any] | None) -> str | None:
    """The MCU build from a pod boot record (`E ev=boot build="…" crc=0x…`)."""
    if not rec or rec.get("ev") != "boot":
        return None
    return mcu_build(rec.get("build"), rec.get("crc"))


def build_from_deployment(rec: dict[str, Any] | None) -> str | None:
    """The MCU build from a `deployments.jsonl` record (`crc`, `build_id`)."""
    if not rec:
        return None
    return mcu_build(rec.get("build_id"), rec.get("crc"))


# -- check-ins ---------------------------------------------------------------------------


def health(status: dict[str, Any] | None) -> tuple[str, dict[str, Any]]:
    """Coarse health and its detail from podd's `status` (None: podd is unreachable)."""
    if status is None:
        return "failing", {"check": "podd_unreachable", "source": "edge_bridge"}
    link = (status.get("link") or {}).get("state")
    opened = status.get("open_incidents") or []
    detail = {
        "link": link,
        "open_incidents": [
            {"id": i.get("id"), "kind": i.get("kind"), "severity": i.get("severity")}
            for i in opened
        ],
    }
    severities = {i.get("severity") for i in opened}
    if link in LINK_FAILING or "critical" in severities:
        return "failing", detail
    if "warn" in severities:
        return "degraded", detail
    return "ok", detail


def metrics(status: dict[str, Any] | None, stats: dict[str, Any] | None) -> dict[str, float]:
    """Numbers worth keeping as history, named `pod_*`. Non-numbers are left out."""
    m: dict[str, Any] = {}
    if status:
        link = status.get("link") or {}
        m["pod_link_bytes_per_s"] = link.get("bytes_per_s")
        m["pod_link_valid_ratio"] = link.get("valid_ratio")
        m["pod_open_incidents"] = len(status.get("open_incidents") or [])
        for key, value in (status.get("last_health") or {}).items():
            if key not in ("t", "ts", "type"):
                m[f"pod_{key}"] = value
    if stats:
        accel, distance = stats.get("accel") or {}, stats.get("distance") or {}
        m["pod_acc_hz"] = accel.get("rate_hz")
        m["pod_acc_mag_mg"] = (accel.get("magnitude_mg") or {}).get("mean")
        m["pod_distance_hz"] = distance.get("rate_hz")
        m["pod_distance_mm"] = (distance.get("mm_ok") or {}).get("mean")
    return {k: float(v) for k, v in m.items() if _is_number(v)}


def checkin(
    status: dict[str, Any] | None, stats: dict[str, Any] | None, build: str | None
) -> CheckIn:
    """A check-in for the pod. It is real hardware, so it says `kind: real` (K17)."""
    state, detail = health(status)
    return CheckIn(
        running=Running(mcu_build=build),
        health=state,
        metrics=metrics(status, stats),
        health_detail=detail,
        contract_version=CONTRACT_VERSION,
        kind="real",
    )


# -- incidents ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Evidence:
    """What the healer recorded around one fault (gathered by the bridge from podd.py)."""

    lo: float
    hi: float
    logs: dict[str, list[dict[str, Any]]]
    """`telemetry`, `health`, `events` → their records in `[lo, hi]`."""
    audit: list[dict[str, Any]]
    notifications: list[dict[str, Any]]
    deployments: list[dict[str, Any]]
    risks: dict[str, str]
    """Heal tool → risk level, from `tools.json`."""
    images: dict[str, dict[str, Any]]
    """CRC → registry entry, for the images flashed in the window."""
    workspace_log: str | None = None


@dataclass(frozen=True)
class Report:
    incident: IncidentReport
    recordings: list[tuple[RecordingMeta, bytes]]


def window(fault: Fault, now: float) -> tuple[float, float]:
    """The evidence window: from `PRE_S` before the fault to `POST_S` after it (not past now)."""
    return fault.start - PRE_S, min(fault.end(now) + POST_S, now)


def report(device_id: str, fault: Fault, reason: Reason, ev: Evidence) -> Report:
    """The fault's incident report and its recordings, ready for the outbox."""
    incident_id = incident_uuid(device_id, fault.key)
    running = Running(mcu_build=fault.mcu_build)
    during = _During(fault, ev)
    recordings = [
        r
        for r in (
            _log_window(incident_id, fault, ev, running),
            _agent_trace(incident_id, fault, ev, during, running),
        )
        if r is not None
    ]
    primary = fault.primary
    others = list(dict.fromkeys(i.kind for i in fault.incidents if i is not primary))
    summary = primary.summary + (f" (also: {', '.join(others)})" if others else "")
    extras: dict[str, Any] = {
        "source": "podd",
        "bridge": {"reason": reason, "version": __version__},
        "podd_incidents": [asdict(i) for i in fault.incidents],
        "deployments": during.deployments,
        "notifications": during.notifications,
    }
    patch = _firmware_patch(during, ev)
    if patch:
        extras["firmware_patch"] = patch
    incident = IncidentReport(
        incident_id=incident_id,
        occurred_at=utc(fault.start),
        kind=primary.kind,
        summary=summary,
        running=running,
        agent=_agent(during),
        overlay=None,
        outcome=_outcome(reason, during),
        signals=_signals(ev),
        recordings=[meta.recording_id for meta, _ in recordings],
        log_tail=ev.logs.get("events", [])[-LOG_TAIL:],
        contract_version=CONTRACT_VERSION,
        **extras,
    )
    return Report(incident, recordings)


class _During:
    """The evidence that falls inside the fault itself (from its start to the window's end)."""

    def __init__(self, fault: Fault, ev: Evidence) -> None:
        def inside(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
            return [r for r in rows if fault.start <= float(r["ts"]) <= ev.hi]

        self.risks = ev.risks
        self.images = ev.images
        self.calls = inside(ev.audit)
        self.notes = [e for e in inside(ev.logs.get("events", [])) if e.get("ev") == "agent_note"]
        self.notifications = inside(ev.notifications)
        self.deployments = inside(ev.deployments)
        self.acted = [c for c in self.calls if c.get("ok") and self.risk(c) != "read"]
        self.flashed = [d for d in self.deployments if d.get("result") == "ok"]
        self.asks = [n for n in self.notifications if str(n.get("actions") or "").strip()]

    def risk(self, call: dict[str, Any]) -> str:
        """Unknown tools count as actions."""
        return self.risks.get(str(call.get("tool")), "action")

    def built_here(self, deployment: dict[str, Any]) -> bool:
        image = self.images.get(normalize_crc(deployment.get("crc")) or "")
        return bool(image) and image.get("origin") == "workspace"


def _outcome(reason: Reason, d: _During) -> Outcome:
    """How it ended. `ok` in the audit means the call didn't raise, not that it worked, so
    a flash counts only when `deployments.jsonl` says it verified."""
    runtime = [c for c in d.acted if d.risk(c) == "runtime"]
    changed = [c for c in d.acted if d.risk(c) != "notify"]
    if d.asks:
        cause = "hardware"
    elif d.flashed or changed:
        cause = "software"
    else:
        cause = "unknown"

    action: str | None = None
    category: str | None = None
    if d.flashed:
        fixed = d.built_here(d.flashed[-1])
        action, category = ("fw_flash", "fix-code") if fixed else ("fw_rollback", "ops")
    elif runtime:
        last = runtime[-1]
        line = (last.get("args") or {}).get("line") if last.get("tool") == "pod_cmd" else None
        action, category = (f"pod_cmd: {line}" if line else str(last.get("tool"))), "ops"
    elif d.asks:
        action = "notify_human"
    if cause == "hardware":
        category = None  # the work order carries it; the pod has no `park`
    return Outcome(
        resolved=reason == "resolved",
        resolving_action=action,
        suspected_cause=cause,
        category=category,
    )


def _agent(d: _During) -> Agent | None:
    if not d.calls and not d.notes:
        return None
    return Agent(
        reasoning="\n".join(str(n.get("text", "")) for n in d.notes),
        actions=[_action(c) for c in d.acted],
        tool_calls=len(d.calls),
    )


def _action(call: dict[str, Any]) -> dict[str, Any]:
    out = {"tool": call.get("tool"), "args": call.get("args") or {}, "ok": call.get("ok")}
    if call.get("error"):
        out["error"] = call["error"]
    return out | {"at": utc(float(call["ts"])).isoformat()}


def _firmware_patch(d: _During, ev: Evidence) -> dict[str, Any] | None:
    """What the healer changed, as evidence: the image it flashed and that image's source
    diff, plus the workspace commits made during the fault."""
    out: dict[str, Any] = {}
    if d.flashed:
        image = d.images.get(normalize_crc(d.flashed[-1].get("crc")) or "")
        if image:
            keys = ("crc", "build_id", "version", "status", "origin", "source_rev")
            out["image"] = {k: image.get(k) for k in keys}
            if image.get("source_patch"):
                out["uncommitted_at_build"] = _clip(image["source_patch"])
    if ev.workspace_log:
        out["workspace_commits"] = _clip(ev.workspace_log)
    return out or None


def _signals(ev: Evidence) -> dict[str, list[float]]:
    """A small inline sample around the fault (APIs §2.5), named like the metrics."""
    telemetry = ev.logs.get("telemetry", [])
    series: dict[str, list[float]] = {
        "pod_distance_mm": [
            float(r["mm"])
            for r in telemetry
            if r.get("type") == "D" and r.get("st") == "ok" and _is_number(r.get("mm"))
        ],
        "pod_acc_mag_mg": [
            round(math.sqrt(r["x"] ** 2 + r["y"] ** 2 + r["z"] ** 2), 1)
            for r in telemetry
            if r.get("type") == "A" and all(_is_number(r.get(k)) for k in "xyz")
        ],
    }
    for rec in ev.logs.get("health", []):
        for key, value in rec.items():
            if key not in ("t", "ts", "type") and _is_number(value):
                series.setdefault(f"pod_{key}", []).append(float(value))
    return {k: _sample(v, MAX_SIGNAL_SAMPLES) for k, v in series.items() if v}


# -- recordings --------------------------------------------------------------------------


def _log_window(
    incident_id: uuid.UUID, fault: Fault, ev: Evidence, running: Running
) -> tuple[RecordingMeta, bytes] | None:
    rows = sorted(
        ({"log": name, **r} for name, recs in ev.logs.items() for r in recs),
        key=lambda r: float(r["ts"]),
    )
    if not rows:
        return None
    data = _jsonl(_elide(rows, MAX_LOG_LINES))
    streams = sorted(name for name, recs in ev.logs.items() if recs)
    return _meta(incident_id, "log_window", data, (ev.lo, ev.hi), running, fault, streams), data


def _agent_trace(
    incident_id: uuid.UUID, fault: Fault, ev: Evidence, d: _During, running: Running
) -> tuple[RecordingMeta, bytes] | None:
    if not d.calls:
        return None
    rows = sorted(
        [{"source": "audit", **c} for c in d.calls] + [{"source": "note", **n} for n in d.notes],
        key=lambda r: float(r["ts"]),
    )
    data = _jsonl(rows)
    span = (fault.start, ev.hi)
    return _meta(incident_id, "agent_trace", data, span, running, fault, ["audit", "notes"]), data


def _meta(
    incident_id: uuid.UUID,
    kind: str,
    data: bytes,
    span: tuple[float, float],
    running: Running,
    fault: Fault,
    streams: list[str],
) -> RecordingMeta:
    extras = {"source": "podd"} | ({"trace_source": TRACE_SOURCE} if kind == "agent_trace" else {})
    return RecordingMeta(
        recording_id=recording_uuid(incident_id, kind),
        kind=kind,
        format="jsonl",
        sha256=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data),
        started_at=utc(span[0]),
        ended_at=utc(span[1]),
        running=running,
        incident_id=incident_id,
        streams=streams,
        labels=list(dict.fromkeys(i.kind for i in fault.incidents)),
        contract_version=CONTRACT_VERSION,
        **extras,
    )


# -- helpers -----------------------------------------------------------------------------


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)


def _sample(values: list[float], limit: int) -> list[float]:
    """At most `limit` values, evenly spaced, first and last kept."""
    if len(values) <= limit:
        return values
    step = (len(values) - 1) / (limit - 1)
    return [values[round(i * step)] for i in range(limit)]


def _elide(rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """Keep the first and last `limit // 2` rows and say how many were left out."""
    if len(rows) <= limit:
        return rows
    keep = limit // 2
    gap = {
        "log": "edge_bridge",
        "ts": rows[keep]["ts"],
        "ev": "elided",
        "count": len(rows) - 2 * keep,
    }
    return rows[:keep] + [gap] + rows[-keep:]


def _jsonl(rows: list[dict[str, Any]]) -> bytes:
    return b"".join(
        json.dumps(r, separators=(",", ":"), default=str).encode() + b"\n" for r in rows
    )


def _clip(text: str, limit: int = MAX_TEXT) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n… clipped {len(text) - limit} characters\n"
