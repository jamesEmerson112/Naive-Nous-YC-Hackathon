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
from dataclasses import dataclass, replace
from datetime import datetime

from sqlalchemy.orm import Session, sessionmaker

from romulus_cloud import db
from romulus_cloud.adapters.blobstore import build_blobstore
from romulus_cloud.ids import utcnow
from romulus_cloud.log import log_line
from romulus_cloud.services import batch, datasets, doctor, gate, telemetry, workorders
from romulus_cloud.settings import Settings, get_settings


@dataclass(frozen=True)
class Step:
    name: str
    fn: Callable[[Session, datetime], object]
    every_s: float = 0  # 0 = every tick


def _run_exports(session: Session, now: datetime) -> object:
    settings = get_settings()
    store = build_blobstore(settings.blob_dir)
    return datasets.run_exports(session, now, export_dir=settings.export_dir, store=store)


STEPS: list[Step] = []
# The gate moves first, so a candidate the batch makes waits one tick; the LLM call lives
# only in the batch, off the gate's path. `maybe_run` checks BATCH_INTERVAL_S itself, so
# a "run now" press is seen on the next tick.
STEPS.append(Step("gate.tick", gate.tick))
STEPS.append(Step("batch.maybe_run", batch.maybe_run))

# M5: hardware incidents → work orders (every tick); post and poll Naïve cards (every 15 s).
STEPS += [
    Step("workorders.triage_new", workorders.triage_new),
    Step("workorders.sync", workorders.sync, every_s=15),
]

# M6: resolved incidents join the regression corpus; requested exports get built.
STEPS += [
    Step("datasets.sync_regression_cases", datasets.sync_regression_cases, every_s=30),
    Step("datasets.run_exports", _run_exports, every_s=5),
]

# M7: keep the blob store under BLOB_BUDGET_GB (oldest unlabeled episodes first).
STEPS.append(Step("telemetry.enforce_budget", telemetry.enforce_budget_step, every_s=600))

# Fleet Doctor consults (interfaces.md §5; a stub until PR 4). It does nothing while
# DOCTOR_MODE=off. Every DOCTOR_TICK_S: the default here, the live setting via `steps_for`.
STEPS.append(
    Step("doctor.tick", doctor.tick, every_s=Settings.model_fields["doctor_tick_s"].default)
)


def steps_for(settings: Settings) -> list[Step]:
    """`STEPS` with cadences that come from settings applied (importing this module never
    reads `.env`, so they're bound when the worker starts)."""
    every = {"doctor.tick": settings.doctor_tick_s}
    return [replace(s, every_s=every[s.name]) if s.name in every else s for s in STEPS]


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
    steps = steps_for(settings)
    log_line("worker.started", steps=[s.name for s in steps], tick_s=settings.worker_tick_s)
    while True:
        run_tick(steps, sessions, utcnow(), last_run)
        time.sleep(settings.worker_tick_s)


if __name__ == "__main__":
    main()
