"""A closed consult's trace (interfaces.md §9): one `Recording(kind="doctor_consult",
format="jsonl")` linked to the incident, so dataset exports (`traces_jsonl`) carry the
Doctor's work next to the healer's `agent_trace` recordings.

One JSON message per line, in the agent-trace shape `domain.datasets.sharegpt` reads
(`role`, `name`, `content`):

1. `system` / `consult`: ids, attempt, origin, trigger, base, times, cost;
2. `user` / `case_file`: the evidence (untrusted, as stored on the consult);
3. `assistant` / `ledger`: the hypotheses;
4. `assistant` / `draft` and `tool` / `checks`, when the consult got that far;
5. `system` / `outcome`: how it closed and why.

No secrets: the LLM and Naïve keys are scrubbed from the text before it is stored (the
export's own redaction runs on top). The bytes go into the blob store under their sha256
with a `blobs` row, so snapshots include them. The caller's `doctor.consult_closed` event
names the recording (`refs.recording`) in the same transaction; nothing here commits.
"""

import hashlib
import io
import json
import logging
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from romulus_cloud.adapters.blobstore import BlobError, BlobStore
from romulus_cloud.adapters.coder import redact
from romulus_cloud.models import Blob, DoctorConsult, Incident, Recording
from romulus_cloud.settings import Settings

log = logging.getLogger(__name__)

KIND = "doctor_consult"
FORMAT = "jsonl"
SCHEMA = "doctor_consult/1"
MEDIA_TYPE = "application/jsonl"


def _version(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def secrets(settings: Settings) -> list[str]:
    """The keys that must never land in a stored trace."""
    keys = [settings.llm_api_key.get_secret_value(), settings.naive_api_key.get_secret_value()]
    return [k for k in keys if k]


def lines(
    consult: DoctorConsult, incident: Incident | None, outcome: str, why: str, now: datetime
) -> list[dict[str, Any]]:
    """The trace's messages (plain dicts, before scrubbing)."""
    raw = consult.raw or {}
    out: list[dict[str, Any]] = [
        {
            "role": "system",
            "name": "consult",
            "content": {
                "consult_id": consult.id,
                "incident_id": consult.incident_id,
                "device_id": consult.device_id,
                "incident_kind": incident.kind if incident else None,
                "attempt": consult.attempt,
                "origin": consult.origin,
                "trigger": consult.trigger,
                "base": (incident.running if incident else None) or {},
                "opened_at": consult.created_at.isoformat() if consult.created_at else None,
                "closed_at": now.isoformat(),
                "cost_usd": consult.cost_usd,
                "retry_of": raw.get("retry_of"),
            },
        },
        {"role": "user", "name": "case_file", "content": consult.case_file or {}},
        {"role": "assistant", "name": "ledger", "content": {"hypotheses": consult.ledger or []}},
    ]
    if consult.draft:
        out.append({"role": "assistant", "name": "draft", "content": consult.draft})
    if consult.checks:
        out.append({"role": "tool", "name": "checks", "content": consult.checks})
    out.append(
        {
            "role": "system",
            "name": "outcome",
            "content": {"outcome": outcome, "why": why, "proposal_id": raw.get("proposal_id")},
        }
    )
    return out


def write(
    session: Session,
    store: BlobStore,
    consult: DoctorConsult,
    incident: Incident | None,
    outcome: str,
    why: str,
    now: datetime,
    *,
    settings: Settings,
) -> Recording | None:
    """Store the consult's trace and add its recording row. None (and a log line) if the
    blob store can't take the bytes: a full disk must not stop a consult from closing."""
    text = "".join(
        json.dumps(m, sort_keys=True, default=str, ensure_ascii=False) + "\n"
        for m in lines(consult, incident, outcome, why, now)
    )
    data = redact(text, secrets(settings)).encode("utf-8")
    sha = hashlib.sha256(data).hexdigest()
    try:
        size = store.put(sha, io.BytesIO(data))
    except (BlobError, OSError) as exc:
        log.warning("doctor trace for %s not stored: %s", consult.id, type(exc).__name__)
        return None
    session.execute(
        pg_insert(Blob)
        .values(
            sha256=sha,
            fleet_id=settings.fleet_id,
            size_bytes=size,
            media_type=MEDIA_TYPE,
            path=sha,
        )
        .on_conflict_do_nothing(index_elements=["sha256"])
    )
    running = (incident.running if incident else None) or {}
    rec = Recording(
        id=str(uuid.uuid4()),
        fleet_id=settings.fleet_id,
        device_id=consult.device_id,
        kind=KIND,
        format=FORMAT,
        sha256=sha,
        size_bytes=size,
        started_at=consult.created_at,
        ended_at=now,
        received_at=now,
        firmware=_version(running.get("firmware")),
        harness=_version(running.get("harness")),
        overlay_id=None,
        incident_id=consult.incident_id,
        episode=None,
        streams=[],
        schema_version=SCHEMA,
        raw={"source": "fleet-doctor", "consult": consult.id, "outcome": outcome},
    )
    session.add(rec)
    session.flush()
    return rec
