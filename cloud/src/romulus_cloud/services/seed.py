"""`make seed`: code c0de001, firmware v1, harness v1, `arm-1` and `sim-1…3` (architecture §8, A18).

Idempotent: anything already there is left alone, so a second run creates nothing. The
one exception: a seeded device that checked in before the seed gets the seed's kind and
role (`devices.update`, with events), so `arm-1` is real and `sim-2` is the canary. The
firmware manifest and code rev match `packages/contracts/examples/firmware_response.json`,
so the fake device and the contract examples line up with a seeded platform.

`make seed-bench` (`seed_bench`, below) seeds the real bench instead: the device/ code
registered from the repo, `arm-1` and exactly one simulated canary.
"""

import json
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from romulus_cloud.adapters.blobstore import BlobStore
from romulus_cloud.errors import ApiError
from romulus_cloud.models import CodeVersion, Device, FirmwareVersion
from romulus_cloud.services import devices, registry

ACTOR = "human:seed"
WHY = "Seeded by `make seed` for the demo fleet."

CODE_REV = "c0de001"
CODE_FILES = {
    "device/README.md": b"Placeholder device code for c0de001 (make seed). The device team's\n"
    b"real code replaces this with POST /v1/code.\n",
}
HARNESS_META = {"version": 1, "framework": "mini-swe-agent", "knowledge_dir": "skills/"}
HARNESS_FILES = {
    "harness.json": json.dumps(HARNESS_META, indent=2).encode() + b"\n",
    "skills/README.md": b"# Skills\n\nThe batch job adds one markdown file per learned fix.\n",
}
FIRMWARE_V1 = {
    "version": 1,
    "device_class": "so101",
    "code_rev": CODE_REV,
    "rules": {
        "camera_occluded": {"signal": "brightness", "below": 0.2, "action": "safe_pose"},
        "gripper_stall": {"signal": "gripper_current_ma", "above": 900, "action": "backoff_retry"},
    },
    "params": {"max_speed": 0.6},
    "policy": {"name": "pick_cube", "rev": "hf:org/act@3f9e"},
}
# One real arm plus three fake ones; sim-2 is the canary (flows F3, F4).
DEVICES = [
    ("arm-1", "real", "normal"),
    ("sim-1", "sim", "normal"),
    ("sim-2", "sim", "canary"),
    ("sim-3", "sim", "normal"),
]


@dataclass
class SeedResult:
    created: list[str] = field(default_factory=list)
    existing: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)  # existed, kind/role set to the seed's


def seed(session: Session, store: BlobStore, fleet_id: str = "default") -> SeedResult:
    result = SeedResult()
    kw = {
        "actor": ACTOR,
        "actor_class": "human",
        "why": WHY,
        "source": "seed",
        "fleet_id": fleet_id,
    }

    _, made = registry.create_code(
        session, store, registry.build_tarball(CODE_FILES), rev=CODE_REV, **kw
    )
    (result.created if made else result.existing).append(f"code {CODE_REV}")

    _, made = registry.create_harness(session, store, registry.build_tarball(HARNESS_FILES), **kw)
    (result.created if made else result.existing).append("harness v1")

    if session.get(FirmwareVersion, (fleet_id, 1)) is None:
        registry.create_firmware(session, FIRMWARE_V1, **kw)
        result.created.append("firmware v1")
    else:
        result.existing.append("firmware v1")

    for device_id, kind, role in DEVICES:
        device = session.get(Device, device_id)
        if device is not None:
            # It checked in before `make seed` (so it may be `unknown`, or not the canary):
            # the seed's kind and role win, through `devices.update`, so each change is an
            # event with a why. `arm-1` can't stay unknown and the fleet keeps its canary.
            if (device.kind, device.role) != (kind, role):
                before = f"kind {device.kind}, role {device.role}"
                devices.update(
                    session,
                    device_id,
                    kind=kind,
                    role=role,
                    actor=ACTOR,
                    actor_class="human",
                    why=f"{WHY} {device_id} checked in before the seed ({before}); the seed "
                    f"makes it a {kind} so101 ({role}).",
                )
                result.updated.append(device_id)
            else:
                result.existing.append(device_id)
            continue
        devices.register(
            session,
            device_id,
            kind=kind,
            role=role,
            actor=ACTOR,
            actor_class="human",
            why=f"{WHY} {device_id} is a {kind} so101 ({role}) on firmware v1, harness v1.",
            fleet_id=fleet_id,
        )
        result.created.append(device_id)
    return result


# --- the real bench (`make seed-bench`) ------------------------------------------------------

BENCH_WHY = "Seeded by `make seed-bench` for the real bench."
CODE_SOURCE_REPO = "repo"  # code packaged from the repo's device/ (`cli code register`)
# Kent's call for the real bench: the real arm plus exactly one simulated canary.
BENCH_DEVICES = [("arm-1", "real", "normal"), ("sim-1", "sim", "canary")]


def latest_repo_code(session: Session, fleet_id: str = "default") -> str | None:
    """The newest code version registered from the repo's device/, if any."""
    return session.scalar(
        select(CodeVersion.rev)
        .where(CodeVersion.fleet_id == fleet_id, CodeVersion.source == CODE_SOURCE_REPO)
        .order_by(CodeVersion.created_at.desc())
        .limit(1)
    )


def seed_bench(
    session: Session, store: BlobStore, code_rev: str | None, fleet_id: str = "default"
) -> SeedResult:
    """`arm-1` (real) and `sim-1` (sim, the canary) on a firmware version that runs the
    registered device/ code `code_rev` (default: the newest one), plus harness v1. No other
    devices.

    Idempotent: a seeded firmware version on `code_rev` is reused, and devices that already
    match are left alone. Refuses (409) while another device is a canary, e.g. after
    `make seed`: the bench has exactly one.
    """
    result = SeedResult()
    kw = {"actor": ACTOR, "actor_class": "human", "source": "seed", "fleet_id": fleet_id}
    rev = code_rev or latest_repo_code(session, fleet_id)
    if rev is None:
        raise ApiError(
            409,
            "no_device_code",
            "No code version from the repo's device/ is registered; run `make code-register` "
            "first, or pass --code-rev.",
        )
    registry.get_code(session, rev, fleet_id)  # 404 if it isn't registered
    canaries = session.scalars(
        select(Device.id).where(
            Device.fleet_id == fleet_id, Device.role == "canary", Device.id != "sim-1"
        )
    ).all()
    if canaries:
        raise ApiError(
            409,
            "other_canary",
            f"{', '.join(canaries)} is already a canary, and the bench has exactly one "
            "(sim-1). Seed the bench on a fresh database (`make reset`), or change that "
            "device's role first.",
        )

    harness, made = registry.create_harness(
        session, store, registry.build_tarball(HARNESS_FILES), why=BENCH_WHY, **kw
    )
    (result.created if made else result.existing).append(f"harness v{harness.version}")

    all_firmware = registry.list_firmware(session, fleet_id)
    firmware = next(
        (
            f
            for f in all_firmware
            if f.manifest.get("code_rev") == rev and f.source == "seed" and not f.retired
        ),
        None,
    )
    if firmware is None:
        version = max((f.version for f in all_firmware), default=0) + 1
        firmware = registry.create_firmware(
            session,
            {**FIRMWARE_V1, "version": version, "code_rev": rev},
            why=f"{BENCH_WHY} Firmware v{version} runs the repo's device/ code {rev}.",
            **kw,
        )
        result.created.append(f"firmware v{version} on code {rev}")
    else:
        result.existing.append(f"firmware v{firmware.version} on code {rev}")

    for device_id, kind, role in BENCH_DEVICES:
        existed = session.get(Device, device_id) is not None
        if not existed:
            devices.register(
                session,
                device_id,
                kind=kind,
                role=role,
                actor=ACTOR,
                actor_class="human",
                why=f"{BENCH_WHY} {device_id} is the bench's {kind} so101 ({role}).",
                fleet_id=fleet_id,
            )
            result.created.append(device_id)
        d = session.get(Device, device_id)
        wanted = (kind, role, firmware.version, harness.version)
        if (d.kind, d.role, d.assigned_firmware, d.assigned_harness) != wanted:
            devices.update(
                session,
                device_id,
                kind=kind,
                role=role,
                firmware=firmware.version,
                harness=harness.version,
                actor=ACTOR,
                actor_class="human",
                why=f"{BENCH_WHY} {device_id} is the bench's {kind} so101 ({role}) on "
                f"firmware v{firmware.version} (device/ code {rev}), harness v{harness.version}.",
            )
            if existed:
                result.updated.append(device_id)
        elif existed:
            result.existing.append(device_id)
    return result
