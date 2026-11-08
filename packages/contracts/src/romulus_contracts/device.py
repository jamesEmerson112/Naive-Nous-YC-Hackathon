"""Check-in (APIs §2.3, route 3.1) and install result (§2.4, route 3.4)."""

import math
from typing import Any, Literal, Self

from pydantic import model_validator

from romulus_contracts.common import (
    CloudModel,
    DeviceModel,
    OpenDict,
    Running,
    UtcDatetime,
    Version,
)
from romulus_contracts.doctor import ProposalPointer

Health = Literal["ok", "degraded", "failing"]
"""Coarse device health (APIs §2.3). Tier detail goes in `health_detail` or `metrics`."""


class CheckIn(DeviceModel):
    """`POST /v1/devices/{device_id}/checkin`, every ~10 s (APIs §2.3).

    `running` is null on first boot. `metrics` is free-form: the cloud keeps the finite
    numbers as environmental history (`numeric_metrics`) and the whole dict raw.
    `contract_version` is not checked here; the server checks it with
    `version.is_supported` and answers with an error if it is too old or too new.
    `health_detail` (proposed, any JSON) carries tier detail, e.g.
    `{"tier": 3, "check": "spec_hold_violation"}`.
    """

    running: Running | None
    overlay_id: str | None = None
    health: Health
    metrics: OpenDict = {}
    health_detail: Any = None
    contract_version: str

    def numeric_metrics(self) -> dict[str, float]:
        """The metrics that are finite real numbers (bools and NaN/inf are dropped)."""
        return {
            k: float(v)
            for k, v in self.metrics.items()
            if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
        }


class Assigned(CloudModel):
    """The versions the cloud wants this device to run. Null means nothing assigned yet."""

    firmware: Version | None = None
    harness: Version | None = None


class CheckInResponse(CloudModel):
    """Response of a check-in (APIs §2.3).

    The code version to run is the assigned firmware manifest's `code_rev` (APIs §2.10).
    `frozen` means the kill switch is on: the device stays on its assigned version.
    `proposal` (optional, Fleet Doctor L2, D28) points at a checked fix waiting for this
    device (pull it with `GET /v1/devices/{id}/proposals/{proposal_id}`); null when there
    is none, and always null while `frozen`.
    """

    assigned: Assigned
    drop_overlay: bool = False
    frozen: bool = False
    proposal: ProposalPointer | None = None


InstallType = Literal["firmware", "harness", "code"]
InstallOutcome = Literal["installed", "rolled_back", "hash_mismatch", "invalid"]
"""`invalid`: the package or manifest downloaded fine (hash ok) but failed to parse or
validate on the device, e.g. a newer rule shape. Nothing was installed."""


class InstallResult(DeviceModel):
    """`POST /v1/devices/{device_id}/installs` after a pull and install (APIs §2.4).

    `type = "code"` is for code packages (APIs §2.10, D21): its `version` is the code
    rev string (e.g. `"a1b2c3d"`). Firmware and harness versions are integers.
    `occurred_at` is optional (APIs §1 asks for it; the §2.4 example omits it).
    `result = "invalid"` (proposed) reports a manifest or package that failed validation;
    put the validation error in `detail`.
    """

    type: InstallType
    version: Version | str
    result: InstallOutcome
    detail: str | None = None
    occurred_at: UtcDatetime | None = None
    contract_version: str | None = None

    @model_validator(mode="after")
    def _version_matches_type(self) -> Self:
        if self.type == "code":
            if not isinstance(self.version, str) or not self.version:
                raise ValueError("a code install's version is the code rev string")
        elif isinstance(self.version, str):
            # The union keeps "8" as a string; read it as the integer version.
            if not (self.version.isascii() and self.version.isdigit()):
                raise ValueError(f"a {self.type} install's version is an integer")
            self.version = int(self.version)
        return self
