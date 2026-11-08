"""The Fleet Doctor's spend today (docs/fleet-doctor/interfaces.md §8). Events are the spine:
every Doctor event that spent money carries `data.cost_usd`, and the day's spend is their
sum. Both L1 (`doctor_ask`) and L2 (`doctor`) read it before spending (`domain.doctor.check_spend`).
"""

from datetime import datetime

from sqlalchemy import Float, func, select
from sqlalchemy.orm import Session

from romulus_cloud.domain.doctor import day_start
from romulus_cloud.models import Event
from romulus_cloud.settings import Settings


def spent_today(session: Session, now: datetime, *, settings: Settings) -> float:
    """USD the Doctor has spent since local midnight (FLEET_TIMEZONE) in this fleet: the sum
    of `data.cost_usd` over `doctor.*` events. Events without a cost add nothing."""
    cost = Event.data["cost_usd"].astext.cast(Float)
    total = session.scalar(
        select(func.coalesce(func.sum(cost), 0.0)).where(
            Event.fleet_id == settings.fleet_id,
            Event.kind.like("doctor.%"),
            Event.ts >= day_start(now, settings.fleet_timezone),
        )
    )
    return float(total or 0.0)
