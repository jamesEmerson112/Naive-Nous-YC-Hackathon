"""The background loop (architecture §5): one process, one loop, ticking every ~2 s.

Each step is a service call that is safe to repeat; all state lives in Postgres, so a
crashed worker resumes where it stopped. Later PRs append to `STEPS`, e.g.
`Step("gate.tick", gate.tick)` or `Step("workorders.sync", workorders.sync, every_s=15)`.
Each step gets its own session and transaction; the worker commits after it returns,
and a step that raises is rolled back and logged without stopping the others.

Slow work (the coding agent, code replays) never runs inside a step: steps submit it to
`services/jobs.py` (a small thread pool in this process, JOBS_MAX_WORKERS) and poll it on
later ticks. With CODER_PROVIDER=local_agent those jobs run `docker run`, so this process
then runs on the host (`make worker-host`), not in the Compose worker container.
"""

import argparse
import atexit
import fcntl
import os
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from romulus_cloud import db
from romulus_cloud.adapters import coder_egress
from romulus_cloud.adapters.blobstore import build_blobstore
from romulus_cloud.ids import utcnow
from romulus_cloud.log import log_line
from romulus_cloud.models import FleetState
from romulus_cloud.services import (
    batch,
    code_lockin,
    datasets,
    doctor,
    gate,
    review,
    review_publish,
    runs,
    technician,
    telemetry,
    workorders,
)
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

# Fleet Doctor consults (interfaces.md §5; a stub until PR 4). Every DOCTOR_TICK_S: the
# default here, the live setting via `steps_for`, which also leaves it out while
# DOCTOR_MODE=off.
STEPS.append(
    Step("doctor.tick", doctor.tick, every_s=Settings.model_fields["doctor_tick_s"].default)
)


# Naïve Technician (docs/naive-technician/design.md §6): open a case for each needs_human
# order with no part (every 5 s), and advance open cases (every 3 s). `steps_for` leaves
# both out while TECHNICIAN_MODE=off.
TECHNICIAN_STEPS = ("technician.open", "technician.sync")
STEPS += [
    Step("technician.open", technician.open_cases, every_s=5),
    Step("technician.sync", technician.sync, every_s=3),
]


def learning_tick(session: Session, now: datetime) -> object:
    """Fleet learning (docs/recursive-self-improvement/design.md §9): keep the standing
    `fleet-runs` dataset snapshotted and exported (plain code, no model); with
    LEARNING_MODE=fixture|live, then one cluster-review step (design §4, `services/review.py`:
    open, poll or settle one Naïve session, within its caps; D25, draft D31)."""
    settings = get_settings()
    if settings.learning_mode == "off":
        return None
    done = runs.ensure_standing_dataset(session, now, settings)
    if settings.learning_mode not in review.MODES:
        return done
    # Cluster review, then T1 (mirror promoted review skills to Naïve Skills): each under its
    # own savepoint, so an error rolls back only its own writes, never the dataset step.
    out: dict[str, object] = {"dataset": done}
    try:
        with session.begin_nested():
            out["review"] = review.tick(session, now, settings=settings)
    except Exception as exc:  # noqa: BLE001 - logged; retried next tick
        log_line("learning.review_failed", level="error", error=repr(exc))
    if review_publish.enabled(settings):
        try:
            with session.begin_nested():
                out["published"] = review_publish.publish_promoted(
                    session, now, settings, review_publish.publisher(settings)
                )
        except Exception as exc:  # noqa: BLE001 - logged; retried next tick
            log_line("learning.publish_failed", level="error", error=repr(exc))
    return out


# Fleet learning: the standing dataset every 30 s. `steps_for` leaves it out while
# LEARNING_MODE=off (the default).
LEARNING_STEP = "learning.tick"
STEPS.append(Step(LEARNING_STEP, learning_tick, every_s=30))


def steps_for(settings: Settings) -> list[Step]:
    """The steps the worker runs: `STEPS` with settings applied. Importing this module never
    reads `.env`, so this happens when the worker starts (changing a setting needs a
    restart). DOCTOR_MODE=off leaves `doctor.tick` out entirely, so an off Doctor writes no
    step log line every few seconds; DOCTOR_TICK_S sets its cadence. TECHNICIAN_MODE=off
    leaves out both technician steps the same way, and LEARNING_MODE=off `learning.tick`."""
    steps = [s for s in STEPS if s.name != "doctor.tick" or settings.doctor_mode != "off"]
    if settings.technician_mode == "off":
        steps = [s for s in steps if s.name not in TECHNICIAN_STEPS]
    if settings.learning_mode == "off":
        steps = [s for s in steps if s.name != LEARNING_STEP]
    every = {"doctor.tick": settings.doctor_tick_s}
    return [replace(s, every_s=every[s.name]) if s.name in every else s for s in steps]


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


def host_refusal(session: Session, settings: Settings, force: bool) -> str | None:
    """Why `make worker-host` must not start: the overnight run uses the Compose worker, and
    `make up`/`make sim` would start a second worker beside a host one (unless FORCE=1)."""
    if force:
        return None
    row = session.get(FleetState, settings.fleet_id)
    started = row.overnight_started_at if row else None
    if started is not None or settings.overnight_started:
        when = started.isoformat() if started else "OVERNIGHT_STARTED is set"
        return (
            f"worker-host: refused, the overnight run is on ({when}). Never use worker-host "
            "overnight; the Compose worker runs the fleet. FORCE=1 if you really mean it."
        )
    return None


def lock_pidfile(path: Path):
    """Take an exclusive lock on the host worker's pid file and write this pid into it.
    Returns `(open file, "")`: the lock lasts as long as the file stays open, so until this
    process exits; or `(None, other pid)` when another host worker holds it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "a+")  # noqa: SIM115 - held open on purpose: it is the lock
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.seek(0)
        other = handle.read().strip() or "?"
        handle.close()
        return None, other
    handle.seek(0)
    handle.truncate()
    handle.write(str(os.getpid()))
    handle.flush()
    return handle, ""


_PIDFILE_LOCK = None  # the host worker's pid-file lock, held until the process exits


def egress_startup(settings: Settings, runner=None) -> None:
    """With the real coder (B5): SIGTERM/SIGINT tear down open agent runs before exiting, and
    leftovers of a killed worker (a proxy still holding the key, its network) are removed."""
    if settings.coder_provider != "local_agent":
        return
    coder_egress.install_signal_teardown()
    removed = coder_egress.prune(runner)
    if removed:
        log_line(
            "worker.egress_pruned",
            removed=removed,
            why=f"removed {removed} leftover coder proxy container(s)/network(s) at startup",
        )


def main(argv: list[str] | None = None) -> None:
    global _PIDFILE_LOCK
    parser = argparse.ArgumentParser(prog="python -m romulus_cloud.worker")
    parser.add_argument("--host", action="store_true", help="run as the host worker (M8b)")
    parser.add_argument("--pidfile", default="", help="with --host: write this pid file")
    args = parser.parse_args(argv)
    settings = get_settings()
    engine = db.make_engine(settings.database_url)
    db.create_all(engine)
    sessions = db.make_sessionmaker(engine)
    if args.host:
        with sessions() as session:
            refusal = host_refusal(session, settings, os.environ.get("FORCE") == "1")
        if refusal:
            log_line("worker.refused", level="error", why=refusal)
            raise SystemExit(refusal)
        if args.pidfile:  # one host worker at a time; taken before the egress prune below
            pidfile = Path(args.pidfile)
            _PIDFILE_LOCK, other = lock_pidfile(pidfile)
            if _PIDFILE_LOCK is None:
                why = (
                    f"worker-host: refused, another host worker is running (pid {other}, lock "
                    f"on {pidfile}). Stop it first; only one host worker may run at a time."
                )
                log_line("worker.refused", level="error", why=why)
                raise SystemExit(why)
            atexit.register(lambda: pidfile.unlink(missing_ok=True))
    egress_startup(settings)
    blocker = code_lockin.coder_blocker(settings)
    if blocker:  # said once here; code overlays and coder replays wait until it is fixed
        log_line("worker.coder_unavailable", level="error", why=blocker)
    last_run: dict[str, datetime] = {}
    steps = steps_for(settings)
    log_line("worker.started", steps=[s.name for s in steps], tick_s=settings.worker_tick_s)
    while True:
        run_tick(steps, sessions, utcnow(), last_run)
        time.sleep(settings.worker_tick_s)


if __name__ == "__main__":
    main()
