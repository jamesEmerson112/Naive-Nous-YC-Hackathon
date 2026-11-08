"""Fleet Doctor proposals: offer, check-in pointer, device pull, result, expiry
(interfaces.md §6.5).

STUB (PR 2, doctor/foundation): the signatures PR 4 (doctor/l2-consult) fills. Every
function raises NotImplementedError except `pointer_for`, which returns None and isn't
called yet (PR 4 wires it into `devices.check_in`). Services never commit; each write
records its event in the caller's transaction.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy.orm import Session

from romulus_cloud.models import Device, DoctorConsult, DoctorProposal
from romulus_cloud.settings import Settings

if TYPE_CHECKING:  # romulus_contracts.doctor lands with PR 1 (doctor/contracts)
    from romulus_contracts.doctor import ProposalPointer, ProposalResult

_STUB = "Fleet Doctor proposals are a stub until PR 4 (doctor/l2-consult)"


def pointer_for(session: Session, device: Device, now: datetime) -> ProposalPointer | None:
    """The device's offered, unexpired proposal for `CheckInResponse.proposal`; None if the
    fleet is frozen or there is none. Stub: always None."""
    return None


def offer(
    session: Session,
    consult: DoctorConsult,
    draft: dict[str, Any],
    checks: dict[str, Any],
    now: datetime,
    *,
    settings: Settings,
) -> DoctorProposal:
    """Make the proposal row and record `doctor.proposal_offered`."""
    raise NotImplementedError(_STUB)


def get_for_device(
    session: Session, device_id: str, proposal_id: str, now: datetime
) -> tuple[bytes, str]:
    """(body, sha256) of the proposal the device pulls; ApiError 404, 410 or 409."""
    raise NotImplementedError(_STUB)


def record_result(
    session: Session,
    device_id: str,
    proposal_id: str,
    body: ProposalResult,
    raw: dict[str, Any],
    now: datetime,
    *,
    safety_paths: list[str],
) -> bool:
    """Store the device's result (True if new). `applied` creates the overlay row."""
    raise NotImplementedError(_STUB)


def expire_due(session: Session, now: datetime) -> int:
    """Mark offered proposals past `expires_at` as expired; record `doctor.proposal_expired`."""
    raise NotImplementedError(_STUB)
