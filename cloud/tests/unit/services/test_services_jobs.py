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


def test_default_jobs_is_one_pool_per_process():
    assert default_jobs(2) is default_jobs(5)
