"""Agent run reports (APIs §3.11): what a device's heal agent did during one run, for people.

The device sends the whole report again whenever it changes (`PUT`, idempotent on
`run_id`): while the agent works (`state: running`), when it stops (`finished`), and once
more if a bench check later confirms the result. The cloud keeps the newest one by
`updated_at` and never lets a `finished` report go back to `running`.

An optional addition to contract v0.1 (D28): a device that never sends one loses nothing.
Only `run_id`, `state` and `started_at` are required. Every other part is free-form JSON
(`OpenDict`, lists of objects), so the device can add detail without a contract change;
the APIs doc §3.11 lists the shape the edge bridge sends and the dashboard reads:

- `outcome`: `status` (`working` | `fixed` | `broken` | `unknown`), `headline`, `reasons`,
  `caveats`, `verified_by`, `running` (the image left on the chip).
- `writeup`: `summary` (labelled paragraphs: problem, investigation, noticed, actions,
  result), `report` (the agent's own final words), `stop`, `stop_text`.
- `steps`: one per tool call: `n`, `at`, `phase`, `tool`, `action`, `effect`, `ok`,
  `thought` (the model's thinking before it), `args`, `excerpt` (the start of the result).
- `firmware`: every image the run touched, in order, each with `event`, `image`, `bad`,
  `error`, `verdict`, `evidence`.
- `help_requests`: `to` (`person` | `expert_model`), `source` (`agent` | `harness`),
  `summary`, `detail`.
"""

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import Field

from romulus_contracts.common import CloudModel, DeviceModel, NullAsEmpty, OpenDict, UtcDatetime

AgentRunState = Literal["running", "finished"]
OUTCOME_STATUSES = ("working", "fixed", "broken", "unknown")
HELP_TARGETS = ("person", "expert_model")

Objects = Annotated[list[dict[str, Any]], NullAsEmpty]


class AgentRunReport(DeviceModel):
    """One heal-agent run, as the device last saw it. Extra fields are kept."""

    run_id: Annotated[str, Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9._:-]+$")]
    state: AgentRunState
    started_at: UtcDatetime
    ended_at: UtcDatetime | None = None
    updated_at: UtcDatetime | None = None
    agent: OpenDict = {}
    """`model`, `driver`, `label`."""
    sim: bool = False
    incident_ids: Annotated[list[UUID], NullAsEmpty] = []
    """The device's incidents this run worked on (empty if the device couldn't tell)."""
    incident_window: list[datetime] | None = None
    """When the device couldn't name the incident: `[from, to]`; the cloud links the
    device's incidents that occurred in it."""
    problem: OpenDict = {}
    outcome: OpenDict = {}
    writeup: OpenDict = {}
    steps: Objects = []
    firmware: Objects = []
    help_requests: Objects = []
    stats: OpenDict = {}
    verification: OpenDict | None = None
    contract_version: str | None = None


class AgentRunAccepted(CloudModel):
    """`200` for every accepted report; `created` is true the first time a run is seen,
    `stored` is false when an older report than the one held was ignored."""

    run_id: str
    created: bool
    stored: bool = True
