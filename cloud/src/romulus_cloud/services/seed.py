"""`make seed`: code c0de001, firmware v1, harness v1, `arm-1` and `sim-1…3` (architecture §8, A18).

Idempotent: anything already there is left alone, so a second run creates nothing. The
one exception: a seeded device that checked in before the seed gets the seed's kind and
role (`devices.update`, with events), so `arm-1` is real and `sim-2` is the canary. The
firmware manifest and code rev match `packages/contracts/examples/firmware_response.json`,
so the fake device and the contract examples line up with a seeded platform.
"""

import json
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from romulus_cloud.adapters.blobstore import BlobStore
from romulus_cloud.models import Device, FirmwareVersion
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
