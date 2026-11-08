"""Fleet Doctor: ask, advice, proposal and proposal result (APIs §2.11-§2.13, routes 3.8-3.10).

These are optional, additive contract changes, so `CONTRACT_VERSION` stays "0.1" (D28).

- **L1 (advise):** the healer asks mid-heal with `DoctorAsk` and gets `DoctorAdvice` back:
  hypotheses plus how to check each one, never a patch.
- **L2 (consult):** the device asks for a consult in its incident (`IncidentReport.consult`,
  a `ConsultRequest`). If the cloud offers a fix, the next check-in carries a
  `ProposalPointer`; the device pulls the `Proposal`, checks its sha256, applies it as an
  overlay and reports a `ProposalResult` (the cloud creates that overlay row from it).

Device-sent models are lenient like `incident`: a bad optional field reads as its default
instead of rejecting the payload (`null` lists and objects read as empty, numbers read as
strings where a string is expected, non-string text is stringified). A rejected ask only trips
the device's circuit breaker, but a rejected `ProposalResult` would be dead-lettered from the
outbox, and a lost `applied` means no overlay row and no lock-in. So **the one strict rule** is
the one the cloud needs to create that row: `result == "applied"` must name a non-blank
`overlay_id`.

Build the body of `GET /v1/devices/{id}/proposals/{pid}` with `proposal_body_and_sha256`,
which dumps once and hashes that same dump, so the served bytes and `X-Sha256` can't diverge.
"""

import hashlib
import math
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
    fallback,
)
from romulus_contracts.manifest import canonical_json


def _number_to_str(value: object) -> object:
    # A trigger or overlay id sent as 4 reads as "4"; booleans stay invalid.
    if isinstance(value, int | float) and not isinstance(value, bool):
        return str(value)
    return value


def _to_text(value: object) -> str:
    # Free text never fails: null reads as "", anything else that isn't a string is stringified.
    if value is None:
        return ""
    return value if isinstance(value, str) else str(value)


BUDGET_DEFAULT_S = 20.0
BUDGET_MIN_S = 3.0
BUDGET_MAX_S = 120.0


def _budget(value: object) -> float:
    # Non-numeric, non-finite or null reads as the default; numbers are clamped to the range.
    try:
        seconds = math.nan if isinstance(value, bool) else float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return BUDGET_DEFAULT_S
    if not math.isfinite(seconds):
        return BUDGET_DEFAULT_S
    return min(max(seconds, BUDGET_MIN_S), BUDGET_MAX_S)


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

Text = Annotated[str, BeforeValidator(_to_text)]
"""Free text; `null` reads as `""` and any other non-string is stringified."""

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
      shorter. Clamped to [`BUDGET_MIN_S`, `BUDGET_MAX_S`] = [3, 120]; a non-numeric,
      non-finite or null value reads as `BUDGET_DEFAULT_S` (20).
    - Any non-200 means "no answer" to the device.
    """

    incident_id: UUID | None = None
    trigger: OpenStr
    question: Text = ""
    context: Annotated[DoctorContext, BeforeValidator(_null_to_context)] = Field(
        default_factory=DoctorContext
    )
    budget_s: Annotated[float, BeforeValidator(_budget)] = BUDGET_DEFAULT_S
    contract_version: str | None = None


class ConsultRequest(DeviceModel):
    """`IncidentReport.consult` (L2): the healer asks the cloud to work on this incident.

    The cloud also infers a consult on its own (fleet-doctor architecture §3), so every field
    is optional, and a bad field reads as its default without affecting the others: `wanted`
    as False, `trigger` as None (a number reads as a string, a boolean is bad), `attempts`
    (how many patches the healer tried) as None unless it is a whole number. A consult needs
    `running.code_rev` on the incident: without it the cloud declines (an event), never a 422.
    """

    wanted: Annotated[bool, fallback(False)] = False
    trigger: Annotated[str | None, BeforeValidator(_number_to_str), fallback(None)] = None
    attempts: Annotated[int | None, BeforeValidator(_count_or_none)] = None


ProposalOutcome = Literal["applied", "rejected", "stale", "failed_verify"]
"""`applied`: the patch is on as overlay `overlay_id` (overlay statuses follow as usual).
`rejected`: the device declined it (not parked, expired, sha mismatch...). `stale`: the base
no longer matches what is running. `failed_verify`: applied, failed verify, rolled back."""


class ProposalResult(DeviceModel):
    """`POST /v1/devices/{device_id}/proposals/{proposal_id}/result` (route 3.10).

    Queued in the outbox like an overlay status. The proposal id is in the URL. This is how a
    proposal overlay reaches the cloud: the cloud creates the overlay row from an `applied`
    result (an incident never carries it).

    Lenient so the result is never dead-lettered: a numeric `overlay_id` reads as a string
    (any other non-string as None), a non-string `reason` is stringified (null reads as ""),
    and a malformed `occurred_at` reads as None. The one strict rule: `applied` needs a
    non-blank `overlay_id`, since without it the cloud can't create the row.
    """

    result: ProposalOutcome
    overlay_id: Annotated[str | None, BeforeValidator(_number_to_str), fallback(None)] = None
    reason: Text = ""
    occurred_at: Annotated[UtcDatetime | None, fallback(None)] = None
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
    """`CheckInResponse.proposal`: a checked fix waits for this device. Pull it with route 3.9.

    `sha256` (optional) is the sha256 of the exact body route 3.9 serves, so the device can
    check the pulled bytes against the check-in and not only against the same response's
    `X-Sha256`. The cloud computes it with `proposal_body_and_sha256` on each request, from
    the same stored proposal it serves, and never serves a stored hash next to a re-dumped body.
    """

    proposal_id: str
    incident_id: UUID
    expires_at: UtcDatetime
    sha256: str | None = None


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
      Before applying, check `sha256(response bytes) == X-Sha256`, and also equals the
      check-in pointer's `sha256` when that is set.
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
