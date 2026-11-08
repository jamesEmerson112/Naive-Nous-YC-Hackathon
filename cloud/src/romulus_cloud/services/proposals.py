"""Fleet Doctor proposals: offer, check-in pointer, device pull, result, expiry
(interfaces.md §6.5, architecture §5.3–5.4).

- **`offer`** makes the proposal row for a consult that passed its checks and records
  `doctor.proposal_offered`. It refuses (raises) a patch that touches a safety path, or any
  patch while `SAFETY_PATHS_CONFIRMED` is false: the consult discards those before it gets
  here, so this is the last line, not the rule (D25).
- **`pointer_for`** answers `CheckInResponse.proposal`: the device's newest servable
  proposal, or None. Always None while the fleet is frozen (D11).
- **`get_for_device`** serves the body the device pulls (409 while frozen).
- **Serve-time gate (`serve_refusal`).** Both re-check, on every request and against that
  request's settings: offered and unexpired, the Doctor not off, safety paths confirmed,
  `touches_safety` recomputed against today's `SAFETY_PATHS`, and no stub check unless
  `DOCTOR_OFFER_STUB_CHECKED` is on and the draft was a stub too. Turning any of these off
  hides an already-offered fix at once (null pointer, 410), without waiting for expiry.
- **Hashes are computed per request, never stored for serving.** `pointer_for` and
  `get_for_device` each call `proposal_body_and_sha256` on the same stored row, and the dump
  is canonical, so the pointer's `sha256`, the body bytes and `X-Sha256` always agree. The
  `sha256` column is informational only.
- **`record_result`** stores the device's `ProposalResult` once. `applied` creates (or fills
  the placeholder of) the overlay row, because a proposal overlay never arrives inside an
  incident. A resend of the recorded result is a no-op; a different one is a conflict.
- **`expire_due`** marks offered proposals past `expires_at` as expired.

Consult state is not touched here: `services/doctor.tick` reads the proposal and overlay
statuses and closes the consult. Services never commit; every write records its event in
the caller's transaction.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from romulus_cloud import events
from romulus_cloud.errors import ApiError
from romulus_cloud.ids import new_id
from romulus_cloud.models import (
    Device,
    DoctorConsult,
    DoctorProposal,
    FleetState,
    Incident,
    Overlay,
)
from romulus_cloud.settings import Settings
from romulus_contracts import summarize_code_patch, touches_safety
from romulus_contracts.doctor import (
    Proposal,
    ProposalBase,
    ProposalPointer,
    ProposalResult,
    proposal_body_and_sha256,
)

ACTOR = "fleet-doctor"
OFFERED, EXPIRED = "offered", "expired"


def _frozen(session: Session, fleet_id: str) -> bool:
    state = session.get(FleetState, fleet_id)
    return bool(state and state.frozen)


def _kind(session: Session, incident_id: str) -> str | None:
    incident = session.get(Incident, incident_id)
    return incident.kind if incident else None


def proposal_model(p: DoctorProposal) -> Proposal:
    """The contract `Proposal` for a stored row (what the device pulls)."""
    return Proposal(
        proposal_id=p.id,
        incident_id=p.incident_id,
        base=ProposalBase.model_validate(p.base or {}),
        code_patch=p.code_patch,
        repro_test=p.repro_test,
        why=p.why or "",
        checks=p.checks or {},
        expires_at=p.expires_at,
    )


def body_and_sha256(p: DoctorProposal) -> tuple[bytes, str]:
    """The exact body bytes the device pulls and their sha256, computed now from the row."""
    return proposal_body_and_sha256(proposal_model(p))


def serve_refusal(p: DoctorProposal, now: datetime, settings: Settings | None) -> str | None:
    """Why this proposal may not be served right now, or None. Checked on **every** pointer
    and pull, against the settings of this request (not those it was offered under):

    - it is `offered` and not past `expires_at`;
    - the Doctor isn't off (no settings counts as off: fail closed);
    - `SAFETY_PATHS_CONFIRMED` is true;
    - its patch doesn't touch `SAFETY_PATHS`, recomputed now (the stored flag counts too);
    - its check isn't a stub, unless `DOCTOR_OFFER_STUB_CHECKED` is on and its draft was a
      stub as well (so a real patch with only a stub check is never served).
    """
    if p.status != OFFERED:
        return p.status or "unknown"
    if p.expires_at <= now:
        return "expired"
    if settings is None or settings.doctor_mode == "off":
        return "held back: the Fleet Doctor is off"
    if not settings.safety_paths_confirmed:
        return "held back: the safety paths are unconfirmed (D25)"
    if p.touches_safety or touches_safety(p.code_patch, settings.safety_paths):
        return "held back: its patch touches the safety layer or visor (D25)"
    stub_ok = settings.doctor_offer_stub_checked and bool((p.raw or {}).get("draft_stub"))
    if (p.checks or {}).get("stub") and not stub_ok:
        return "held back: its check is a stub"
    return None


# --- the check-in pointer and the pull ---------------------------------------------------


def pointer_for(
    session: Session, device: Device, now: datetime, *, settings: Settings | None
) -> ProposalPointer | None:
    """The device's newest servable proposal (`serve_refusal`) for
    `CheckInResponse.proposal`; None if the fleet is frozen or there is none. Reads only, so
    a routine check-in still writes no event."""
    if _frozen(session, device.fleet_id):
        return None
    offered = session.scalars(
        select(DoctorProposal)
        .where(
            DoctorProposal.device_id == device.id,
            DoctorProposal.status == OFFERED,
            DoctorProposal.expires_at > now,
        )
        .order_by(DoctorProposal.created_at.desc(), DoctorProposal.id.desc())
    )
    p = next((p for p in offered if serve_refusal(p, now, settings) is None), None)
    if p is None:
        return None
    _, sha256 = body_and_sha256(p)
    return ProposalPointer(
        proposal_id=p.id, incident_id=p.incident_id, expires_at=p.expires_at, sha256=sha256
    )


def _unknown(device_id: str, proposal_id: str) -> ApiError:
    return ApiError(
        404, "unknown_proposal", f"Proposal {proposal_id!r} does not exist for {device_id}."
    )


def get_for_device(
    session: Session,
    device_id: str,
    proposal_id: str,
    now: datetime,
    *,
    settings: Settings | None,
) -> tuple[bytes, str]:
    """(body, sha256) of the proposal the device pulls.

    404 `unknown_proposal` (also when it belongs to another device), 409 `fleet_frozen`,
    410 `proposal_gone` (no longer offered, expired, or held back by `serve_refusal`).
    """
    p = session.get(DoctorProposal, proposal_id)
    if p is None or p.device_id != device_id:
        raise _unknown(device_id, proposal_id)
    if _frozen(session, p.fleet_id):
        raise ApiError(
            409,
            "fleet_frozen",
            "The fleet is frozen (kill switch), so no Fleet Doctor fix is served; "
            "stay on the assigned version.",
        )
    refusal = serve_refusal(p, now, settings)
    if refusal is not None:
        raise ApiError(
            410, "proposal_gone", f"Proposal {proposal_id} is {refusal}; it is not offered now."
        )
    return body_and_sha256(p)


# --- offering ----------------------------------------------------------------------------


def offer(
    session: Session,
    consult: DoctorConsult,
    draft: dict[str, Any],
    checks: dict[str, Any],
    now: datetime,
    *,
    settings: Settings,
) -> DoctorProposal:
    """Make the proposal row and record `doctor.proposal_offered`.

    The base is the incident's `running` plus the device's current overlay (the one the
    proposal replaces). It expires `DOCTOR_PROPOSAL_TTL_S` after `now`.
    """
    patch = draft.get("code_patch")
    if not isinstance(patch, str) or not patch.strip():
        raise ValueError(f"consult {consult.id}: the draft has no code_patch to offer")
    safety = touches_safety(patch, settings.safety_paths)
    if safety or not settings.safety_paths_confirmed:
        raise ValueError(
            f"consult {consult.id}: refusing to offer a patch while it touches a safety path "
            "or SAFETY_PATHS_CONFIRMED is false (D25)"
        )
    draft_stub = bool(draft.get("stub"))
    if checks.get("stub") and not (settings.doctor_offer_stub_checked and draft_stub):
        raise ValueError(
            f"consult {consult.id}: refusing to offer a stub-checked patch unless "
            "DOCTOR_OFFER_STUB_CHECKED is on and the draft is a stub too"
        )
    incident = session.get(Incident, consult.incident_id)
    device = session.get(Device, consult.device_id)
    assert incident is not None and device is not None
    running = incident.running or {}
    base = ProposalBase(
        firmware=running.get("firmware"),
        harness=running.get("harness"),
        code_rev=running.get("code_rev"),
        mcu_build=running.get("mcu_build"),
        overlay_id=device.overlay_id,
    )
    p = DoctorProposal(
        id=new_id("prop"),
        fleet_id=settings.fleet_id,
        device_id=consult.device_id,
        consult_id=consult.id,
        incident_id=consult.incident_id,
        base=base.model_dump(mode="json"),
        code_patch=patch,
        repro_test=draft.get("repro_test"),
        why=draft.get("why") or "",
        checks=dict(checks),
        touches_safety=safety,
        status=OFFERED,
        expires_at=now + timedelta(seconds=settings.doctor_proposal_ttl_s),
        created_at=now,
        updated_at=now,
        raw={"draft_stub": draft_stub},  # `serve_refusal` reads it on every serve
    )
    _, p.sha256 = body_and_sha256(p)  # informational; served hashes are recomputed
    session.add(p)
    session.flush()
    stub = bool(checks.get("stub"))
    why = (
        f"The Fleet Doctor offered fix {p.id} to {p.device_id} for {incident.kind}; the "
        f"device's next check-in points at it, and it expires at {p.expires_at.isoformat()}. "
        "The device applies it only if parked and still on the base it was made for."
    )
    if stub:
        why += " Its check is a stub (DOCTOR_OFFER_STUB_CHECKED is on): no sandbox ran it."
    events.record(
        session,
        ACTOR,
        "agent",
        "doctor.proposal_offered",
        {
            "device": p.device_id,
            "incident": p.incident_id,
            "consult": consult.id,
            "proposal": p.id,
        },
        why,
        data={
            "expires_at": p.expires_at.isoformat(),
            "base": p.base,
            "stub_checked": stub,
            "incident_kind": incident.kind,
        },
        ts=now,
        fleet_id=settings.fleet_id,
    )
    return p


# --- the device's result -----------------------------------------------------------------


def _same(recorded: dict[str, Any], body: ProposalResult) -> bool:
    """A resend: the same result and, for `applied`, the same overlay id (reason text and
    `occurred_at` may differ between outbox attempts)."""
    overlay_id = body.overlay_id if body.result == "applied" else None
    return recorded.get("result") == body.result and recorded.get("overlay_id") == overlay_id


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
    """Store the device's result. True if new; False for a resend of the recorded result.

    - Recorded once, in `raw["result"]`. A resend with the same result (and overlay id) is
      a no-op; a different one is `409 proposal_result_conflict`.
    - A result on an `expired` proposal with none recorded is still stored: the device
      pulled it before it expired and only the report is late (the outbox). Refusing it
      would lose a running overlay's row. `late` (and the why) say so, also for a result
      that beat the tick's expiry (`expires_at <= now`).
    - `applied` creates the overlay row (`_apply_overlay`) and records `overlay.applied`.
      An `overlay_id` whose row already belongs to an incident and doesn't carry this
      proposal's id is `409 proposal_result_conflict`, before anything is written: the
      cloud never takes over another incident's overlay.
    - Not gated by the serve-time rules: a device can always report what it did.
    - Every new result records `doctor.proposal_result` (actor `device:<id>`).
    """
    p = session.get(DoctorProposal, proposal_id, with_for_update=True)
    if p is None or p.device_id != device_id:
        raise _unknown(device_id, proposal_id)
    recorded = (p.raw or {}).get("result")
    if recorded is not None or p.status not in (OFFERED, EXPIRED):
        if recorded is not None and _same(recorded, body):
            return False
        said = (recorded or {}).get("result") or p.status
        raise ApiError(
            409,
            "proposal_result_conflict",
            f"{device_id} already reported {said} for proposal {proposal_id}; "
            f"this result says {body.result}.",
        )
    row: Overlay | None = None
    if body.result == "applied":
        assert body.overlay_id is not None  # the contract's one strict rule
        row = session.get(Overlay, (device_id, body.overlay_id), with_for_update=True)
        if (
            row is not None
            and row.incident_id is not None
            and (row.raw or {}).get("proposal_id") != p.id
        ):
            # Never take over another incident's overlay (a reused or wrong id).
            raise ApiError(
                409,
                "proposal_result_conflict",
                f"Overlay {body.overlay_id} on {device_id} already belongs to incident "
                f"{row.incident_id}, not to proposal {proposal_id}; report the overlay id "
                "the fix was applied as.",
            )
    late = p.status == EXPIRED or p.expires_at <= now
    at = body.occurred_at or now
    p.status = body.result
    p.overlay_id = body.overlay_id if body.result == "applied" else None
    p.result_reason = body.reason or None
    p.result_at = at
    p.updated_at = now
    p.raw = {
        **(p.raw or {}),
        "result": {"result": body.result, "overlay_id": p.overlay_id, "body": raw},
    }
    session.flush()
    kind = _kind(session, p.incident_id)
    refs: dict[str, Any] = {
        "device": device_id,
        "incident": p.incident_id,
        "consult": p.consult_id,
        "proposal": p.id,
    }
    if p.overlay_id:
        refs["overlay"] = p.overlay_id
    reason = body.reason or "no reason given"
    words = {
        "applied": f"applied the Fleet Doctor's fix {p.id} as overlay {p.overlay_id}",
        "rejected": f"rejected the Fleet Doctor's fix {p.id}",
        "stale": f"reported the Fleet Doctor's fix {p.id} stale (its base no longer matches)",
        "failed_verify": f"applied the Fleet Doctor's fix {p.id}, failed verify and rolled back",
    }[body.result]
    why = f"{device_id} {words}: {reason}."
    if late:
        why += " The report arrived after the proposal expired; it is recorded anyway."
    events.record(
        session,
        f"device:{device_id}",
        "agent",
        "doctor.proposal_result",
        refs,
        why,
        data={
            "result": body.result,
            "reason": body.reason,
            "overlay_id": p.overlay_id,
            "late": late,
            "incident_kind": kind,
        },
        ts=at,
        fleet_id=p.fleet_id,
    )
    if body.result == "applied":
        _apply_overlay(session, p, row, kind, at, now, safety_paths)
    return True


def _apply_overlay(
    session: Session,
    p: DoctorProposal,
    row: Overlay | None,
    kind: str | None,
    at: datetime,
    now: datetime,
    safety_paths: list[str],
) -> None:
    """Create the proposal's overlay row, or fill `row`, the placeholder an earlier overlay
    status made (keeping its status), like `incidents._apply_overlay`. Records
    `overlay.applied` after the result, so the story reads result → overlay."""
    assert p.overlay_id is not None
    if row is None:
        row = Overlay(
            device_id=p.device_id,
            id=p.overlay_id,
            fleet_id=p.fleet_id,
            status="active",
            received_at=now,
        )
        session.add(row)
    # else: a placeholder made by a status that arrived first; fill it in, keep its status.
    base = p.base or {}
    safety = p.touches_safety or touches_safety(p.code_patch, safety_paths)
    row.incident_id = p.incident_id
    row.base_firmware = base.get("firmware")
    row.base_harness = base.get("harness")
    row.base_code_rev = base.get("code_rev")
    row.diff = {}
    row.code_patch = p.code_patch
    row.touches_safety = safety
    row.occurred_at = at
    row.received_at = row.received_at or now
    row.updated_at = now
    row.raw = {**(row.raw or {}), "proposal_id": p.id, "source": "doctor"}
    session.flush()
    summary = summarize_code_patch(p.code_patch)
    refs: dict[str, Any] = {
        "incident": p.incident_id,
        "device": p.device_id,
        "overlay": p.overlay_id,
        "consult": p.consult_id,
        "proposal": p.id,
    }
    if safety:
        refs["touches_safety"] = True
    events.record(
        session,
        f"device:{p.device_id}",
        "agent",
        "overlay.applied",
        refs,
        f"{p.device_id} applied the Fleet Doctor's fix {p.id} locally to stop "
        f"{kind or 'the fault'}; the overlay is watched on the device and kept or reverted.",
        data={
            "rules": [],
            "params": [],
            "code_paths": list(summary.paths),
            "code_patch_problems": list(summary.problems),
            "proposal_id": p.id,
            "source": "doctor",
        },
        ts=at,
        fleet_id=p.fleet_id,
    )


# --- expiry ------------------------------------------------------------------------------


def expire_due(session: Session, now: datetime) -> int:
    """Mark offered proposals past `expires_at` as expired; record `doctor.proposal_expired`.
    Rows another transaction holds (a result coming in) are skipped until the next tick."""
    due = session.scalars(
        select(DoctorProposal)
        .where(DoctorProposal.status == OFFERED, DoctorProposal.expires_at <= now)
        .order_by(DoctorProposal.expires_at, DoctorProposal.id)
        .with_for_update(skip_locked=True)
    ).all()
    for p in due:
        p.status = EXPIRED
        p.updated_at = now
        session.flush()
        events.record(
            session,
            ACTOR,
            "agent",
            "doctor.proposal_expired",
            {
                "device": p.device_id,
                "incident": p.incident_id,
                "consult": p.consult_id,
                "proposal": p.id,
            },
            f"{p.device_id} didn't apply the Fleet Doctor's fix {p.id} before it expired at "
            f"{p.expires_at.isoformat()}; it is no longer offered.",
            data={"incident_kind": _kind(session, p.incident_id)},
            ts=now,
            fleet_id=p.fleet_id,
        )
    return len(due)
