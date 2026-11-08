"""Work-order triage: when a hardware fault becomes a Naïve card, and what the card says.

Pure functions (architecture §9 rule 3): no DB, network, clock or file IO. The parts
catalogue comes in as data (`parse_catalog`; `romulus_cloud.config.load_parts` reads the YAML),
and the counts for the spend caps come in from the caller.

- `needs_work_order`: an incident that is **unresolved** and **hardware** gets a work order
  (v0 plan §7.5). Hardware means `outcome.suspected_cause == "hardware"` or the healer's
  outcome `category == "park"` (park means hardware: CLOUD-PLATFORM.md §7, contracts
  `DEVICE_OUTCOMES`). A missing outcome reads as unresolved, cause unknown: no order.
- `check_caps`: our own spend caps, checked before a card is posted (naive-integration §3.4,
  D19b). Over a cap → `blocked_over_cap`, which waits for a person.
- `render_card`: the self-contained card body (naive-integration §3.3).
- `parse_card_note` / `card_outcome`: read the card back (naive-integration §3.2).
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from romulus_contracts import IncidentReport

SUMMARY_MAX = 300
"""Card bodies carry a short fault line only, never raw logs (naive-integration §3.3)."""

TITLE_MAX = 200
"""Naïve card titles are 1–200 characters (api/board)."""


class CatalogError(ValueError):
    """`parts.yaml` is malformed."""


@dataclass(frozen=True)
class Part:
    """One orderable part. `supplier`/`phone` default to a teammate placeholder (K13)."""

    id: str
    name: str
    fault_kinds: tuple[str, ...]
    supplier: str
    phone: str
    typical_usd: float
    max_usd: float
    quantity: int = 1


@dataclass(frozen=True)
class PartsCatalog:
    parts: tuple[Part, ...] = ()

    def for_kind(self, kind: str) -> Part | None:
        """The first part whose `fault_kinds` lists `kind` (case-insensitive), or None."""
        k = kind.strip().lower()
        for part in self.parts:
            if k in part.fault_kinds:
                return part
        return None


def _num(value: Any, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or value < 0:
        raise CatalogError(f"{where} must be a non-negative number, got {value!r}")
    return float(value)


def parse_catalog(data: Mapping[str, Any] | None) -> PartsCatalog:
    """Build a `PartsCatalog` from the parsed YAML (`{defaults: {...}, parts: [...]}`)."""
    if not data:
        return PartsCatalog()
    defaults = data.get("defaults") or {}
    parts: list[Part] = []
    seen: set[str] = set()
    for i, raw in enumerate(data.get("parts") or []):
        where = f"parts[{i}]"
        if not isinstance(raw, Mapping):
            raise CatalogError(f"{where} must be a mapping")
        for key in ("id", "name", "fault_kinds", "typical_usd", "max_usd"):
            if key not in raw:
                raise CatalogError(f"{where} is missing {key!r}")
        part_id = str(raw["id"])
        if part_id in seen:
            raise CatalogError(f"{where}: duplicate part id {part_id!r}")
        seen.add(part_id)
        kinds = raw["fault_kinds"]
        if isinstance(kinds, str) or not kinds:
            raise CatalogError(f"{where}.fault_kinds must be a non-empty list")
        quantity = raw.get("quantity", 1)
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 1:
            raise CatalogError(f"{where}.quantity must be a positive integer, got {quantity!r}")
        supplier = raw.get("supplier", defaults.get("supplier"))
        phone = raw.get("phone", defaults.get("phone"))
        if not supplier or not phone:
            raise CatalogError(f"{where} needs a supplier and phone (or catalogue defaults)")
        parts.append(
            Part(
                id=part_id,
                name=str(raw["name"]),
                fault_kinds=tuple(str(k).strip().lower() for k in kinds),
                supplier=str(supplier),
                phone=str(phone),
                typical_usd=_num(raw["typical_usd"], f"{where}.typical_usd"),
                max_usd=_num(raw["max_usd"], f"{where}.max_usd"),
                quantity=quantity,
            )
        )
    return PartsCatalog(parts=tuple(parts))


@dataclass(frozen=True)
class WorkOrderSpec:
    """Everything the work-order row and its card need, before an id is assigned.

    `part` is None when the catalogue has no part for this fault kind; `check_caps` then
    returns `needs_human` instead of posting a card that orders nothing.
    """

    incident_id: str
    device_id: str
    fault_kind: str
    summary: str
    part: Part | None
    quantity: int
    est_cost_usd: float | None
    max_usd: float
    why: str


def is_hardware_unresolved(incident: IncidentReport) -> bool:
    """True when the incident ended unresolved and the cause is hardware (or a `park`)."""
    outcome = incident.outcome
    if outcome is None or outcome.resolved:
        return False
    cause = (outcome.suspected_cause or "").strip().lower()
    category = (outcome.category or "").strip().lower()
    return cause == "hardware" or category == "park"


def _one_line(text: str, limit: int = SUMMARY_MAX) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1].rstrip() + "…"


def needs_work_order(
    incident: IncidentReport, parts_catalog: PartsCatalog, *, device_id: str
) -> WorkOrderSpec | None:
    """A `WorkOrderSpec` if this incident needs a hardware fix, else None.

    `device_id` comes from the route (`/v1/devices/{device_id}/incidents`), not the body.
    """
    if not is_hardware_unresolved(incident):
        return None
    outcome = incident.outcome
    assert outcome is not None  # is_hardware_unresolved checked it
    reason = (
        "the healer parked the arm"
        if (outcome.category or "").strip().lower() == "park"
        else "the device reported a hardware cause"
    )
    part = parts_catalog.for_kind(incident.kind)
    if part is None:
        why = (
            f"{incident.kind} on {device_id} is unresolved and {reason}, "
            f"but parts.yaml has no part for {incident.kind!r}; a person must pick one"
        )
        return WorkOrderSpec(
            incident_id=str(incident.incident_id),
            device_id=device_id,
            fault_kind=incident.kind,
            summary=_one_line(incident.summary),
            part=None,
            quantity=0,
            est_cost_usd=None,
            max_usd=0.0,
            why=why,
        )
    why = (
        f"{incident.kind} on {device_id} is unresolved and {reason}; "
        f"order {part.quantity} x {part.name} (about ${part.typical_usd * part.quantity:.2f})"
    )
    return WorkOrderSpec(
        incident_id=str(incident.incident_id),
        device_id=device_id,
        fault_kind=incident.kind,
        summary=_one_line(incident.summary),
        part=part,
        quantity=part.quantity,
        est_cost_usd=part.typical_usd * part.quantity,
        max_usd=part.max_usd,
        why=why,
    )


# --- spend caps (naive-integration §3.4, D19b) -------------------------------------------


@dataclass(frozen=True)
class Caps:
    """`ORDER_MAX_USD`, `ORDERS_PER_DAY_MAX`, `ORDERS_PER_ROBOT_DAY_MAX` from settings."""

    order_max_usd: float
    orders_per_day_max: int
    orders_per_robot_day_max: int


@dataclass(frozen=True)
class CapDecision:
    """`ok`: post the card. `blocked_over_cap`: hold it for a person (D19b).
    `needs_human`: no part to order. `why` is the sentence for the event."""

    status: Literal["ok", "blocked_over_cap", "needs_human"]
    why: str

    @property
    def ok(self) -> bool:
        return self.status == "ok"


def check_caps(
    spec: WorkOrderSpec, orders_today: int, orders_for_robot_today: int, caps: Caps
) -> CapDecision:
    """Decide whether `spec` may be posted.

    `orders_today` and `orders_for_robot_today` count the orders **already** placed today,
    not including this one (the caller decides which statuses count). So a count equal to
    the cap blocks, and a `max_usd` equal to `ORDER_MAX_USD` passes. Every broken cap is
    named in `why`.
    """
    if spec.part is None:
        return CapDecision("needs_human", spec.why)
    over: list[str] = []
    if spec.max_usd > caps.order_max_usd:
        over.append(f"MAX_USD ${spec.max_usd:.2f} is over ORDER_MAX_USD ${caps.order_max_usd:.2f}")
    if orders_today >= caps.orders_per_day_max:
        over.append(
            f"{orders_today} orders today already reach ORDERS_PER_DAY_MAX "
            f"{caps.orders_per_day_max}"
        )
    if orders_for_robot_today >= caps.orders_per_robot_day_max:
        over.append(
            f"{orders_for_robot_today} orders for {spec.device_id} today already reach "
            f"ORDERS_PER_ROBOT_DAY_MAX {caps.orders_per_robot_day_max}"
        )
    if over:
        return CapDecision("blocked_over_cap", "Over the spend cap: " + "; ".join(over) + ".")
    return CapDecision(
        "ok",
        f"Within caps: ${spec.max_usd:.2f} <= ${caps.order_max_usd:.2f}, "
        f"order {orders_today + 1} of {caps.orders_per_day_max} today, "
        f"{orders_for_robot_today + 1} of {caps.orders_per_robot_day_max} for {spec.device_id}.",
    )


# --- the card (naive-integration §3.3) ----------------------------------------------------


@dataclass(frozen=True)
class CardText:
    title: str
    body: str


def render_card(
    spec: WorkOrderSpec, work_order_id: str, *, deliver_to: str, robot: str | None = None
) -> CardText:
    """Title and body of the board card. `robot` describes the arm, e.g.
    "SO-101 follower, bench 1"; `deliver_to` is the bench address (from settings)."""
    if spec.part is None:
        raise ValueError("a work order with no part has no card; it needs a person")
    part = spec.part
    title = f"{work_order_id} · {spec.device_id} · replace {part.name}"
    if len(title) > TITLE_MAX:
        title = title[: TITLE_MAX - 1] + "…"
    robot_line = f"{spec.device_id} ({robot})" if robot else spec.device_id
    fault = f"{spec.fault_kind}. {spec.summary}" if spec.summary else spec.fault_kind
    body = "\n".join(
        [
            f"WORK ORDER {work_order_id}",
            f"Robot: {robot_line}",
            f"Fault: {fault}",
            f"Request: order {spec.quantity} × {part.name}",
            f"Supplier: {part.supplier} · {part.phone}",
            f"MAX_USD: {spec.max_usd:g}",
            f"Deliver to: {deliver_to}",
            "",
            "Rules:",
            "- If the price is over MAX_USD or the part is out of stock: set the card to "
            "blocked and comment why.",
            "- When finished: set done, with the first line of the note exactly",
            "  RESULT: ordered ORDER_REF=<ref> COST_USD=<amount>",
            "  or",
            "  STOPPED: <reason>",
        ]
    )
    return CardText(title=title, body=body)


# --- reading the card back (naive-integration §3.2) ---------------------------------------

CardStatus = Literal["in_progress", "done", "stopped", "needs_human"]


@dataclass(frozen=True)
class CardOutcome:
    """The platform status for a card, plus what the note said. `why` is for the timeline."""

    status: CardStatus
    why: str
    order_ref: str | None = None
    cost_usd: float | None = None
    reason: str | None = None


_RESULT = re.compile(r"^RESULT:\s*ordered\b(?P<rest>.*)$", re.IGNORECASE)
_STOPPED = re.compile(r"^STOPPED:\s*(?P<reason>.*)$", re.IGNORECASE)
_PAIR = re.compile(r"\b([A-Za-z_]+)=(\S+)")
_COST = re.compile(r"^\$?(\d+(?:\.\d+)?)$")


def parse_card_note(note: str | None) -> CardOutcome:
    """Parse the first line of a `done` card's note.

    - `RESULT: ordered ORDER_REF=<ref> COST_USD=<amount>` → `done`. Both keys are required;
      keys are case-insensitive and may come in either order; `$` before the amount is ok.
    - `STOPPED: <reason>` → `stopped` (needs a person).
    - Anything else (empty, malformed, a `RESULT` without a ref) → `needs_human`.
    Leading blank lines are skipped; only the first non-blank line counts.
    """
    first = (note or "").strip().split("\n", 1)[0].strip()
    if not first:
        return CardOutcome("needs_human", "Card closed without an order ref (empty note).")
    if m := _STOPPED.match(first):
        reason = m.group("reason").strip() or "no reason given"
        return CardOutcome("stopped", f"Stopped: {reason}", reason=reason)
    if m := _RESULT.match(first):
        pairs = {k.upper(): v for k, v in _PAIR.findall(m.group("rest"))}
        ref = pairs.get("ORDER_REF")
        cost_match = _COST.match(pairs.get("COST_USD", ""))
        if ref and cost_match:
            cost = float(cost_match.group(1))
            return CardOutcome(
                "done", f"Ordered: ref {ref}, ${cost:.2f}", order_ref=ref, cost_usd=cost
            )
        missing = "ORDER_REF" if not ref else "a valid COST_USD"
        return CardOutcome(
            "needs_human", f"Card closed without {missing}: {_one_line(first, 120)!r}"
        )
    return CardOutcome(
        "needs_human",
        f"Card closed without a valid RESULT or STOPPED line: {_one_line(first, 120)!r}",
    )


def card_outcome(card_status: str, note: str | None, last_comment: str = "") -> CardOutcome:
    """Map a Naïve card's `status` (+ note) to the platform status (naive-integration §3.2).

    `todo`/`doing` → in_progress; `done` → `parse_card_note`; `blocked` (incl. parked after
    3 starts) and unknown statuses → needs_human, quoting `last_comment`.
    """
    status = (card_status or "").strip().lower()
    if status in ("todo", "doing"):
        return CardOutcome("in_progress", "fleet-ops is on it")
    if status == "done":
        return parse_card_note(note)
    said = _one_line(last_comment, 200) if last_comment.strip() else "no comment"
    if status == "blocked":
        return CardOutcome("needs_human", f"Parked by Naïve: {said}")
    return CardOutcome("needs_human", f"Unknown card status {card_status!r}: {said}")
