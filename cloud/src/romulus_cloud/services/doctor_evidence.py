"""The Fleet Doctor's case file: what a consult knows about an incident (interfaces.md §6.4).

**v0 (PR 4):** the incident's own fields plus its log tail (`Incident.raw["log_tail"]`),
what the healer already tried (its actions) and how earlier consults on the same incident
ended. A note says what isn't wired yet: the agent trace, the code at the base rev and
MCAP logs (PR 7 adds the first two; MCAP stays "not wired", `mcap` isn't a dependency).

Everything here comes from the device, so the case file says `untrusted: True` and
`domain.doctor.diagnose_prompt` puts all of it inside the untrusted evidence block. Reads
only; nothing here writes or commits.
"""

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from romulus_cloud.adapters.blobstore import BlobStore
from romulus_cloud.models import DoctorConsult, Incident
from romulus_cloud.settings import Settings

NOT_WIRED = "trace, code, mcap: not wired"


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def case_file(
    session: Session,
    store: BlobStore | None,
    consult: DoctorConsult,
    incident: Incident,
    *,
    settings: Settings,
) -> dict[str, Any]:
    """A plain, JSON-safe dict (stored on the consult, sent to the diagnose job).

    `store` and `settings` are for PR 7's readers (traces through the blob store, code from
    the registry, byte caps); v0 doesn't use them.
    """
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
    tried: list[Any] = [{"by": "healer", "action": a} for a in _list(incident.actions)]
    tried += [
        {
            "by": "fleet-doctor",
            "attempt": c.attempt,
            "outcome": c.outcome,
            "proposal_id": (c.raw or {}).get("proposal_id"),
        }
        for c in earlier
    ]
    return {
        "incident": {
            "id": incident.id,
            "kind": incident.kind,
            "summary": incident.summary,
            "tier": raw.get("tier"),
            "signals": incident.signals or {},
            "outcome": raw.get("outcome"),
            "running": running,
            "agent_reasoning": incident.agent_reasoning,
            "consult": raw.get("consult"),
        },
        "log_tail": _list(raw.get("log_tail")),
        "trace_tail": [],
        "code": {"rev": running.get("code_rev"), "files": []},
        "tried": tried,
        "untrusted": True,
        "notes": [NOT_WIRED],
    }
