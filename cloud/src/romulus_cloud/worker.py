"""The background loop (architecture §5): one process, one loop, ticking every ~2 s.

Each step is a service call that is safe to repeat; all state lives in Postgres, so a
crashed worker resumes where it stopped. Later PRs append to `STEPS`, e.g.
`Step("gate.tick", gate.tick)` or `Step("workorders.sync", workorders.sync, every_s=15)`.
Each step gets its own session and transaction; the worker commits after it returns,
and a step that raises is rolled back and logged without stopping the others.
"""

import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session, sessionmaker

from romulus_cloud import db
from romulus_cloud.ids import utcnow
from romulus_cloud.log import log_line
from romulus_cloud.services import workorders
from romulus_cloud.settings import get_settings


@dataclass(frozen=True)
class Step:
    name: str
    fn: Callable[[Session, datetime], object]
    every_s: float = 0  # 0 = every tick


STEPS: list[Step] = []

# M5: hardware incidents → work orders (every tick); post and poll Naïve cards (every 15 s).
STEPS += [
    Step("workorders.triage_new", workorders.triage_new),
    Step("workorders.sync", workorders.sync, every_s=15),
]


def run_tick(
    steps: list[Step],
    sessions: sessionmaker[Session],
    now: datetime,
    last_run: dict[str, datetime],
) -> dict[str, str]:
    """Run every step that is due; return {step name: "ok" | "error"} for the ones that ran."""
    results: dict[str, str] = {}
    for step in steps:
        previous = last_run.get(step.name)
        if previous is not None and (now - previous).total_seconds() < step.every_s:
            continue
        last_run[step.name] = now
        start = time.perf_counter()
        with sessions() as session:
            try:
                step.fn(session, now)
                session.commit()
                results[step.name] = "ok"
            except Exception as exc:
                session.rollback()
                results[step.name] = "error"
                log_line(
                    "worker.step_failed",
                    level="error",
                    step=step.name,
                    error=repr(exc),
                    trace=traceback.format_exc(limit=5),
                )
                continue
        log_line("worker.step", step=step.name, ms=round((time.perf_counter() - start) * 1000, 1))
    return results


def main() -> None:
    settings = get_settings()
    engine = db.make_engine(settings.database_url)
    db.create_all(engine)
    sessions = db.make_sessionmaker(engine)
    last_run: dict[str, datetime] = {}
    log_line("worker.started", steps=[s.name for s in STEPS], tick_s=settings.worker_tick_s)
    while True:
        run_tick(STEPS, sessions, utcnow(), last_run)
        time.sleep(settings.worker_tick_s)


if __name__ == "__main__":
    main()
