"""Recording metadata (APIs §2.8, routes 3.7a/3.7b) and the fleet dataset schema (§2.9).

`kind` and `format` are open strings (BUILD §2 principle 3): the cloud stores any pair,
and `EXPECTED_FORMAT` only says what each known kind normally carries.
"""

from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import NonNegativeInt

from romulus_contracts.common import CloudModel, DeviceModel, Running, Sha256, UtcDatetime


class RecordingKind(StrEnum):
    """Known recording kinds (telemetry §2). `RecordingMeta.kind` accepts any string."""

    INCIDENT_WINDOW = "incident_window"
    LOG_WINDOW = "log_window"
    AGENT_TRACE = "agent_trace"
    EPISODE = "episode"
    AUDIT = "audit"
    """Proposed: the device's audit JSON (project-overview §8.3). Not in APIs §2.8 yet."""


EXPECTED_FORMAT: dict[str, str] = {
    RecordingKind.INCIDENT_WINDOW: "lerobot_v3_tar",
    RecordingKind.LOG_WINDOW: "mcap",
    RecordingKind.AGENT_TRACE: "jsonl",
    RecordingKind.EPISODE: "lerobot_v3_tar",
    RecordingKind.AUDIT: "json",
}
"""The usual `format` for each known `kind` (APIs §2.8 table). Advisory, not enforced."""


class Episode(DeviceModel):
    """Only for `kind = episode`: `{"task": "pick_cube", "success": true, "index": 412}`."""

    task: str
    success: bool | None = None
    index: NonNegativeInt | None = None


class RecordingMeta(DeviceModel):
    """`POST /v1/devices/{device_id}/recordings`: metadata first, blob second (APIs §2.8).

    `recording_id` is a UUID made on the device. `sha256` is the content address of
    the blob uploaded to `PUT /v1/recordings/{recording_id}/blob`. Send the incident
    before its recordings (APIs §3).
    """

    recording_id: UUID
    kind: str
    format: str
    sha256: Sha256
    size_bytes: NonNegativeInt
    started_at: UtcDatetime
    ended_at: UtcDatetime | None = None
    running: Running | None = None
    overlay_id: str | None = None
    incident_id: UUID | None = None
    episode: Episode | None = None
    streams: list[str] = []
    schema_version: str | None = None
    labels: list[str] = []


class RecordingAccepted(CloudModel):
    """Response of route 3.7a. `exists` means a blob with that sha256 is already stored."""

    recording_id: UUID
    upload: Literal["needed", "exists"]


FLEET_SCHEMA_VERSION = "0.1"
"""Version of the fleet LeRobot feature schema (APIs §2.9, telemetry §8)."""

FLEET_FEATURES: dict[str, tuple[str, tuple[int, ...] | None]] = {
    "observation.state": ("float32", (6,)),
    "action": ("float32", (6,)),
    "observation.images.front": ("video", None),
    "servo_temp": ("float32", (6,)),
    "servo_load": ("float32", (6,)),
    "servo_voltage": ("float32", (6,)),
    "fault_code": ("int32", (1,)),
    "reflex_active": ("bool", (1,)),
    "intervention": ("bool", (1,)),
}
"""Every `lerobot_v3_tar` recording uses exactly these features: name -> (dtype, shape).

Shape is None for video features, whose resolution is set by the device (telemetry §8).
Changing this bumps both `FLEET_SCHEMA_VERSION` and `CONTRACT_VERSION`.
"""
