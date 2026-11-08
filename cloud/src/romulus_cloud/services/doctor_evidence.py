"""The Fleet Doctor's case file: what a consult knows about an incident (interfaces.md §6.4).

- **Incident:** its own fields and its log tail (`Incident.raw["log_tail"]`).
- **Trace tail (PR 7):** the last DOCTOR_TRACE_TAIL_LINES lines (at most
  DOCTOR_TRACE_TAIL_BYTES) of the incident's `agent_trace` recordings whose bytes are stored,
  newest recordings last, read from the end of each blob through `BlobStore.get`.
- **Code (PR 7):** the base `running.code_rev` from the registry (`doctor_code.package`,
  `verify/` removed), its file list, and excerpts of the files the log, trace or healer
  actions name (paths relative to CODE_ROOT, with or without a `device/` prefix), capped at
  DOCTOR_EVIDENCE_FILE_BYTES each and DOCTOR_EVIDENCE_CODE_BYTES in all.
- **Tried:** what the healer already did (its actions) and how earlier consults ended.
- **MCAP** stays a labeled "not wired" (`mcap` isn't a dependency).

Everything here comes from the device, so the case file says `untrusted: True`, and the
prompt builders (`domain.doctor.diagnose_prompt`, `draft_task`) put all of it, notes
included, inside the untrusted evidence block. The held-out `verify/` scripts never appear.
Reads only; nothing here writes or commits.
"""

import io
import logging
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from romulus_cloud.adapters.blobstore import BlobError, BlobStore
from romulus_cloud.domain import doctor as rules
from romulus_cloud.models import Blob, DoctorConsult, Incident, Recording
from romulus_cloud.services import doctor_code
from romulus_cloud.settings import Settings

log = logging.getLogger(__name__)

TRACE_KIND = "agent_trace"
MAX_TRACES = 3  # newest agent-trace recordings read per incident
MAX_TREE = 40  # paths listed from the base package (the prompt keeps 40 list entries)
MCAP_NOTE = "mcap: not wired"


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _read_tail(store: BlobStore, sha256: str, max_bytes: int) -> tuple[bytes, bool]:
    """The last `max_bytes` of a blob (bytes, whether the read started mid-file). Seeks to
    the tail, so a 200 MB trace costs `max_bytes` of reading; a store that can't seek is
    read whole."""
    if max_bytes <= 0:
        return b"", False
    fh = store.get(sha256)
    try:
        try:
            start = max(store.size(sha256) - max_bytes, 0)
            fh.seek(start)
        except (OSError, AttributeError, ValueError, io.UnsupportedOperation):
            start = 0
        data = fh.read()
    finally:
        fh.close()
    if len(data) > max_bytes:
        return data[-max_bytes:], True
    return data, start > 0


def trace_tail(
    session: Session, store: BlobStore | None, incident: Incident, *, settings: Settings
) -> tuple[list[str], list[str], list[str]]:
    """(the last lines of the incident's agent traces, the recording ids read, notes)."""
    if store is None:
        return [], [], ["trace: no blob store"]
    stored = select(Blob.sha256).where(Blob.sha256 == Recording.sha256)
    recs = list(
        session.scalars(
            select(Recording)
            .where(
                Recording.fleet_id == incident.fleet_id,
                Recording.incident_id == incident.id,
                Recording.kind == TRACE_KIND,
                stored.exists(),
            )
            .order_by(
                func.coalesce(Recording.started_at, Recording.received_at).desc(), Recording.id
            )
            .limit(MAX_TRACES)
        )
    )[::-1]
    if not recs:
        return [], [], ["trace: no agent_trace recording stored for this incident"]
    max_lines = max(settings.doctor_trace_tail_lines, 0)
    max_bytes = max(settings.doctor_trace_tail_bytes, 0)
    lines: list[str] = []
    read: list[str] = []
    notes: list[str] = []
    for rec in recs:
        try:
            data, partial = _read_tail(store, rec.sha256, max_bytes)
        except (BlobError, OSError) as exc:
            notes.append(f"trace {rec.id}: can't read its bytes ({type(exc).__name__})")
            continue
        lines += rules.tail_lines(data, max_lines=max_lines, max_bytes=max_bytes, partial=partial)
        read.append(rec.id)
    # Newest last, then the same caps over all of them together.
    joined = "\n".join(lines).encode("utf-8")
    lines = rules.tail_lines(joined, max_lines=max_lines, max_bytes=max_bytes)
    return lines, read, notes


def code_evidence(
    session: Session,
    store: BlobStore | None,
    rev: str | None,
    texts: list[Any],
    *,
    settings: Settings,
) -> tuple[dict[str, Any], list[str]]:
    """(`{rev, files: [{path, excerpt, size_bytes, truncated}], tree}`, notes)."""
    code: dict[str, Any] = {"rev": rev, "files": [], "tree": []}
    if store is None:
        return code, ["code: no blob store"]
    try:
        files = doctor_code.package(session, store, rev, settings.fleet_id)
    except doctor_code.NoPackage as exc:
        return code, [f"code: {exc}"]
    named = rules.named_paths(texts, files)
    code["files"] = rules.code_excerpts(
        files,
        named,
        file_bytes=settings.doctor_evidence_file_bytes,
        total_bytes=settings.doctor_evidence_code_bytes,
    )
    code["tree"] = sorted(files)[:MAX_TREE]
    notes = [] if named else ["code: the log and trace name no file in the package"]
    return code, notes


def case_file(
    session: Session,
    store: BlobStore | None,
    consult: DoctorConsult,
    incident: Incident,
    *,
    settings: Settings,
) -> dict[str, Any]:
    """A plain, JSON-safe dict (stored on the consult, sent to the diagnose and draft jobs)."""
    raw = incident.raw if isinstance(incident.raw, dict) else {}
    running = incident.running or {}
    earlier = session.scalars(
        select(DoctorConsult)
        .where(
            DoctorConsult.incident_id == incident.id,
            DoctorConsult.attempt < consult.attempt,
        )
        .order_by(DoctorConsult.attempt)
    ).all()
    # Held-out checks stay hidden: any log, trace or action line naming a verify/ path is
    # withheld before it reaches a prompt, the stored case file or the consult's trace.
    actions, hidden_actions = rules.withhold_verify(_list(incident.actions))
    reasoning, hidden_reasoning = rules.withhold_verify_text(incident.agent_reasoning)
    summary, hidden_summary = rules.withhold_verify_text(incident.summary)
    tried: list[Any] = [{"by": "healer", "action": a} for a in actions]
    tried += [
        {
            "by": "fleet-doctor",
            "attempt": c.attempt,
            "outcome": c.outcome,
            "proposal_id": (c.raw or {}).get("proposal_id"),
        }
        for c in earlier
    ]
    log_tail, hidden_log = rules.withhold_verify(_list(raw.get("log_tail")))
    traces, trace_ids, trace_notes = trace_tail(session, store, incident, settings=settings)
    traces, hidden_trace = rules.withhold_verify(traces)
    texts = [*log_tail, *traces, *actions, reasoning or "", summary or ""]
    code, code_notes = code_evidence(
        session, store, running.get("code_rev"), texts, settings=settings
    )
    hidden = hidden_log + hidden_trace + hidden_actions + hidden_reasoning + hidden_summary
    verify_notes = (
        [f"verify: {hidden} log, trace or healer lines withheld (they name held-out verify/ paths)"]
        if hidden
        else []
    )
    return {
        "incident": {
            "id": incident.id,
            "kind": incident.kind,
            "summary": summary,
            "tier": raw.get("tier"),
            "signals": incident.signals or {},
            "outcome": raw.get("outcome"),
            "running": running,
            "agent_reasoning": reasoning,
            "consult": raw.get("consult"),
        },
        "log_tail": log_tail,
        "trace_tail": traces,
        "trace_recordings": trace_ids,
        "code": code,
        "tried": tried,
        "untrusted": True,
        "notes": [*trace_notes, *code_notes, *verify_notes, MCAP_NOTE],
    }
