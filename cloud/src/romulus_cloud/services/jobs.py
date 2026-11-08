"""Jobs: run slow work (the coding agent, a code replay, a long LLM call) off the worker's tick.

One seam for everything slow (M8b; the Fleet Doctor's diagnose call uses it too). A worker
step **submits** a job under a key, then **polls** that key on later ticks and applies the
result in its own transaction, with events. Nothing ever waits on a job, so `gate.tick`
keeps moving (and the kill switch keeps working) while a ~100 s coder run is in progress.

Rules for job functions: they take plain data (file dicts, patch text, incident dicts) and
**never touch the database** or a Session; they return a plain result or raise.

- `submit(key, fn)` is idempotent: the same key while pending or done is a no-op. A failed
  key may be submitted again (a retry); callers decide whether that's wanted.
- `poll(key)` returns `Pending()`, `Done(result)`, `Failed(error)` or `Missing()`. Missing
  means this process never saw the key (e.g. the worker restarted): resubmit.

Implementations: `ThreadPoolJobs` (a small thread pool inside the worker process; results
live in memory, so a restart loses them and the caller resubmits) and `InlineJobs` (runs
`fn` at submit; for tests). `default_jobs(max_workers)` is the worker's process-wide pool
(`JOBS_MAX_WORKERS`).
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Protocol

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Pending:
    pass


@dataclass(frozen=True)
class Done:
    result: Any


@dataclass(frozen=True)
class Failed:
    error: str  # "<ExceptionType>: <message>"


@dataclass(frozen=True)
class Missing:
    pass


Status = Pending | Done | Failed | Missing


class Jobs(Protocol):
    def submit(self, key: str, fn: Callable[[], Any]) -> None: ...

    def poll(self, key: str) -> Status: ...


def _error(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"


class InlineJobs:
    """Runs `fn` right away at submit (tests, and anywhere blocking is fine)."""

    def __init__(self) -> None:
        self._done: dict[str, Status] = {}
        self.submitted: list[str] = []

    def submit(self, key: str, fn: Callable[[], Any]) -> None:
        if isinstance(self._done.get(key), Done):
            return
        self.submitted.append(key)
        try:
            self._done[key] = Done(fn())
        except Exception as exc:
            self._done[key] = Failed(_error(exc))

    def poll(self, key: str) -> Status:
        return self._done.get(key, Missing())


class ThreadPoolJobs:
    """A few threads in this process. Results stay in memory until the process exits."""

    def __init__(self, max_workers: int = 2) -> None:
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="job")
        self._futures: dict[str, Future] = {}
        self._lock = threading.Lock()

    def submit(self, key: str, fn: Callable[[], Any]) -> None:
        with self._lock:
            old = self._futures.get(key)
            if old is not None and not (old.done() and old.exception() is not None):
                return  # pending or done: a no-op
            self._futures[key] = self._pool.submit(self._run, key, fn)

    @staticmethod
    def _run(key: str, fn: Callable[[], Any]) -> Any:
        try:
            return fn()
        except Exception:
            log.exception("job %s failed", key)
            raise

    def poll(self, key: str) -> Status:
        with self._lock:
            fut = self._futures.get(key)
        if fut is None:
            return Missing()
        if not fut.done():
            return Pending()
        exc = fut.exception()
        return Failed(_error(exc)) if exc is not None else Done(fut.result())

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)


_default: ThreadPoolJobs | None = None
_default_lock = threading.Lock()


def default_jobs(max_workers: int = 2) -> ThreadPoolJobs:
    """The worker process's one pool (created on first use)."""
    global _default
    with _default_lock:
        if _default is None:
            _default = ThreadPoolJobs(max_workers)
        return _default
