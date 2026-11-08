"""Jobs: run slow work (the coding agent, a code replay, a long LLM call) off the worker's tick.

One seam for everything slow (M8b; the Fleet Doctor's diagnose call uses it too). A worker
step **submits** a job under a key, then **polls** that key on later ticks and applies the
result in its own transaction, with events, then **forgets** the key. Nothing ever waits on
a job, so `gate.tick` keeps moving (and the kill switch keeps working) while a ~100 s coder
run is in progress.

Rules for job functions: they take plain data (file dicts, patch text, incident dicts) and
**never touch the database** or a Session; they return a plain result or raise.

- `submit(key, fn)` is idempotent: the same key while pending or done is a no-op. A failed
  key may be submitted again (a retry); callers decide whether that's wanted.
- `poll(key)` returns `Pending()`, `Done(result)`, `Failed(error)` or `Missing()`. Missing
  means this process doesn't know the key (never submitted, forgotten, pruned, or the worker
  restarted): resubmit if it is still wanted. A cancelled job reads as `Failed`.
- `forget(key)` drops the key (after its result is applied, or when it's no longer wanted;
  a running job finishes but its result is discarded).

Implementations: `ThreadPoolJobs` (a small thread pool inside the worker process; results
live in memory, so a restart loses them and the caller resubmits; settled results are pruned
`read_ttl_s` after they were first read, or `unread_ttl_s` after they settled if nobody
reads them) and `InlineJobs` (runs `fn` at submit; for tests). `default_jobs(max_workers,
name)` returns the worker's process-wide pool of that name: the batch drafts in
`"coder.draft"` and the gate replays in `"coder.replay"`, so long drafts never starve replays.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from concurrent.futures import CancelledError, Future, ThreadPoolExecutor
from dataclasses import dataclass, field
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
JobState = Status  # the name the Fleet Doctor's stand-in (doctor_jobs.py) uses


class Jobs(Protocol):
    def submit(self, key: str, fn: Callable[[], Any]) -> None: ...

    def poll(self, key: str) -> Status: ...

    def forget(self, key: str) -> None: ...


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

    def forget(self, key: str) -> None:
        self._done.pop(key, None)

    def size(self) -> int:
        """How many keys are held (for tests and logs)."""
        return len(self._done)


@dataclass
class _Entry:
    future: Future
    settled_at: float | None = None
    read_at: float | None = None


@dataclass
class ThreadPoolJobs:
    """A few threads in this process. Settled results are pruned after their TTLs."""

    max_workers: int = 2
    read_ttl_s: float = 600  # a settled result is kept this long after it was first read
    unread_ttl_s: float = 24 * 3600  # ... and this long if nobody ever reads it
    clock: Callable[[], float] = time.monotonic
    _entries: dict[str, _Entry] = field(default_factory=dict, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)

    def __post_init__(self) -> None:
        self._pool = ThreadPoolExecutor(max_workers=self.max_workers, thread_name_prefix="job")

    def submit(self, key: str, fn: Callable[[], Any]) -> None:
        with self._lock:
            self._prune()
            old = self._entries.get(key)
            if old is not None and not isinstance(self._status(old), Failed):
                return  # pending or done: a no-op
            try:
                fut = self._pool.submit(self._run, key, fn)
            except RuntimeError as exc:  # the pool is shut down
                fut = Future()
                fut.set_exception(exc)
            self._entries[key] = _Entry(fut)

    @staticmethod
    def _run(key: str, fn: Callable[[], Any]) -> Any:
        try:
            return fn()
        except Exception as exc:
            # The type only: the message may carry output a caller redacts (e.g. a key).
            log.error("job %s failed: %s", key, type(exc).__name__)
            raise

    def _status(self, entry: _Entry) -> Status:
        fut = entry.future
        if not fut.done():
            return Pending()
        if entry.settled_at is None:
            entry.settled_at = self.clock()
        try:
            exc = fut.exception()
        except CancelledError:
            return Failed("CancelledError: the job was cancelled (worker shutting down)")
        return Failed(_error(exc)) if exc is not None else Done(fut.result())

    def _prune(self) -> None:
        now = self.clock()
        for key in list(self._entries):
            entry = self._entries[key]
            if isinstance(self._status(entry), Pending):
                continue
            read = entry.read_at is not None and now - entry.read_at > self.read_ttl_s
            stale = entry.settled_at is not None and now - entry.settled_at > self.unread_ttl_s
            if read or stale:
                del self._entries[key]

    def poll(self, key: str) -> Status:
        with self._lock:
            self._prune()
            entry = self._entries.get(key)
            if entry is None:
                return Missing()
            status = self._status(entry)
            if not isinstance(status, Pending) and entry.read_at is None:
                entry.read_at = self.clock()
            return status

    def forget(self, key: str) -> None:
        with self._lock:
            self._entries.pop(key, None)

    def size(self) -> int:
        """How many keys are held (for tests and logs)."""
        with self._lock:
            return len(self._entries)

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)


_pools: dict[str, ThreadPoolJobs] = {}
_pools_lock = threading.Lock()


def default_jobs(max_workers: int = 2, name: str = "default") -> ThreadPoolJobs:
    """The worker process's pool called `name` (created on first use, then shared)."""
    with _pools_lock:
        if name not in _pools:
            _pools[name] = ThreadPoolJobs(max_workers)
        return _pools[name]
