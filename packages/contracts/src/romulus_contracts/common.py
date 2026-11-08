"""Building blocks shared by every contract module (APIs §1).

- `DeviceModel`: payloads the device sends. Unknown fields are kept (`extra="allow"`),
  so the cloud can store the raw payload and a newer device never gets rejected for
  adding a field (BUILD §2 principle 3).
- `CloudModel`: payloads the cloud returns. Unknown fields are ignored, so a device
  on this package keeps parsing responses from a newer cloud.
"""

from datetime import UTC, datetime
from typing import Annotated, Any

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    NonNegativeInt,
    StringConstraints,
)


class DeviceModel(BaseModel):
    """Base for device-sent payloads: extra fields are allowed and kept."""

    model_config = ConfigDict(extra="allow")


class CloudModel(BaseModel):
    """Base for cloud-sent payloads: extra fields are ignored."""

    model_config = ConfigDict(extra="ignore")


def _null_dict(value: object) -> object:
    return {} if value is None else value


def _null_list(value: object) -> object:
    return [] if value is None else value


OpenDict = Annotated[dict[str, Any], BeforeValidator(_null_dict)]
"""A free-form JSON object; `null` reads as `{}` so it never fails a device payload."""

NullAsEmpty = BeforeValidator(_null_list)
"""Put on a list field (`Annotated[list[X], NullAsEmpty]`) so `null` reads as `[]`."""


def _to_utc(value: datetime) -> datetime:
    # APIs §1: all times are UTC. A timestamp without an offset is read as UTC;
    # one with an offset is converted to UTC.
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


UtcDatetime = Annotated[datetime, AfterValidator(_to_utc)]
"""ISO 8601 timestamp, normalized to timezone-aware UTC."""


def _lower(value: str) -> str:
    return value.lower()


Sha256 = Annotated[
    str,
    StringConstraints(strip_whitespace=True, pattern=r"^[0-9a-fA-F]{64}$"),
    AfterValidator(_lower),
]
"""A sha256 hex digest: 64 hex characters, normalized to lowercase."""


def _not_bool(value: object) -> object:
    # Pydantic's lax int mode would read JSON `true` as 1; a version is never a boolean.
    if isinstance(value, bool):
        raise ValueError("a version is an integer, not a boolean")
    return value


Version = Annotated[NonNegativeInt, BeforeValidator(_not_bool)]
"""Firmware and harness versions: integers that go up within a fleet (APIs §1).

Lax like the rest of pydantic ("8" and 8.0 read as 8), except that booleans are rejected.
"""


class Running(DeviceModel):
    """What the device is running right now (APIs §2.3, §2.5, §2.8).

    Incidents and recordings usually send only `firmware` and `harness`; check-ins
    also send `code_rev` and `mcu_build`. A field is null when nothing of that kind
    is installed yet.
    """

    firmware: Version | None = None
    harness: Version | None = None
    code_rev: str | None = None
    mcu_build: str | None = None
    """The STM32 C-firmware build id. Reported by the device, never assigned by the cloud."""


class ErrorResponse(CloudModel):
    """Every error the cloud returns: `{"error": "<code>", "detail": "<sentence>"}` (APIs §1)."""

    error: str
    detail: str = ""


SHA256_HEADER = "X-Sha256"
"""HTTP header carrying a blob's sha256 on package downloads and blob uploads (APIs §3)."""
