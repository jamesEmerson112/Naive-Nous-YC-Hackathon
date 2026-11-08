"""Small synthetic evidence files for fake incidents (telemetry §2, build plan M4).

- `log_window`: a real MCAP file (written with the `mcap` library), one JSON message per
  log line on channel `/log`.
- `agent_trace`: JSONL, one message per line, like a harness session export.
- `incident_window`: a *placeholder* tar labeled `lerobot_v3_tar`. Real LeRobot datasets
  come from the arm; generating them needs the heavy `lerobot` package.
"""

from __future__ import annotations

import hashlib
import io
import json
import tarfile
import uuid
from datetime import datetime, timedelta

from mcap.writer import Writer
from romulus_contracts import RecordingMeta, Running
from romulus_contracts.telemetry import FLEET_FEATURES, FLEET_SCHEMA_VERSION

Recording = tuple[RecordingMeta, bytes]


def _meta(kind: str, fmt: str, data: bytes, started_at: datetime, ended_at: datetime, **fields):
    return RecordingMeta(
        recording_id=uuid.uuid4(),
        kind=kind,
        format=fmt,
        sha256=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data),
        started_at=started_at,
        ended_at=ended_at,
        **fields,
    )


def log_window(
    lines: list[str],
    at: datetime,
    incident_id: uuid.UUID,
    running: Running | None,
    labels: list[str],
    overlay_id: str | None = None,
) -> Recording:
    buf = io.BytesIO()
    writer = Writer(buf)
    writer.start(profile="", library="fake_device")
    schema = writer.register_schema(
        name="Log",
        encoding="jsonschema",
        data=json.dumps({"type": "object", "properties": {"line": {"type": "string"}}}).encode(),
    )
    channel = writer.register_channel(topic="/log", message_encoding="json", schema_id=schema)
    start_ns = int((at - timedelta(seconds=30)).timestamp() * 1e9)
    for i, line in enumerate(lines):
        t = start_ns + i * 100_000_000
        writer.add_message(
            channel, log_time=t, publish_time=t, data=json.dumps({"line": line}).encode()
        )
    writer.finish()
    data = buf.getvalue()
    meta = _meta(
        "log_window",
        "mcap",
        data,
        at - timedelta(seconds=30),
        at + timedelta(seconds=30),
        running=running,
        incident_id=incident_id,
        overlay_id=overlay_id,
        streams=["/log"],
        labels=labels,
    )
    return meta, data


def agent_trace(
    reasoning: str,
    actions: list[str],
    at: datetime,
    incident_id: uuid.UUID,
    running: Running | None,
    labels: list[str],
    overlay_id: str | None = None,
) -> Recording:
    messages = [
        {"role": "system", "content": "You are the on-device healer."},
        {"role": "user", "content": "Health check failed; diagnose and fix."},
        *({"role": "tool", "name": "action", "content": a} for a in actions),
        {"role": "assistant", "content": reasoning},
    ]
    data = "".join(json.dumps(m) + "\n" for m in messages).encode()
    meta = _meta(
        "agent_trace",
        "jsonl",
        data,
        at,
        at + timedelta(seconds=len(actions) * 5),
        running=running,
        incident_id=incident_id,
        overlay_id=overlay_id,
        labels=labels,
    )
    return meta, data


def incident_window(
    at: datetime,
    incident_id: uuid.UUID,
    running: Running | None,
    labels: list[str],
    overlay_id: str | None = None,
) -> Recording:
    info = {
        "placeholder": True,
        "note": "Synthetic stand-in from tools/fake_device, not a real LeRobotDataset.",
        "codebase_version": "v3.0",
        "features": {name: {"dtype": d, "shape": s} for name, (d, s) in FLEET_FEATURES.items()},
    }
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        raw = json.dumps(info, indent=2).encode()
        member = tarfile.TarInfo("meta/info.json")
        member.size = len(raw)
        member.mtime = int(at.timestamp())
        tar.addfile(member, io.BytesIO(raw))
    data = buf.getvalue()
    meta = _meta(
        "incident_window",
        "lerobot_v3_tar",
        data,
        at - timedelta(seconds=30),
        at + timedelta(seconds=30),
        running=running,
        incident_id=incident_id,
        overlay_id=overlay_id,
        streams=list(FLEET_FEATURES),
        schema_version=FLEET_SCHEMA_VERSION,
        labels=labels,
    )
    return meta, data
