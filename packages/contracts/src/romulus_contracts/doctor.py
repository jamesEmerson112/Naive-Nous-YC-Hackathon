"""Fleet Doctor: ask, advice, proposal and proposal result (APIs §2.11-§2.13, routes 3.8-3.10).

These are optional, additive contract changes, so `CONTRACT_VERSION` stays "0.1" (D28).

- **L1 (advise):** the healer asks mid-heal with `DoctorAsk` and gets `DoctorAdvice` back:
  hypotheses plus how to check each one, never a patch.
- **L2 (consult):** the device asks for a consult in its incident (`IncidentReport.consult`,
  a `ConsultRequest`). If the cloud offers a fix, the next check-in carries a
  `ProposalPointer`; the device pulls the `Proposal`, checks its `X-Sha256`, applies it as an
  overlay (`Overlay.proposal_id`) and reports a `ProposalResult`.

Device-sent models are lenient like `incident`: `null` lists and objects read as empty,
numeric triggers read as strings, and `null` text reads as "". A rejected ask only trips the
device's circuit breaker, but a rejected `ProposalResult` would be dead-lettered from the
outbox, so the one strict rule is the one the cloud needs to create the overlay row:
`result == "applied"` must name the `overlay_id` it was applied as.

Build the body of `GET /v1/devices/{id}/proposals/{pid}` with `proposal_body_and_sha256`,
which dumps once and hashes that same dump, so the served bytes and `X-Sha256` can't diverge.
"""

import hashlib
from collections.abc import Mapping
from typing import Annotated, Any, Literal, Self
from uuid import UUID

from pydantic import BeforeValidator, Field, model_validator

from romulus_contracts.common import (
    CloudModel,
    DeviceModel,
    NullAsEmpty,
    OpenDict,
    UtcDatetime,
    Version,
)
from romulus_contracts.manifest import canonical_json


def _number_to_str(value: object) -> object:
    # A trigger sent as 4 or 4.0 reads as "4"; booleans stay invalid.
    if isinstance(value, int | float) and not isinstance(value, bool):
        return str(value)
    return value


def _null_to_blank(value: object) -> object:
    return "" if value is None else value


def _count_or_none(value: object) -> object:
    # `attempts` is informational: anything that isn't a whole number reads as unknown.
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


OpenStr = Annotated[str, BeforeValidator(_number_to_str)]
"""An open string that also accepts a number (`4` reads as `"4"`)."""

Text = Annotated[str, BeforeValidator(_null_to_blank)]
"""Free text; `null` reads as `""`."""

Lines = Annotated[list[str | dict[str, Any]], NullAsEmpty]
"""Log lines or healer messages, as text or JSON-lines objects; `null` reads as `[]`."""

TRIGGERS = ("T1", "T2", "T3", "T4", "T5", "T6", "T7")
"""Known `trigger` values: when the healer asks (fleet-doctor architecture §3). Open: others
are kept as sent."""


# --- device -> cloud ---------------------------------------------------------------------


class DoctorContext(DeviceModel):
    """What the healer knows when it asks. Every field is optional.

    `recent_steps` holds the healer's last ≤5 messages; `files` holds
    `[{"path": ..., "excerpt": ...}]` for the code it is looking at.
    """

    tier: int | str | None = None
    code_rev: str | None = None
    signals: OpenDict = {}
    log_tail: Lines = []
    recent_steps: Lines = []
    files: Annotated[list[dict[str, Any]], NullAsEmpty] = []


def _null_to_context(value: object) -> object:
    return {} if value is None else value


class DoctorAsk(DeviceModel):
    """`POST /v1/devices/{device_id}/doctor/ask` (L1, route 3.8). A direct call, not the outbox.

    - `incident_id` is optional: the healer may ask before it reports the incident.
    - `trigger` is an open string (known: `TRIGGERS`).
    - `budget_s`: the device gives up after this many seconds, so the cloud's LLM timeout is
      shorter. `null` reads as the default.
    - Any non-200 means "no answer" to the device.
    """

    incident_id: UUID | None = None
    trigger: OpenStr
    question: Text = ""
    context: Annotated[DoctorContext, BeforeValidator(_null_to_context)] = Field(
        default_factory=DoctorContext
    )
    budget_s: Annotated[float, BeforeValidator(lambda v: 20.0 if v is None else v)] = 20.0
    contract_version: str | None = None


class ConsultRequest(DeviceModel):
    """`IncidentReport.consult` (L2): the healer asks the cloud to work on this incident.

    The cloud also infers a consult on its own (fleet-doctor architecture §3), so every field
    is optional. `attempts` is how many patches the healer tried; anything that isn't a whole
    number reads as `None`. A consult needs `running.code_rev` on the incident: without it the
    cloud declines (an event), never a 422.
    """

    wanted: bool = False
    trigger: Annotated[str | None, BeforeValidator(_number_to_str)] = None
    attempts: Annotated[int | None, BeforeValidator(_count_or_none)] = None


ProposalOutcome = Literal["applied", "rejected", "stale", "failed_verify"]
"""`applied`: the patch is on as overlay `overlay_id` (overlay statuses follow as usual).
`rejected`: the device declined it (not parked, expired, sha mismatch...). `stale`: the base
no longer matches what is running. `failed_verify`: applied, failed verify, rolled back."""


class ProposalResult(DeviceModel):
    """`POST /v1/devices/{device_id}/proposals/{proposal_id}/result` (route 3.10).

    Queued in the outbox like an overlay status. The proposal id is in the URL. When
    `result` is `applied`, `overlay_id` is required: the cloud creates that overlay row
    itself, because proposal overlays never arrive inside an incident.
    """

    result: ProposalOutcome
    overlay_id: str | None = None
    reason: Text = ""
    occurred_at: UtcDatetime | None = None
    contract_version: str | None = None

    @model_validator(mode="after")
    def _applied_names_its_overlay(self) -> Self:
        if self.result == "applied" and not (self.overlay_id or "").strip():
            raise ValueError("an 'applied' result needs the overlay_id the patch was applied as")
        return self


# --- cloud -> device ---------------------------------------------------------------------


class Hypothesis(CloudModel):
    """One possible cause and how the healer can check it."""

    claim: str
    how_to_check: str = ""


class DoctorAdvice(CloudModel):
    """`200` response of route 3.8. Hypotheses only, never a patch: the healer stays in charge.

    The whole answer stays small (about 300 tokens); the cloud enforces that, not this model.
    """

    ask_id: str
    hypotheses: list[Hypothesis]
    model: str = ""


class ProposalPointer(CloudModel):
    """`CheckInResponse.proposal`: a checked fix waits for this device. Pull it with route 3.9."""

    proposal_id: str
    incident_id: UUID
    expires_at: UtcDatetime


class ProposalBase(CloudModel):
    """What the device must be running for the proposal to apply (else report `stale`).

    `overlay_id` is the active overlay this proposal replaces, or null if none is active.
    `mcu_build` matters only for patches under `firmware/` (STM32).
    """

    firmware: Version | None = None
    harness: Version | None = None
    code_rev: str
    mcu_build: str | None = None
    overlay_id: str | None = None


class Proposal(CloudModel):
    """`200` response of `GET /v1/devices/{device_id}/proposals/{proposal_id}` (route 3.9).

    - `code_patch`: a unified diff against `base.code_rev`, paths relative to `device/`, in
      the `code_patch` format (APIs §2.10, `codepatch`). It includes the reproduction test.
    - `repro_test`: the path or pytest node id of that test inside `code_patch` (it failed on
      the base and passes with the patch), or null.
    - `checks`: what the cloud ran, e.g. `{"stub": false, "fails_before": true,
      "passes_after": true}`. Open; the device may ignore it.
    - The body is served with `X-Sha256` over its exact bytes (`proposal_body_and_sha256`).
      Check `sha256(response bytes) == X-Sha256` before applying.
    """

    proposal_id: str
    incident_id: UUID
    base: ProposalBase
    code_patch: str
    repro_test: str | None = None
    why: str = ""
    checks: OpenDict = {}
    expires_at: UtcDatetime


def proposal_body_and_sha256(proposal: Proposal | Mapping[str, Any]) -> tuple[bytes, str]:
    """The exact body bytes of route 3.9 and their sha256 (the `X-Sha256` header).

    The proposal is validated, dumped once (`model_dump(mode="json")`) as canonical JSON
    (`manifest.canonical_json`), and the hash is computed over those same bytes, as
    `firmware_response` does. Serve the bytes as they are; don't re-encode them.
    """
    model = proposal if isinstance(proposal, Proposal) else Proposal.model_validate(proposal)
    body = canonical_json(model.model_dump(mode="json"))
    return body, hashlib.sha256(body).hexdigest()
