"""Off-tick jobs for the Fleet Doctor (docs/fleet-doctor/interfaces.md §6.2).

A stand-in with the same shape as the generic `services/jobs.py` M8b is building. When that
lands, this module becomes `from romulus_cloud.services.jobs import *`, a one-line swap.

`run_tick` runs steps one after another, so an LLM or coder call inside a step would stall
the gate and every other step. Instead `doctor.tick` submits a job under a stable key
(`diagnose:{consult_id}`), returns, and polls it on later ticks:

- A job function gets plain inputs (the case-file dict and an adapter) and returns plain
  data. **It never opens a DB session**; the tick applies the result in its own transaction.
- `submit` is idempotent: a key that is pending or done is a no-op, a failed key runs again.
- `poll` says `Pending`, `Done(result)`, `Failed(error)` or `Missing` (an unknown key, e.g.
  after a worker restart: resubmit it).
- Finished results are kept until `max_finished` newer ones push them out, so a long run
  doesn't grow without bound; an evicted key reads as `Missing`.
"""

import threading
from collections import OrderedDict
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Protocol

from romulus_cloud.settings import Settings

MAX_FINISHED = 256


@dataclass(frozen=True)
class Pending:
    pass


@dataclass(frozen=True)
class Done:
    result: Any


@dataclass(frozen=True)
class Failed:
    error: str


@dataclass(frozen=True)
class Missing:
    pass


JobState = Pending | Done | Failed | Missing


class Jobs(Protocol):
    def submit(self, key: str, fn: Callable[[], Any]) -> None:
        """Start `fn` under `key`. A pending or done key is a no-op; a failed one reruns."""
        ...

    def poll(self, key: str) -> JobState: ...


def _error(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"


class _Finished:
    """Finished states, oldest evicted first."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.states: OrderedDict[str, Done | Failed] = OrderedDict()

    def put(self, key: str, state: Done | Failed) -> None:
        self.states[key] = state
        self.states.move_to_end(key)
        while len(self.states) > self.limit:
            self.states.popitem(last=False)


class InlineJobs:
    """Runs `fn` inside `submit`. For tests: a submitted job is already done or failed."""

    def __init__(self, max_finished: int = MAX_FINISHED) -> None:
        self._finished = _Finished(max_finished)
        self.calls: list[str] = []  # keys actually run, in order (tests count them)

    def submit(self, key: str, fn: Callable[[], Any]) -> None:
        if isinstance(self._finished.states.get(key), Done):
            return
        self.calls.append(key)
        try:
            state: Done | Failed = Done(fn())
        except Exception as exc:
            state = Failed(_error(exc))
        self._finished.put(key, state)

    def poll(self, key: str) -> JobState:
        return self._finished.states.get(key) or Missing()


class ThreadPoolJobs:
    """Runs jobs on a thread pool inside the worker process (the GIL is fine: jobs wait on
    the network). Thread-safe; one per process via `default_jobs`."""

    def __init__(self, max_workers: int, max_finished: int = MAX_FINISHED) -> None:
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="doctor-job")
        self._lock = threading.Lock()
        self._running: dict[str, Future[Any]] = {}
        self._finished = _Finished(max_finished)

    def _settle(self, key: str) -> JobState:
        """Move a finished future into the finished states. Call with the lock held."""
        future = self._running.get(key)
        if future is None:
            return self._finished.states.get(key) or Missing()
        if not future.done():
            return Pending()
        del self._running[key]
        if future.cancelled():
            state: Done | Failed = Failed("CancelledError: the job was cancelled")
        elif (exc := future.exception()) is not None:
            state = Failed(_error(exc))
        else:
            state = Done(future.result())
        self._finished.put(key, state)
        return state

    def submit(self, key: str, fn: Callable[[], Any]) -> None:
        with self._lock:
            if isinstance(self._settle(key), Pending | Done):
                return
            self._finished.states.pop(key, None)
            self._running[key] = self._pool.submit(fn)

    def poll(self, key: str) -> JobState:
        with self._lock:
            return self._settle(key)

    def shutdown(self, wait: bool = True) -> None:
        self._pool.shutdown(wait=wait, cancel_futures=not wait)


_default: ThreadPoolJobs | None = None
_default_lock = threading.Lock()


def default_jobs(settings: Settings) -> Jobs:
    """The process-wide `ThreadPoolJobs`, sized by DOCTOR_JOB_WORKERS on first use."""
    global _default
    with _default_lock:
        if _default is None:
            _default = ThreadPoolJobs(max_workers=max(1, settings.doctor_job_workers))
        return _default
