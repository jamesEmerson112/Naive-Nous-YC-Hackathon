"""The Fleet Doctor's spend today (docs/fleet-doctor/interfaces.md §8). Events are the spine:
every Doctor event that spent money carries `data.cost_usd`, and the day's spend is their
sum. Both L1 (`doctor_ask`) and L2 (`doctor`) read it before spending (`domain.doctor.check_spend`).
"""

from datetime import datetime

from sqlalchemy import Numeric, case, func, select
from sqlalchemy.orm import Session

from romulus_cloud.domain.doctor import day_start
from romulus_cloud.models import Event
from romulus_cloud.settings import Settings


def spent_today(session: Session, now: datetime, *, settings: Settings) -> float:
    """USD the Doctor has spent since local midnight (FLEET_TIMEZONE) in this fleet: the sum
    of `data.cost_usd` over `doctor.*` events.

    One odd value can't break the sum or slip spending past the cap:
    - only JSON numbers count (a string, bool, object or missing cost adds nothing, and can't
      raise a cast error that would block every ask and consult until midnight);
    - each value is clamped at 0, so a negative cost can't offset real spend;
    - the sum is NUMERIC, so a huge value like 1e400 can't overflow a float in SQL; it comes
      back as infinity, which `check_spend` refuses (fails closed).
    """
    cost = Event.data["cost_usd"]
    number = func.greatest(cost.astext.cast(Numeric), 0)
    total = session.scalar(
        select(
            func.coalesce(func.sum(case((func.jsonb_typeof(cost) == "number", number))), 0)
        ).where(
            Event.fleet_id == settings.fleet_id,
            Event.kind.like("doctor.%"),
            Event.ts >= day_start(now, settings.fleet_timezone),
        )
    )
    return float(total or 0)
