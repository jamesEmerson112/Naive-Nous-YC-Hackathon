"""Lock-in of a held Fleet Doctor overlay: no second Coder pass (architecture §5.4, D24).

The Doctor already drafted and checked its patch, so when the overlay it became is held, the
batch uses that patch as the draft instead of calling the Coder (`proposal_draft`). Nothing
downstream changes: the same code version (`source = "doctor"`, which the gate replays like
`coder`), firmware manifest and candidate events; a draft in the coding-agent lane still gets
`patch_refusal` and the gate's own replay (`coder_runs.replay_code_patch`), which never trusts
a draft's `passed`; code candidates still stop at `awaiting_canary` (B7). It spends no Coder
budget, since nothing drafts. The proposal's own `touches_safety` is OR'd into the candidate's.

Fail closed: `overlays.raw` is what the device sent, so a `proposal_id` there is only a
claim. The skip happens only for a `doctor_proposals` row in the same fleet, issued to the
same device, with real (not stub) checks, the same patch sha256 as the overlay, built on the
code the class runs now (and the overlay's base), `applied` or `held`, as this overlay.
Anything else records one `doctor.declined` (`stage = "lock_in"`) and the overlay goes
through the Coder like any device patch, for good (`_with_the_coder`).

A proposal still `offered` and unexpired (the device's result hasn't arrived) is `pending`:
the batch decides nothing and submits no Coder job until it is applied, refused or expired,
so a later `applied` can never orphan a running Coder draft.
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
SOURCE = "doctor"  # code_versions.source and the candidate's data.source for a proposal
STAGE = "lock_in"
LOCKED_IN_STATUSES = ("applied", "held")
_PENDING = "pending"  # `_assess`: offered, waiting for the device's result


@dataclass(frozen=True)
class DoctorDraft(CodeDraft):
    """A verified Doctor proposal standing in for the Coder's draft."""

    proposal_id: str = ""
    touches_safety: bool = False  # the proposal's own flag (the cloud's K17 check)

    @property
    def key(self) -> str:
        """Its key in the batch's drafts: one part (and one candidate) per proposal."""
        return f"doctor.proposal:{self.proposal_id}"


def patch_sha256(patch: str | None) -> str:
    return hashlib.sha256((patch or "").encode("utf-8")).hexdigest()


def claims(overlay: Overlay) -> bool:
    """The overlay names a proposal (true or not)."""
    pid = (overlay.raw or {}).get("proposal_id")
    return pid is not None and pid != ""


def pending(session: Session, overlay: Overlay, base_rev: str | None, now: datetime) -> bool:
    """Read-only: its proposal checks out so far but is still `offered` and unexpired."""
    if not claims(overlay) or _with_the_coder(session, overlay):
        return False
    return _assess(session, overlay, base_rev, now)[1] == _PENDING


def verified(session: Session, overlay: Overlay, base_rev: str | None, now: datetime) -> bool:
    """Read-only: `proposal_draft` would return a draft (for the batch's early run)."""
    if not claims(overlay) or _with_the_coder(session, overlay):
        return False
    return _assess(session, overlay, base_rev, now)[1] is None


def proposal_draft(
    session: Session, overlay: Overlay, now: datetime, *, base_rev: str | None
) -> DoctorDraft | None:
    """The overlay's verified Doctor proposal as a draft, or None (use the Coder). `base_rev`
    is the code the batch builds on (the class's current rev). An overlay that names no
    proposal, or that already went to the Coder, returns None quietly; one whose proposal
    doesn't check out also records why."""
    if not claims(overlay) or _with_the_coder(session, overlay):
        return None
    proposal, problem = _assess(session, overlay, base_rev, now)
    pid = overlay.raw["proposal_id"]  # type: ignore[index]
    if problem == _PENDING:  # the batch defers these first; if one gets here, fail closed
        problem = f"proposal {pid} is still offered: the device's result hasn't arrived"
    if problem:
        _decline(session, overlay, pid, problem, now)
        return None
    assert proposal is not None
    return DoctorDraft(
        diff=overlay.code_patch or "",
        test_output=str((proposal.checks or {}).get("summary") or "checked by the Fleet Doctor"),
        passed=True,
        notes=(
            f"Checked by the Fleet Doctor (proposal {proposal.id}, consult "
            f"{proposal.consult_id}); no second Coder pass (D24)."
        ),
        proposal_id=proposal.id,
        touches_safety=bool(proposal.touches_safety),
    )


def _with_the_coder(session: Session, ov: Overlay) -> bool:
    """Sticky: the batch already declined its proposal or started a Coder draft for it, so a
    proposal that turns valid later never takes over (and never orphans that draft)."""
    refs = {"device": ov.device_id, "overlay": ov.id}
    past = events.find(session, refs, kinds=["doctor.declined", "coder.started"])
    return any(e.kind == "coder.started" or (e.data or {}).get("stage") == STAGE for e in past)


def _assess(
    session: Session, ov: Overlay, base_rev: str | None, now: datetime
) -> tuple[DoctorProposal | None, str | None]:
    """(proposal, problem): problem None when it checks out, `_PENDING` while it is offered,
    else why not. The overlay binding comes last: a proposal gets `overlay_id` when applied."""
    pid = (ov.raw or {}).get("proposal_id")
    if not isinstance(pid, str):
        return None, f"proposal_id {pid!r} is not a string"
    p = session.get(DoctorProposal, pid)
    if p is None or p.fleet_id != ov.fleet_id:
        return None, f"there is no proposal {pid} in this fleet"
    if p.device_id != ov.device_id:
        return p, f"proposal {pid} was issued to {p.device_id}, not {ov.device_id}"
    if (p.checks or {}).get("stub"):
        return p, f"proposal {pid}'s checks were a stub, so nothing really checked it"
    mine, theirs = patch_sha256(ov.code_patch), patch_sha256(p.code_patch)
    if mine != theirs:
        return p, (
            f"the overlay's code_patch (sha256 {mine[:12]}) is not proposal {pid}'s "
            f"(sha256 {theirs[:12]})"
        )
    base = (p.base or {}).get("code_rev")
    if base != base_rev:
        return p, f"stale base: proposal {pid} is on code {base}, the class runs {base_rev}"
    if ov.base_code_rev and ov.base_code_rev != base:
        return p, f"stale base: proposal {pid} is on code {base}, the overlay on {ov.base_code_rev}"
    if p.status == "offered":
        if p.expires_at is not None and p.expires_at > now:
            return p, _PENDING
        return p, f"proposal {pid} expired before the device's result arrived"
    if p.status not in LOCKED_IN_STATUSES:
        return p, f"proposal {pid} is {p.status}, not applied or held"
    if p.overlay_id != ov.id:
        return p, f"proposal {pid} was applied as overlay {p.overlay_id}, not {ov.id}"
    return p, None


def _decline(session: Session, ov: Overlay, pid: Any, problem: str, now: datetime) -> None:
    refs: dict[str, Any] = {"device": ov.device_id, "overlay": ov.id}
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
