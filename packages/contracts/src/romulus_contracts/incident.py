"""Incident report (APIs §2.5, route 3.5) and overlay status (§2.6, route 3.6).

Incidents sit in the device's on-disk outbox and are retried until accepted, so a
permanent 422 would wedge the queue (and every recording behind it). Fields are
therefore lenient wherever the APIs doc allows: `outcome` may be missing,
`suspected_cause` and `category` are open strings, `agent.actions` may hold dicts, and
`null` lists/objects read as empty. Only what idempotency needs stays strict:
`incident_id` and recording ids are UUIDs (D8), and `occurred_at` is a timestamp.
"""

from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import ValidationError, ValidatorFunctionWrapHandler, WrapValidator

from romulus_contracts.common import (
    CloudModel,
    DeviceModel,
    NullAsEmpty,
    OpenDict,
    Running,
    UtcDatetime,
)
from romulus_contracts.doctor import ConsultRequest

SuspectedCause = str
"""Open string; the known values are `SUSPECTED_CAUSES`."""

SUSPECTED_CAUSES = ("software", "hardware", "unknown")
"""Known `outcome.suspected_cause` values (APIs §2.5). Others are stored as sent; the
cloud's triage treats them as `unknown`."""

DEVICE_OUTCOMES = ("fix-code", "fix-config", "ops", "park")
"""Known `outcome.category` values: the healer's outcome (REPO/docs/fault-catalog.md).

`outcome.category` is proposed (not in APIs §2.5 yet) and open: unknown values are kept.
`park` means hardware: report `suspected_cause: "hardware"` with it (BUILD §5, I0).
"""


class Agent(DeviceModel):
    """What the embedded agent thought and did. An action is a string or a harness's dict."""

    reasoning: str = ""
    actions: Annotated[list[str | dict[str, Any]], NullAsEmpty] = []


class Overlay(DeviceModel):
    """The local patch the agent applied on top of the assigned version (D5a).

    - `diff`: a partial manifest (`rules` and/or `params`) merged over the assigned
      manifest. Rule entries here may be partial, e.g. `{"rules": {"x": {"below": 0.35}}}`,
      so they are not validated as full rules. Validate the *merged* rules with
      `rules.validate_rules` before using them.
    - `code_patch`: a unified diff against the overlay's base `code_rev`, paths relative
      to `device/` (APIs §2.10), or null. Kept as raw text; check it with
      `codepatch.summarize_code_patch`, which reports problems instead of rejecting the
      incident. Decide `touches_safety` with `codepatch.touches_safety` (fails closed).
    - `proposal_id` (optional, Fleet Doctor L2, D28): the Doctor proposal this overlay came
      from, or null when the device's own healer wrote it.
    """

    overlay_id: str
    diff: OpenDict = {}
    code_patch: str | None = None
    proposal_id: str | None = None


class Outcome(DeviceModel):
    """How the incident ended.

    - `resolved` defaults to False.
    - `suspected_cause` is an open string (known: `SUSPECTED_CAUSES`), default `unknown`.
    - `category` (proposed) is the healer's outcome (known: `DEVICE_OUTCOMES`).
    """

    resolved: bool = False
    resolving_action: str | None = None
    suspected_cause: str = "unknown"
    category: str | None = None


def _lenient_consult(value: Any, handler: ValidatorFunctionWrapHandler) -> ConsultRequest | None:
    # `"consult": true` is shorthand for `{"wanted": true}`. A malformed consult reads as no
    # request instead of rejecting the incident: the cloud keeps the raw payload and applies
    # its own consult rule (fleet-doctor architecture §3).
    if isinstance(value, bool):
        value = {"wanted": value}
    try:
        return handler(value)
    except ValidationError:
        return None


class IncidentReport(DeviceModel):
    """`POST /v1/devices/{device_id}/incidents` (APIs §2.5). Idempotent on `incident_id`.

    `incident_id` and each id in `recordings` are UUIDs made on the device, so a retry
    repeats the same request (D8). `signals` is the small inline sample that replay feeds
    to `rules.evaluate`; it is kept loose on purpose (see `rules` for how samples are read).
    `outcome` may be missing: the cloud treats that as unresolved, cause unknown.

    Proposed optional fields (not in APIs §2.5 yet): `tier` (health tier 1-3),
    `log_tail` (recent log lines, as text or JSON-lines objects), `contract_version`.

    `consult` (optional, Fleet Doctor L2, D28) asks the cloud to work on this incident; send
    `running.code_rev` with it, or the cloud declines. `true` reads as `{"wanted": true}`, and
    a malformed value reads as null instead of rejecting the incident.
    """

    incident_id: UUID
    occurred_at: UtcDatetime
    kind: str
    summary: str = ""
    running: Running | None = None
    agent: Agent | None = None
    overlay: Overlay | None = None
    outcome: Outcome | None = None
    signals: OpenDict = {}
    recordings: Annotated[list[UUID], NullAsEmpty] = []
    tier: int | str | None = None
    log_tail: Annotated[list[str | dict[str, Any]], NullAsEmpty] = []
    consult: Annotated[ConsultRequest | None, WrapValidator(_lenient_consult)] = None
    contract_version: str | None = None


class IncidentAccepted(CloudModel):
    """Response of route 3.5: `201` when new, `200` when already received."""

    incident_id: UUID


OverlayState = Literal["held", "reverted", "superseded"]
"""`held`: the patch worked. `reverted`: it rolled itself back (a failure).
`superseded`: a newer firmware or code version installed and replaced it (D7); not a
failure."""


class OverlayStatus(DeviceModel):
    """`POST /v1/devices/{device_id}/overlays/{overlay_id}` (APIs §2.6).

    The overlay id is in the URL. `occurred_at` and `contract_version` are optional
    (APIs §1 asks for `occurred_at`; the §2.6 example omits it).
    """

    status: OverlayState
    reason: str = ""
    occurred_at: UtcDatetime | None = None
    contract_version: str | None = None
