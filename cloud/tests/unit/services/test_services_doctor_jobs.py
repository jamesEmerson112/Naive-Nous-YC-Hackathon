"""services/doctor_jobs.py: inline and thread-pool jobs, idempotent submit, poll, missing;
and services/doctor_spend.py: the day's Doctor spend summed from events."""

import math
import threading
from concurrent import futures
from datetime import timedelta

import pytest
from sqlalchemy import text

from romulus_cloud import events
from romulus_cloud.domain.doctor import Spend, check_spend
from romulus_cloud.services import doctor_jobs
from romulus_cloud.services.doctor_jobs import (
    Done,
    Failed,
    InlineJobs,
    Missing,
    Pending,
    ThreadPoolJobs,
    default_jobs,
)
from romulus_cloud.services.doctor_spend import spent_today


def _boom():
    raise RuntimeError("model said no")


# --- InlineJobs -------------------------------------------------------------------------


def test_inline_runs_at_submit_and_is_idempotent():
    jobs, runs = InlineJobs(), []
    assert jobs.poll("diagnose:cons_1") == Missing()
    jobs.submit("diagnose:cons_1", lambda: runs.append(1) or {"text": "hi"})
    assert jobs.poll("diagnose:cons_1") == Done({"text": "hi"})
    jobs.submit("diagnose:cons_1", lambda: runs.append(2))  # done: a no-op
    assert runs == [1] and jobs.calls == ["diagnose:cons_1"]


def test_inline_failure_is_reported_and_reruns_on_submit():
    jobs = InlineJobs()
    jobs.submit("k", _boom)
    assert jobs.poll("k") == Failed("RuntimeError: model said no")
    jobs.submit("k", lambda: 42)
    assert jobs.poll("k") == Done(42) and jobs.calls == ["k", "k"]


def test_finished_results_are_bounded():
    jobs = InlineJobs(max_finished=2)
    for k in ("a", "b", "c"):
        jobs.submit(k, lambda k=k: k)
    assert jobs.poll("a") == Missing()  # evicted: the tick resubmits
    assert (jobs.poll("b"), jobs.poll("c")) == (Done("b"), Done("c"))


# --- ThreadPoolJobs ---------------------------------------------------------------------


@pytest.fixture
def pool():
    jobs = ThreadPoolJobs(max_workers=2)
    yield jobs
    jobs.shutdown(wait=False)


def _wait(jobs, key, timeout=5.0):
    done = threading.Event()
    for _ in range(int(timeout / 0.01)):
        state = jobs.poll(key)
        if not isinstance(state, Pending):
            return state
        done.wait(0.01)
    raise AssertionError(f"{key} still pending")


def test_thread_pool_pending_then_done(pool):
    gate, runs = threading.Event(), []

    def job():
        gate.wait(5)
        runs.append(1)
        return "hypotheses"

    pool.submit("diagnose:cons_1", job)
    assert pool.poll("diagnose:cons_1") == Pending()
    pool.submit("diagnose:cons_1", job)  # pending: a no-op
    gate.set()
    assert _wait(pool, "diagnose:cons_1") == Done("hypotheses")
    pool.submit("diagnose:cons_1", job)  # done: a no-op
    assert pool.poll("diagnose:cons_1") == Done("hypotheses")
    assert runs == [1]


def test_thread_pool_failure_then_resubmit(pool):
    pool.submit("draft:cons_1", _boom)
    assert _wait(pool, "draft:cons_1") == Failed("RuntimeError: model said no")
    pool.submit("draft:cons_1", lambda: "patch")
    assert _wait(pool, "draft:cons_1") == Done("patch")


def test_thread_pool_unknown_key_is_missing(pool):
    assert pool.poll("check:cons_9") == Missing()


def test_forget_drops_a_key_so_it_reads_missing_and_can_run_again(pool):
    inline = InlineJobs()
    inline.submit("k", lambda: 1)
    inline.forget("k")
    assert inline.poll("k") == Missing()
    inline.forget("never-seen")  # harmless
    pool.submit("k", lambda: 1)
    assert _wait(pool, "k") == Done(1)
    pool.forget("k")
    assert pool.poll("k") == Missing()
    pool.submit("k", lambda: 2)  # a forgotten key is new again
    assert _wait(pool, "k") == Done(2)


def test_forget_a_running_job_discards_its_result(pool):
    gate = threading.Event()
    pool.submit("slow", lambda: gate.wait(5) and "late")
    pool.forget("slow")
    gate.set()
    pool.poll("other")  # sweeps
    assert pool.poll("slow") == Missing()


def test_finished_futures_never_pile_up_in_the_running_set(pool):
    for i in range(5):
        pool.submit(f"job:{i}", lambda i=i: i)
    futures.wait(list(pool._running.values()), timeout=5)  # without polling any of them
    pool.poll("unrelated")
    assert pool._running == {}  # one poll of any key moved every finished one out
    assert [pool.poll(f"job:{i}") for i in range(5)] == [Done(i) for i in range(5)]


def test_default_jobs_is_one_pool_per_process(make_settings, monkeypatch):
    monkeypatch.setattr(doctor_jobs, "_default", None)
    a = default_jobs(make_settings(doctor_job_workers=3))
    try:
        assert isinstance(a, ThreadPoolJobs) and a._pool._max_workers == 3
        assert default_jobs(make_settings(doctor_job_workers=9)) is a
    finally:
        a.shutdown(wait=False)


# --- spent_today ------------------------------------------------------------------------


def _spend(session, kind, ts, cost=None, fleet_id="default"):
    data = {"cost_usd": cost} if cost is not None else {"note": "free"}
    events.record(
        session,
        "fleet-doctor",
        "agent",
        kind,
        {"device": "arm-1"},
        "test spend",
        data=data,
        ts=ts,
        fleet_id=fleet_id,
    )


def test_spent_today_sums_doctor_costs_since_local_midnight(db_session, make_settings, now):
    # now = Sat Nov 7 22:00 UTC = 14:00 in Los Angeles; local midnight = 08:00 UTC.
    settings = make_settings(fleet_timezone="America/Los_Angeles")
    assert spent_today(db_session, now, settings=settings) == 0.0
    _spend(db_session, "doctor.advised", now - timedelta(hours=1), 0.25)
    _spend(db_session, "doctor.diagnosed", now - timedelta(hours=13), 0.5)  # 09:00 UTC: today
    _spend(db_session, "doctor.diagnosed", now - timedelta(hours=15), 9.0)  # 07:00 UTC: yesterday
    _spend(db_session, "doctor.asked", now)  # no cost
    _spend(db_session, "candidate.created", now, 7.0)  # not a Doctor event
    _spend(db_session, "doctor.advised", now, 3.0, fleet_id="other")  # another fleet
    assert spent_today(db_session, now, settings=settings) == pytest.approx(0.75)
    # In UTC the day starts at 00:00, so the 07:00 UTC spend counts too.
    utc = make_settings(fleet_timezone="UTC")
    assert spent_today(db_session, now, settings=utc) == pytest.approx(9.75)


@pytest.mark.parametrize("odd", ["0.5", "lots", True, False, {"usd": 1}, [1], None])
def test_a_cost_that_isnt_a_number_adds_nothing_and_breaks_nothing(db_session, settings, now, odd):
    _spend(db_session, "doctor.advised", now, 0.25)
    events.record(
        db_session,
        "fleet-doctor",
        "agent",
        "doctor.diagnosed",
        {"device": "arm-1"},
        "odd cost",
        data={"cost_usd": odd},
        ts=now,
    )
    assert spent_today(db_session, now, settings=settings) == pytest.approx(0.25)


def test_a_negative_cost_cannot_offset_real_spend(db_session, settings, now):
    _spend(db_session, "doctor.advised", now, 4.0)
    _spend(db_session, "doctor.advised", now, -100.0)
    assert spent_today(db_session, now, settings=settings) == pytest.approx(4.0)


def test_a_huge_cost_reads_as_infinity_and_check_spend_refuses(db_session, settings, now):
    _spend(db_session, "doctor.advised", now, 0.25)
    evt = events.record(
        db_session, "fleet-doctor", "agent", "doctor.diagnosed", {"device": "arm-1"}, "x", ts=now
    )
    db_session.execute(
        text("UPDATE events SET data = CAST(:d AS jsonb) WHERE id = :id"),
        {"d": '{"cost_usd": 1e400}', "id": evt.id},
    )
    spent = spent_today(db_session, now, settings=settings)
    assert math.isinf(spent) and spent > 0  # no overflow error in SQL
    assert not check_spend(Spend(spent, 5.0)).ok
