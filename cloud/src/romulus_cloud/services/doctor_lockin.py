"""Lock-in of a held Fleet Doctor overlay: no second Coder pass (architecture §5.4, D24).

The Doctor already drafted and checked its patch, so when the overlay it became is held, the
batch uses that patch as the draft instead of calling the Coder (`proposal_draft`). Nothing
downstream changes: the same code version, firmware manifest and candidate events; a draft
in the coding-agent lane still gets `patch_refusal` and the gate's own replay
(`coder_runs.replay_code_patch`), which never trusts a draft's `passed`; code candidates
still stop at `awaiting_canary` (B7). It skips no Coder budget either, since nothing drafts.

Fail closed: `overlays.raw` is what the device sent, so a `proposal_id` there is only a
claim. The skip happens only for a `doctor_proposals` row in the same fleet, issued to the
same device, `applied` or `held`, whose `code_patch` has the same sha256 as the overlay's.
Anything else records one `doctor.declined` (`stage = "lock_in"`) and the overlay goes
through the Coder like any device patch.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from romulus_cloud import events
from romulus_cloud.adapters.coder import CodeDraft
from romulus_cloud.models import DoctorProposal, Incident, Overlay

ACTOR = "agent:batch"
SOURCE = "doctor"  # the candidate's data.source when its code is a Doctor proposal
STAGE = "lock_in"
LOCKED_IN_STATUSES = ("applied", "held")


@dataclass(frozen=True)
class DoctorDraft(CodeDraft):
    """A verified Doctor proposal standing in for the Coder's draft."""

    proposal_id: str = ""

    @property
    def key(self) -> str:
        """Its key in the batch's drafts: one part (and one candidate) per proposal."""
        return f"doctor.proposal:{self.proposal_id}"


def patch_sha256(patch: str | None) -> str:
    return hashlib.sha256((patch or "").encode("utf-8")).hexdigest()


def proposal_draft(session: Session, overlay: Overlay, now: datetime) -> DoctorDraft | None:
    """The overlay's verified Doctor proposal as a draft, or None (use the Coder). An overlay
    that names no proposal returns None quietly; one that names a proposal that doesn't check
    out also records why, once per overlay."""
    pid = (overlay.raw or {}).get("proposal_id")
    if pid is None or pid == "":
        return None
    proposal = session.get(DoctorProposal, pid) if isinstance(pid, str) else None
    problem = _problem(overlay, pid, proposal)
    if problem:
        _decline(session, overlay, pid, problem, now)
        return None
    assert proposal is not None
    checks = proposal.checks or {}
    stub = ", stub check" if checks.get("stub") else ""
    return DoctorDraft(
        diff=overlay.code_patch or "",
        test_output=str(checks.get("summary") or "checked by the Fleet Doctor"),
        passed=True,
        notes=(
            f"Checked by the Fleet Doctor (proposal {proposal.id}, consult "
            f"{proposal.consult_id}{stub}); no second Coder pass (D24)."
        ),
        proposal_id=proposal.id,
    )


def _problem(overlay: Overlay, pid: Any, proposal: DoctorProposal | None) -> str | None:
    if not isinstance(pid, str):
        return f"proposal_id {pid!r} is not a string"
    if proposal is None or proposal.fleet_id != overlay.fleet_id:
        return f"there is no proposal {pid} in this fleet"
    if proposal.device_id != overlay.device_id:
        return f"proposal {pid} was issued to {proposal.device_id}, not {overlay.device_id}"
    if proposal.status not in LOCKED_IN_STATUSES:
        return f"proposal {pid} is {proposal.status}, not applied or held"
    mine, theirs = patch_sha256(overlay.code_patch), patch_sha256(proposal.code_patch)
    if mine != theirs:
        return (
            f"the overlay's code_patch (sha256 {mine[:12]}) is not proposal {pid}'s "
            f"(sha256 {theirs[:12]})"
        )
    return None


def _decline(session: Session, ov: Overlay, pid: Any, problem: str, now: datetime) -> None:
    refs: dict[str, Any] = {"device": ov.device_id, "overlay": ov.id}
    past = events.find(session, refs, kinds=["doctor.declined"])
    if any((e.data or {}).get("stage") == STAGE for e in past):
        return  # a held overlay is looked at every batch run; say it once
    data: dict[str, Any] = {
        "reason": f"lock-in: proposal not verified ({problem})",
        "stage": STAGE,
        "proposal_id": pid if isinstance(pid, str) else repr(pid),
    }
    if ov.incident_id:
        refs["incident"] = ov.incident_id
        incident = session.get(Incident, ov.incident_id)
        if incident is not None:
            data["incident_kind"] = incident.kind
    events.record(
        session,
        ACTOR,
        "agent",
        "doctor.declined",
        refs,
        f"Overlay {ov.id} from {ov.device_id} names a Fleet Doctor proposal, but {problem}; "
        "it is locked in like any device patch, through the Coder, with no Doctor shortcut.",
        data=data,
        ts=now,
        fleet_id=ov.fleet_id,
    )


def candidate_data(proposals: list[str], *, whole: bool) -> dict[str, Any]:
    """What a code candidate's `data` says about Doctor proposals: `source` and `proposal_id`
    when its code is exactly one verified proposal (`whole`); in a pass-through fold that
    mixes proposals with device patches, just their ids."""
    proposals = list(dict.fromkeys(proposals))
    if not proposals:
        return {}
    if whole and len(proposals) == 1:
        return {"source": SOURCE, "proposal_id": proposals[0]}
    return {"proposal_ids": proposals}
