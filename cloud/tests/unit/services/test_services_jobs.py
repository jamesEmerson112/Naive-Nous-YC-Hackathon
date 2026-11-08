"""services/jobs.py: submit is idempotent, poll never blocks, failures carry the error."""

import threading
import time

from romulus_cloud.services.jobs import (
    Done,
    Failed,
    InlineJobs,
    Missing,
    Pending,
    ThreadPoolJobs,
    default_jobs,
)


def _boom():
    raise RuntimeError("sandbox exploded")


def _until_done(jobs, key, timeout=10):
    deadline = time.monotonic() + timeout
    while isinstance(status := jobs.poll(key), Pending):
        assert time.monotonic() < deadline
        time.sleep(0.01)
    return status


def test_inline_runs_at_submit_and_is_idempotent_once_done():
    jobs, calls = InlineJobs(), []
    assert jobs.poll("k") == Missing()
    jobs.submit("k", lambda: calls.append(1) or "result")
    jobs.submit("k", lambda: calls.append(2) or "other")
    assert jobs.poll("k") == Done("result") and calls == [1]


def test_inline_failure_is_failed_and_may_be_retried():
    jobs = InlineJobs()
    jobs.submit("k", _boom)
    assert jobs.poll("k") == Failed("RuntimeError: sandbox exploded")
    jobs.submit("k", lambda: "second try")
    assert jobs.poll("k") == Done("second try")


def test_thread_pool_poll_never_waits_and_resubmitting_a_pending_key_is_a_no_op():
    jobs = ThreadPoolJobs(max_workers=1)
    gate, calls = threading.Event(), []

    def slow():
        calls.append("slow")
        gate.wait(10)
        return 42

    try:
        jobs.submit("k", slow)
        jobs.submit("k", slow)
        started = time.monotonic()
        assert jobs.poll("k") == Pending()
        assert time.monotonic() - started < 1
        gate.set()
        assert _until_done(jobs, "k") == Done(42)
        jobs.submit("k", slow)  # done: still a no-op
        assert jobs.poll("k") == Done(42) and calls == ["slow"]
        assert jobs.poll("other") == Missing()
    finally:
        gate.set()
        jobs.shutdown()


def test_thread_pool_failure_carries_the_error_and_a_failed_key_can_retry():
    jobs = ThreadPoolJobs(max_workers=1)
    try:
        jobs.submit("k", _boom)
        assert _until_done(jobs, "k") == Failed("RuntimeError: sandbox exploded")
        jobs.submit("k", lambda: "ok")
        assert _until_done(jobs, "k") == Done("ok")
    finally:
        jobs.shutdown()


def test_default_jobs_is_one_pool_per_name():
    assert default_jobs(2) is default_jobs(5)
    assert default_jobs(2, "coder.draft") is not default_jobs(2, "coder.replay")


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_forget_drops_a_key_in_both_implementations():
    inline = InlineJobs()
    inline.submit("k", lambda: 1)
    inline.forget("k")
    assert inline.poll("k") == Missing() and inline.size() == 0
    pool = ThreadPoolJobs(max_workers=1)
    try:
        pool.submit("k", lambda: 1)
        _until_done(pool, "k")
        pool.forget("k")
        pool.forget("never-there")
        assert pool.poll("k") == Missing() and pool.size() == 0
    finally:
        pool.shutdown()


def test_settled_results_are_pruned_after_their_ttls():
    clock = Clock()
    pool = ThreadPoolJobs(max_workers=2, read_ttl_s=10, unread_ttl_s=100, clock=clock)
    try:
        for i in range(50):
            pool.submit(f"read-{i}", lambda i=i: i)
        pool.submit("unread", lambda: "x")
        for i in range(50):
            assert _until_done(pool, f"read-{i}") == Done(i)  # first read at t=0
        assert pool.poll("read-0") == Done(0)  # a second read in the same tick still works
        pool.size()  # settles "unread" (never polled) at t=0
        clock.t = 11
        assert pool.poll("other") == Missing()  # any call prunes
        assert pool.size() == 1  # only the never-read one is left
        clock.t = 101
        pool.submit("new", lambda: 1)
        _until_done(pool, "new")
        assert pool.poll("unread") == Missing()
    finally:
        pool.shutdown()


def test_a_cancelled_job_reads_failed_and_never_raises():
    pool = ThreadPoolJobs(max_workers=1)
    gate = threading.Event()
    pool.submit("running", lambda: gate.wait(5))
    pool.submit("queued", lambda: 1)
    pool.shutdown()  # cancels "queued"
    gate.set()
    status = pool.poll("queued")
    assert isinstance(status, Failed) and "CancelledError" in status.error
    pool.submit("queued", lambda: 2)  # after shutdown: a Failed, not an exception
    assert isinstance(pool.poll("queued"), Failed)


def test_the_failure_log_carries_the_type_only(caplog):
    pool = ThreadPoolJobs(max_workers=1)
    try:
        pool.submit("k", lambda: (_ for _ in ()).throw(RuntimeError("sk-SECRET in output")))
        _until_done(pool, "k")
    finally:
        pool.shutdown()
    assert "RuntimeError" in caplog.text and "sk-SECRET" not in caplog.text
