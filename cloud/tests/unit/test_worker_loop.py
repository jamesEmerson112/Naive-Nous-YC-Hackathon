from datetime import timedelta

from romulus_cloud.worker import Step, run_tick


class FakeSession:
    def __init__(self, log):
        self.log = log

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def commit(self):
        self.log.append("commit")

    def rollback(self):
        self.log.append("rollback")


def _sessions(log):
    return lambda: FakeSession(log)


def test_a_failing_step_is_rolled_back_and_the_others_still_run(now):
    log, seen = [], []

    def boom(session, now):
        raise RuntimeError("step broke")

    steps = [
        Step("a", lambda s, n: seen.append("a")),
        Step("boom", boom),
        Step("c", lambda s, n: seen.append("c")),
    ]
    results = run_tick(steps, _sessions(log), now, {})
    assert results == {"a": "ok", "boom": "error", "c": "ok"}
    assert seen == ["a", "c"]
    assert log == ["commit", "rollback", "commit"]


def test_cadence_skips_steps_that_are_not_due(now):
    calls = []
    steps = [
        Step("fast", lambda s, n: calls.append("fast")),
        Step("slow", lambda s, n: calls.append("slow"), every_s=15),
    ]
    last_run = {}
    run_tick(steps, _sessions([]), now, last_run)
    run_tick(steps, _sessions([]), now + timedelta(seconds=2), last_run)
    run_tick(steps, _sessions([]), now + timedelta(seconds=16), last_run)
    assert calls == ["fast", "slow", "fast", "fast", "slow"]


def test_steps_get_the_tick_time(now):
    got = []
    run_tick([Step("t", lambda s, n: got.append(n))], _sessions([]), now, {})
    assert got == [now]


def test_the_doctor_tick_is_a_step_on_its_settings_cadence(make_settings, now):
    from romulus_cloud import worker
    from romulus_cloud.services import doctor

    (step,) = [s for s in worker.STEPS if s.name == "doctor.tick"]
    assert step.fn is doctor.tick and step.every_s == 5
    on = make_settings(
        doctor_mode="fake", doctor_tick_s=30, technician_mode="fake", learning_mode="data"
    )
    (bound,) = [s for s in worker.steps_for(on) if s.name == step.name]
    assert bound.every_s == 30 and bound.fn is doctor.tick
    assert [s.name for s in worker.steps_for(on)] == [s.name for s in worker.STEPS]
    # A stub until PR 4: it does nothing.
    assert doctor.tick(None, now, settings=make_settings()) == {}


def test_an_off_doctor_is_not_a_step_at_all(make_settings):
    from romulus_cloud import worker

    names = [s.name for s in worker.steps_for(make_settings())]  # DOCTOR_MODE=off
    assert "doctor.tick" not in names
    off = ("doctor.tick", *worker.TECHNICIAN_STEPS, worker.LEARNING_STEP)  # all off
    assert names == [s.name for s in worker.STEPS if s.name not in off]
    assert worker.steps_for(make_settings())[0] is worker.STEPS[0]  # others untouched


def test_an_off_technician_is_not_a_step_at_all(make_settings):
    """TECHNICIAN_MODE=off (the default) leaves both technician steps out, like the Doctor."""
    from romulus_cloud import worker

    off = [s.name for s in worker.steps_for(make_settings())]
    on = [s.name for s in worker.steps_for(make_settings(technician_mode="fake"))]
    assert not set(worker.TECHNICIAN_STEPS) & set(off)
    assert set(worker.TECHNICIAN_STEPS) <= set(on)


def test_learning_is_a_step_only_when_on(make_settings):
    """LEARNING_MODE=off (the default) leaves `learning.tick` out; `data` runs it every 30 s."""
    from romulus_cloud import worker

    off = [s.name for s in worker.steps_for(make_settings())]
    (on,) = [
        s
        for s in worker.steps_for(make_settings(learning_mode="data"))
        if s.name == worker.LEARNING_STEP
    ]
    assert worker.LEARNING_STEP not in off
    assert on.fn is worker.learning_tick and on.every_s == 30
