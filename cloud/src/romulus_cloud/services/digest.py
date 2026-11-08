"""The morning digest from the database (flow F10): `domain.digest.summarize` over events.

`report` picks the window (`--since` as an ISO time or `30m` / `1h` / `12h` / `2d` ago;
by default from the overnight start, else the last 12 h), reads the events, and adds the
blob store's size against `BLOB_BUDGET_GB`. `cli digest`, `make digest` and
`GET /v1/digest` all go through it. Read only.
"""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from romulus_cloud.domain.digest import Digest, render_text, summarize
from romulus_cloud.errors import ApiError
from romulus_cloud.ids import ensure_utc
from romulus_cloud.models import Blob, Device, Event
from romulus_cloud.services import overnight
from romulus_cloud.services.telemetry import GB, size_text

DEFAULT_WINDOW = timedelta(hours=12)
_RELATIVE = re.compile(r"(\d+(?:\.\d+)?)\s*([smhd])")
_UNITS = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days"}


def parse_since(text: str, now: datetime) -> datetime:
    """`2026-11-08T00:30:00-08:00` (naive means UTC) or a duration ago: `30m`, `12h`, `2d`."""
    text = text.strip()
    match = _RELATIVE.fullmatch(text.lower())
    if match:
        return now - timedelta(**{_UNITS[match.group(2)]: float(match.group(1))})
    try:
        return ensure_utc(datetime.fromisoformat(text))
    except ValueError:
        raise ApiError(
            422,
            "bad_since",
            f"since={text!r} is neither an ISO time nor a duration like 30m, 12h or 2d.",
        ) from None


@dataclass(frozen=True)
class Report:
    digest: Digest
    now: datetime
    blob_bytes: int
    budget_bytes: int
    overnight_started_at: datetime | None
    simulated: int = 0
    real: int = 0
    """Arms that checked in since the window began, simulated vs real."""

    @property
    def fleet(self) -> str:
        return f"{self.simulated} simulated arms + {self.real} real"

    def text(self, tz: str | None = None) -> str:
        zone = ZoneInfo(tz) if tz else None
        storage = (
            f"Blob store: {size_text(self.blob_bytes)} of a {size_text(self.budget_bytes)} "
            f"budget ({'under' if self.blob_bytes <= self.budget_bytes else 'OVER'})"
        )
        return render_text(self.digest, zone, self.now, self.fleet) + storage + "\n"

    def to_dict(self) -> dict[str, Any]:
        d = self.digest
        return {
            "since": d.since.isoformat(),
            "until": d.until.isoformat() if d.until else None,
            "now": self.now.isoformat(),
            "overnight_started_at": (
                self.overnight_started_at.isoformat() if self.overnight_started_at else None
            ),
            "total_events": d.total_events,
            "actions": d.actions,
            "actions_by_class": dict(d.actions_by_class),
            "no_human_share": d.no_human_share,
            "incidents": d.incidents,
            "heals": d.heals,
            "overlays_reverted": d.overlays_reverted,
            "candidates_created": d.candidates_created,
            "candidates_promoted": d.candidates_promoted,
            "candidates_rolled_back": d.candidates_rolled_back,
            "work_orders_created": d.work_orders_created,
            "work_orders_posted": d.work_orders_posted,
            "freezes": d.freezes,
            "by_kind": dict(d.by_kind),
            "human_actions": [
                {"at": h.occurred_at.isoformat(), "actor": h.actor, "kind": h.kind, "why": h.why}
                for h in d.human_actions
            ],
            "waiting_for_person": [
                {
                    "work_order": w.work_order,
                    "status": w.status,
                    "since": w.since.isoformat(),
                    "why": w.why,
                }
                for w in d.waiting_for_person
            ],
            "storage": {"blob_bytes": self.blob_bytes, "budget_bytes": self.budget_bytes},
            "fleet": {"simulated": self.simulated, "real": self.real},
        }


def events_for(
    session: Session, since: datetime, until: datetime | None, fleet_id: str
) -> list[Event]:
    """The window's events, plus every earlier work-order event (for "waiting for a person")."""
    stmt = select(Event).where(
        Event.fleet_id == fleet_id, or_(Event.ts >= since, Event.kind.like("workorder.%"))
    )
    if until is not None:
        stmt = stmt.where(Event.ts < until)
    return list(session.scalars(stmt.order_by(Event.ts)))


def blob_bytes(session: Session) -> int:
    return int(session.scalar(select(func.coalesce(func.sum(Blob.size_bytes), 0))) or 0)


def is_simulated(device: Device) -> bool:
    """`kind` sim/test, or a `sim-N` id (A18): a fake arm that checks in on its own is
    auto-registered with the default kind, so the id is the reliable sign."""
    return device.kind in ("sim", "test") or device.id.startswith("sim-")


def active_fleet(session: Session, since: datetime, fleet_id: str) -> tuple[int, int]:
    """(simulated, real) arms that checked in at or after `since`."""
    rows = session.scalars(
        select(Device).where(Device.fleet_id == fleet_id, Device.last_seen_at >= since)
    )
    sims = [is_simulated(d) for d in rows]
    return sum(sims), len(sims) - sum(sims)


def report(
    session: Session,
    now: datetime,
    *,
    since: str | None = None,
    until: str | None = None,
    budget_gb: float = 20,
    fleet_id: str = "default",
) -> Report:
    started = overnight.started_at(session, fleet_id)
    if since:
        start = parse_since(since, now)
    else:
        start = started or now - DEFAULT_WINDOW
    end = parse_since(until, now) if until else None
    digest = summarize(events_for(session, start, end, fleet_id), start, end)
    simulated, real = active_fleet(session, start, fleet_id)
    return Report(digest, now, blob_bytes(session), int(budget_gb * GB), started, simulated, real)
