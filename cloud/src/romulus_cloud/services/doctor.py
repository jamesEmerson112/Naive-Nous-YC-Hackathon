"""Fleet Doctor consults: the L2 state machine, one worker step (interfaces.md §5).

STUB (PR 2, doctor/foundation): `tick` does nothing and returns `{}`. It is already in
`worker.STEPS` as `doctor.tick`, so PR 4 (doctor/l2-consult) only fills this module: open
consults, advance each at most one stage per tick (admit → gather → diagnose → draft →
check → offer → applied → closed), expire proposals, retry. Slow work goes through
`services/doctor_jobs` (submit and poll, never inside the tick's transaction).
"""

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from romulus_cloud.settings import Settings


def tick(
    session: Session,
    now: datetime,
    *,
    settings: Settings | None = None,
    jobs: Any = None,
    llm: Any = None,
    coder: Any = None,
) -> dict[str, int]:
    """One pass over open consults → counts by what happened. Stub: does nothing.

    `jobs` (`doctor_jobs.Jobs`), `llm` (`adapters.llm.Llm`) and `coder` are injected the way
    `workorders.sync` takes `naive=`; None means the process-wide default.
    """
    return {}
