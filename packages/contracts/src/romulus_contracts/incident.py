"""Incident report (APIs §2.5, route 3.5) and overlay status (§2.6, route 3.6)."""

from typing import Any, Literal
from uuid import UUID

from romulus_contracts.common import CloudModel, DeviceModel, Running, UtcDatetime

SuspectedCause = Literal["software", "hardware", "unknown"]

DEVICE_OUTCOMES = ("fix-code", "fix-config", "ops", "park")
"""The healer's own outcome categories (REPO/docs/fault-catalog.md, "How to read an entry").

APIs §2.5 has no slot for them, so v0.1 carries one as the extra field
`outcome.category` (proposed, needs device-side agreement). It is an open string:
the cloud reads it from `Outcome.model_extra` and does not reject unknown values.
`park` means hardware: report `suspected_cause: "hardware"` with it (BUILD §5, I0).
"""


class Agent(DeviceModel):
    """What the embedded agent thought and did."""

    reasoning: str = ""
    actions: list[str] = []


class Overlay(DeviceModel):
    """The local patch the agent applied on top of the assigned version (D5a).

    - `diff`: a partial manifest (`rules` and/or `params`) merged over the assigned
      manifest. Rule entries here may be partial, e.g. `{"rules": {"x": {"below": 0.35}}}`,
      so they are not validated as full rules.
    - `code_patch`: a unified diff against the overlay's base `code_rev`, paths relative
      to `device/` (APIs §2.10), or null. Kept as raw text; check it with
      `codepatch.validate_code_patch`, which reports problems instead of rejecting the
      incident.
    """

    overlay_id: str
    diff: dict[str, Any] = {}
    code_patch: str | None = None


class Outcome(DeviceModel):
    """How the incident ended. Extra field `category` carries `DEVICE_OUTCOMES` (proposed)."""

    resolved: bool
    resolving_action: str | None = None
    suspected_cause: SuspectedCause = "unknown"


class IncidentReport(DeviceModel):
    """`POST /v1/devices/{device_id}/incidents` (APIs §2.5). Idempotent on `incident_id`.

    `incident_id` and each id in `recordings` are UUIDs made on the device, so a retry
    repeats the same request (D8). `signals` is the small inline sample that replay feeds
    to `rules.evaluate`; it is kept loose on purpose (see `rules` for how samples are read).
    """

    incident_id: UUID
    occurred_at: UtcDatetime
    kind: str
    summary: str = ""
    running: Running | None = None
    agent: Agent | None = None
    overlay: Overlay | None = None
    outcome: Outcome
    signals: dict[str, Any] = {}
    recordings: list[UUID] = []


class IncidentAccepted(CloudModel):
    """Response of route 3.5: `201` when new, `200` when already received."""

    incident_id: UUID


OverlayState = Literal["held", "reverted"]


class OverlayStatus(DeviceModel):
    """`POST /v1/devices/{device_id}/overlays/{overlay_id}` (APIs §2.6).

    The overlay id is in the URL. `occurred_at` is optional (APIs §1 asks for it; the
    §2.6 example omits it).
    """

    status: OverlayState
    reason: str = ""
    occurred_at: UtcDatetime | None = None
