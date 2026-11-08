"""services/doctor_jobs.py: a thin adapter over M8b's `services/jobs.py` (the same types, the
Doctor's own named pool); and services/doctor_spend.py: the day's Doctor spend from events.

The generic runner's own behavior (TTL pruning, cancellation, log redaction) is tested in
`test_services_jobs.py`; here, what the Doctor's tick relies on."""

import math
import threading
import time
from datetime import timedelta

import pytest
from sqlalchemy import text

from romulus_cloud import events
from romulus_cloud.domain.doctor import Spend, check_spend
from romulus_cloud.services import doctor_jobs, jobs
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


def _wait(pool, key, timeout=5.0):
    deadline = time.monotonic() + timeout
    while isinstance(state := pool.poll(key), Pending):
        assert time.monotonic() < deadline, f"{key} still pending"
        time.sleep(0.01)
    return state


def test_the_types_are_the_shared_modules_own():
    """A one-line swap: no second implementation to drift from M8b's."""
    for name in ("Done", "Failed", "Missing", "Pending", "InlineJobs", "ThreadPoolJobs", "Jobs"):
        assert getattr(doctor_jobs, name) is getattr(jobs, name), name
    assert doctor_jobs.JobState is jobs.Status


def test_inline_runs_at_submit_is_idempotent_and_a_failure_reruns():
    inline, runs = InlineJobs(), []
    assert inline.poll("diagnose:cons_1") == Missing()
    inline.submit("diagnose:cons_1", lambda: runs.append(1) or {"text": "hi"})
    inline.submit("diagnose:cons_1", lambda: runs.append(2))  # done: a no-op
    assert inline.poll("diagnose:cons_1") == Done({"text": "hi"}) and runs == [1]
    inline.submit("draft:cons_1", _boom)
    assert inline.poll("draft:cons_1") == Failed("RuntimeError: model said no")
    inline.submit("draft:cons_1", lambda: "patch")  # failed: runs again
    assert inline.poll("draft:cons_1") == Done("patch")
    assert inline.submitted == ["diagnose:cons_1", "draft:cons_1", "draft:cons_1"]
    inline.forget("draft:cons_1")
    assert inline.poll("draft:cons_1") == Missing()


def test_default_jobs_is_the_doctors_own_named_pool(make_settings, monkeypatch):
    monkeypatch.setattr(jobs, "_pools", {})
    pool = default_jobs(make_settings(doctor_job_workers=3))
    try:
        assert isinstance(pool, ThreadPoolJobs) and pool.max_workers == 3
        assert default_jobs(make_settings(doctor_job_workers=9)) is pool  # one per process
        assert jobs._pools == {doctor_jobs.POOL: pool}
        # The batch's drafts and the gate's replays never share it.
        assert jobs.default_jobs(2, "coder.draft") is not pool
        assert jobs.default_jobs(2, "coder.replay") is not pool
        gate = threading.Event()
        pool.submit("check:cons_1", lambda: gate.wait(5) and "replayed")
        assert pool.poll("check:cons_1") == Pending()
        gate.set()
        assert _wait(pool, "check:cons_1") == Done("replayed")
    finally:
        for p in jobs._pools.values():
            p.shutdown()


def test_zero_workers_still_gets_one_thread(make_settings, monkeypatch):
    monkeypatch.setattr(jobs, "_pools", {})
    pool = default_jobs(make_settings(doctor_job_workers=0))
    try:
        assert pool.max_workers == 1
    finally:
        pool.shutdown()


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
