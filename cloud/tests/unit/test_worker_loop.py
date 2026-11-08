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
