"""Devices: registration, check-ins, install reports and assignments (APIs §2.3–2.4, §4.1).

Routine check-ins write no event (APIs §1); only changes do: first sight, a new running
version, a health change, an overlay change. Install reports always write one, unless they
are a retry of a report already stored. Nothing here commits.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from romulus_cloud import events
from romulus_cloud.errors import ApiError
from romulus_cloud.models import (
    CodeVersion,
    Device,
    FirmwareVersion,
    FleetState,
    HarnessVersion,
    InstallReport,
    Overlay,
)
from romulus_cloud.services import registry
from romulus_contracts import Assigned, CheckIn, CheckInResponse, InstallResult

DEVICE_KINDS = ("real", "sim", "test")
ROLES = ("normal", "canary")
# Overlay statuses after which the device should drop its local patch (the fleet has it).
DROP_STATUSES = ("locked_in", "dropped")


def get_device(session: Session, device_id: str) -> Device:
    device = session.get(Device, device_id)
    if device is None:
        raise ApiError(
            404,
            "unknown_device",
            f"Device {device_id!r} is not registered. "
            f"Check in first (POST /v1/devices/{device_id}/checkin) or register it.",
        )
    return device


def view(d: Device) -> dict[str, Any]:
    """A device as JSON for operators: identity, assigned, running, last good, health."""
    return {
        "id": d.id,
        "kind": d.kind,
        "device_class": d.device_class,
        "role": d.role,
        "assigned": {"firmware": d.assigned_firmware, "harness": d.assigned_harness},
        "running": {
            "firmware": d.running_firmware,
            "harness": d.running_harness,
            "code_rev": d.running_code_rev,
            "mcu_build": d.running_mcu_build,
        },
        "last_good": {"firmware": d.last_good_firmware, "harness": d.last_good_harness},
        "overlay_id": d.overlay_id,
        "health": d.health,
        "contract_version": d.contract_version,
        "last_seen_at": d.last_seen_at.isoformat() if d.last_seen_at else None,
    }


def list_devices(session: Session, fleet_id: str = "default") -> list[Device]:
    return list(
        session.scalars(select(Device).where(Device.fleet_id == fleet_id).order_by(Device.id))
    )


def register(
    session: Session,
    device_id: str,
    *,
    kind: str = "real",
    role: str = "normal",
    device_class: str = "so101",
    actor: str,
    actor_class: events.ActorClass,
    why: str = "",
    fleet_id: str = "default",
) -> Device:
    """Add a device and give it the class's default versions."""
    if kind not in DEVICE_KINDS:
        raise ApiError(422, "invalid_request", f"kind must be one of {DEVICE_KINDS}.")
    if role not in ROLES:
        raise ApiError(422, "invalid_request", f"role must be one of {ROLES}.")
    if session.get(Device, device_id) is not None:
        raise ApiError(409, "device_exists", f"Device {device_id!r} is already registered.")
    firmware, harness = registry.default_assignment(session, device_class, fleet_id)
    device = Device(
        id=device_id,
        fleet_id=fleet_id,
        kind=kind,
        role=role,
        device_class=device_class,
        assigned_firmware=firmware,
        assigned_harness=harness,
    )
    session.add(device)
    session.flush()
    events.record(
        session,
        actor,
        actor_class,
        "device.registered",
        {"device": device_id},
        why
        or f"{device_id} registered as a {kind} {device_class} ({role}); "
        f"assigned firmware {firmware} and harness {harness}.",
        data={"kind": kind, "role": role, "device_class": device_class},
        fleet_id=fleet_id,
    )
    return device


def _fleet_frozen(session: Session, fleet_id: str) -> bool:
    state = session.get(FleetState, fleet_id)
    return bool(state and state.frozen)


def _drop_overlay(session: Session, device: Device) -> bool:
    if not device.overlay_id:
        return False
    overlay = session.get(Overlay, (device.id, device.overlay_id))
    return overlay is not None and overlay.status in DROP_STATUSES


def _fill_assignment(session: Session, device: Device, now: datetime, *, quiet: bool) -> None:
    """Give a device with nothing assigned the class default (`registry.default_assignment`).

    `quiet` skips the event when the caller's own event already says what was assigned.
    """
    if device.assigned_firmware is not None and device.assigned_harness is not None:
        return
    firmware, harness = registry.default_assignment(
        session, device.device_class or "so101", device.fleet_id or "default"
    )
    new = (device.assigned_firmware or firmware, device.assigned_harness or harness)
    if new == (device.assigned_firmware, device.assigned_harness):
        return
    device.assigned_firmware, device.assigned_harness = new
    if not quiet:
        events.record(
            session,
            "registry",
            "automation",
            "device.assigned",
            {"device": device.id},
            f"{device.id} had nothing assigned; gave it the class default "
            f"(firmware {new[0]}, harness {new[1]}).",
            ts=now,
            fleet_id=device.fleet_id,
        )


def check_in(
    session: Session, device_id: str, body: CheckIn, raw: dict[str, Any], now: datetime
) -> CheckInResponse:
    """Record what the device runs and answer with what it should run (APIs §2.3)."""
    actor = f"device:{device_id}"
    device = session.get(Device, device_id)
    registered_now = False
    if device is None:
        # ON CONFLICT DO NOTHING: two first check-ins racing in never raise; the loser
        # waits for the winner's commit and then reads its row.
        firmware, harness = registry.default_assignment(session, "so101")
        registered_now = (
            session.execute(
                pg_insert(Device)
                .values(id=device_id, assigned_firmware=firmware, assigned_harness=harness)
                .on_conflict_do_nothing(index_elements=[Device.id])
                .returning(Device.id)
            ).scalar()
            is not None
        )
        device = session.get(Device, device_id, populate_existing=True)
        assert device is not None
    first = registered_now or device.last_seen_at is None
    _fill_assignment(session, device, now, quiet=registered_now)
    session.flush()
    if first:
        what = "registered it and assigned" if registered_now else "it is assigned"
        events.record(
            session,
            actor,
            "automation",
            "device.first_seen",
            {"device": device_id},
            f"{device_id} checked in for the first time (health {body.health}); {what} "
            f"firmware {device.assigned_firmware} and harness {device.assigned_harness}.",
            data={"running": body.running.model_dump() if body.running else None},
            ts=now,
            fleet_id=device.fleet_id,
        )

    r = body.running
    running = (r.firmware, r.harness, r.code_rev, r.mcu_build) if r else (None, None, None, None)
    before = (
        device.running_firmware,
        device.running_harness,
        device.running_code_rev,
        device.running_mcu_build,
    )
    if not first and running != before:
        events.record(
            session,
            actor,
            "automation",
            "device.running_changed",
            {"device": device_id},
            f"{device_id} now runs firmware {running[0]}, harness {running[1]}, code "
            f"{running[2]}, MCU build {running[3]} (was {before[0]}, {before[1]}, "
            f"{before[2]}, {before[3]}).",
            data={"running": list(running), "before": list(before)},
            ts=now,
            fleet_id=device.fleet_id,
        )
    if not first and body.health != device.health:
        events.record(
            session,
            actor,
            "automation",
            "device.health_changed",
            {"device": device_id},
            f"{device_id} health went from {device.health} to {body.health}.",
            data={"health": body.health, "before": device.health, "detail": body.health_detail},
            ts=now,
            fleet_id=device.fleet_id,
        )
    if not first and body.overlay_id != device.overlay_id:
        refs = {"device": device_id}
        if body.overlay_id or device.overlay_id:
            refs["overlay"] = body.overlay_id or device.overlay_id
        events.record(
            session,
            actor,
            "automation",
            "device.overlay_changed",
            refs,
            f"{device_id} now runs overlay {body.overlay_id or 'none'} "
            f"(was {device.overlay_id or 'none'}).",
            data={"overlay_id": body.overlay_id, "before": device.overlay_id},
            ts=now,
            fleet_id=device.fleet_id,
        )

    (
        device.running_firmware,
        device.running_harness,
        device.running_code_rev,
        device.running_mcu_build,
    ) = running
    device.overlay_id = body.overlay_id
    device.health = body.health
    device.metrics = body.metrics
    device.contract_version = body.contract_version
    device.last_seen_at = now
    device.raw = raw
    session.flush()
    return CheckInResponse(
        assigned=Assigned(firmware=device.assigned_firmware, harness=device.assigned_harness),
        drop_overlay=_drop_overlay(session, device),
        frozen=_fleet_frozen(session, device.fleet_id),
    )


def _install_why(device: Device, body: InstallResult) -> str:
    what = f"{body.type} {body.version}"
    detail = f" ({body.detail})" if body.detail else ""
    if body.result == "installed":
        return f"{device.id} installed {what} and passed its health check{detail}."
    if body.result == "rolled_back":
        keep = (
            f"; it stays on last good {body.type} {getattr(device, f'last_good_{body.type}')}"
            if body.type in ("firmware", "harness")
            else ""
        )
        return f"{device.id} rolled back {what} after a failed health check{detail}{keep}."
    if body.result == "hash_mismatch":
        return f"{device.id} refused {what}: the download's sha256 did not match{detail}."
    return f"{device.id} rejected {what} as invalid; nothing was installed{detail}."


def _version_exists(session: Session, device: Device, body: InstallResult) -> bool:
    if body.type == "firmware":
        return session.get(FirmwareVersion, (device.fleet_id, int(body.version))) is not None
    if body.type == "harness":
        return session.get(HarnessVersion, (device.fleet_id, int(body.version))) is not None
    return session.get(CodeVersion, (device.fleet_id, str(body.version))) is not None


def _newer_report_at(session: Session, device_id: str, type_: str, at: datetime) -> datetime | None:
    """When the newest stored report of this type happened, if it is later than `at`."""
    when = func.coalesce(InstallReport.occurred_at, InstallReport.received_at)
    latest = session.scalar(
        select(func.max(when)).where(
            InstallReport.device_id == device_id, InstallReport.type == type_
        )
    )
    return latest if latest is not None and latest > at else None


def _apply_installed(device: Device, body: InstallResult) -> str:
    """Move `running` (and `last_good` when it is the assigned version); say what moved."""
    if body.type == "code":
        device.running_code_rev = str(body.version)
        return ""
    version = int(body.version)
    setattr(device, f"running_{body.type}", version)
    assigned = getattr(device, f"assigned_{body.type}")
    if version == assigned:
        setattr(device, f"last_good_{body.type}", version)
        return ""
    last_good = getattr(device, f"last_good_{body.type}")
    return f" It is not the assigned {body.type} ({assigned}), so last good stays {last_good}."


def report_install(
    session: Session, device_id: str, body: InstallResult, raw: dict[str, Any], now: datetime
) -> bool:
    """Store an install result; on `installed`, move `running` and maybe `last_good`.
    Returns created.

    - A version the registry doesn't have is refused (`422 unknown_version`).
    - A resend with the same (type, version, result, occurred_at) is a no-op.
    - A report older than the newest stored report of its type is kept as history but
      moves nothing (it arrived late, e.g. from the outbox after a reconnect).
    - `installed` moves `running`; `last_good` moves only when the version is the one
      assigned. The other results change nothing but the log.
    """
    device = get_device(session, device_id)
    if not _version_exists(session, device, body):
        raise ApiError(
            422,
            "unknown_version",
            f"{body.type} {body.version} is not in the registry, so it can't be installed.",
        )
    version = str(body.version)
    if body.occurred_at is not None:
        dupe = session.scalar(
            select(InstallReport.id).where(
                InstallReport.device_id == device_id,
                InstallReport.type == body.type,
                InstallReport.version == version,
                InstallReport.result == body.result,
                InstallReport.occurred_at == body.occurred_at,
            )
        )
        if dupe is not None:
            return False
    at = body.occurred_at or now
    newer = _newer_report_at(session, device_id, body.type, at)
    why = _install_why(device, body)
    if newer is not None:
        why += (
            f" Ignored for running and last good: a newer {body.type} report "
            f"(from {newer.isoformat()}) is already stored."
        )
    elif body.result == "installed":
        why += _apply_installed(device, body)
    session.add(
        InstallReport(
            fleet_id=device.fleet_id,
            device_id=device_id,
            type=body.type,
            version=version,
            result=body.result,
            detail=body.detail,
            occurred_at=body.occurred_at,
            received_at=now,
            raw=raw,
        )
    )
    version_ref = "code_rev" if body.type == "code" else body.type
    events.record(
        session,
        f"device:{device_id}",
        "automation",
        "install.reported",
        {"device": device_id, version_ref: body.version},
        why,
        data={
            "type": body.type,
            "version": body.version,
            "result": body.result,
            "detail": body.detail,
            "stale": newer is not None,
        },
        ts=at,
        fleet_id=device.fleet_id,
    )
    return True


def update(
    session: Session,
    device_id: str,
    *,
    firmware: int | None = None,
    harness: int | None = None,
    role: str | None = None,
    device_class: str | None = None,
    actor: str,
    actor_class: events.ActorClass,
    why: str = "",
) -> Device:
    """Assign versions and/or change role or class, recording each change."""
    device = get_device(session, device_id)
    registry.check_assignable(session, firmware, harness, device.fleet_id)
    note = f" {why.strip()}" if why.strip() else ""
    new_fw = firmware if firmware is not None else device.assigned_firmware
    new_hw = harness if harness is not None else device.assigned_harness
    if (new_fw, new_hw) != (device.assigned_firmware, device.assigned_harness):
        events.record(
            session,
            actor,
            actor_class,
            "device.assigned",
            {"device": device_id},
            f"{actor} assigned {device_id} firmware {new_fw} and harness {new_hw} "
            f"(was {device.assigned_firmware} and {device.assigned_harness}).{note}",
            data={"firmware": new_fw, "harness": new_hw},
            fleet_id=device.fleet_id,
        )
        device.assigned_firmware, device.assigned_harness = new_fw, new_hw
    changes = {}
    if role is not None and role != device.role:
        if role not in ROLES:
            raise ApiError(422, "invalid_request", f"role must be one of {ROLES}.")
        changes["role"] = role
    if device_class is not None and device_class != device.device_class:
        changes["device_class"] = device_class
    if changes:
        events.record(
            session,
            actor,
            actor_class,
            "device.updated",
            {"device": device_id},
            f"{actor} set {device_id} "
            + ", ".join(f"{k} to {v}" for k, v in changes.items())
            + f".{note}",
            data=changes,
            fleet_id=device.fleet_id,
        )
        for k, v in changes.items():
            setattr(device, k, v)
    session.flush()
    return device
