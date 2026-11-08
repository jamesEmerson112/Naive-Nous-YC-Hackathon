"""ManagerClient (armsim/manager.py) against the fake Hermes API server (tests/fake_manager.py), one test
per mode, plus the plan check on what came back. No Hermes, no key, no network beyond 127.0.0.1.
Run from prototypes/arm-sim:

    PYTHONPATH=. .venv/bin/python tests/test_manager.py      (or: python -m pytest tests/)
"""
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import fake_manager  # noqa: E402
from armsim.escalation import SAFETY, check_plan  # noqa: E402
from armsim.manager import ManagerClient, fake_client  # noqa: E402
from test_escalation import ESC, STATE  # noqa: E402


def client(mode, *, deadline=None, **opts):
    srv, url = fake_manager.start(mode, **opts)
    return srv, ManagerClient(url, key=opts.get("key"), poll_s=0.1), (deadline or 10)


def ask(mode, **opts):
    srv, m, deadline = client(mode, **opts)
    t0 = time.time()
    try:
        text, why = m.ask(ESC, deadline_s=deadline)
    finally:
        if srv:
            srv.shutdown()
    return text, why, time.time() - t0, m, srv


def check(text):
    return check_plan(text, ESC, {**STATE, "now": time.time(), "asked_at": time.time() - 1})


def test_good_answers_a_plan_that_passes_the_check():
    text, why, secs, m, srv = ask("good")
    assert text and "completed" in why and secs < 5, (text, why, secs)
    assert check(text) == (True, "ok"), check(text)
    assert json.loads(text)["action"] == "restart_sensor"
    post = next(r for r in srv.requests if r["method"] == "POST")
    body = json.loads(post["body"])
    assert post["headers"]["Idempotency-Key"] == ESC["incident_id"] and body["session_id"] == ESC["incident_id"]
    assert json.loads(body["input"]) == ESC and "arm-fixplan/v1" in body["instructions"]
    assert m.last["run_id"].startswith("run_") and m.last["status"] == "completed"


def test_bad_json_comes_back_and_the_check_refuses_it():
    text, why, secs, m, srv = ask("bad_json")
    assert text and "completed" in why, why
    ok, reason = check(text)
    assert not ok and "not one JSON object" in reason, reason


def test_off_list_is_refused_by_the_check():
    text, why, secs, m, srv = ask("off_list")
    ok, reason = check(text)
    assert not ok and "not on this incident's list" in reason, reason


def test_unsafe_is_refused_as_a_safety_change():
    text, why, secs, m, srv = ask("unsafe")
    ok, reason = check(text)
    assert not ok and reason.startswith(SAFETY), reason


def test_slow_gives_up_at_the_deadline():
    text, why, secs, m, srv = ask("slow", deadline=3)
    assert text is None and "within its 3 s deadline" in why, why
    assert 2.8 <= secs < 4.5, secs
    polls = [r for r in srv.requests if r["method"] == "GET"]
    assert len(polls) >= 5, len(polls)                     # it kept polling until the deadline


def test_429_backs_off_then_gets_the_same_plan():
    text, why, secs, m, srv = ask("429", n429=2)
    assert text and check(text)[0], (text, why)
    assert m.last["http"] == [429, 429, 202] and secs >= 1.9, (m.last, secs)   # Retry-After: 1, twice


def test_429_past_the_deadline_gives_up():
    text, why, secs, m, srv = ask("429", n429=100, deadline=3)
    assert text is None and "didn't take the run" in why and secs < 4.5, (why, secs)


def test_500_fails_fast():
    text, why, secs, m, srv = ask("500")
    assert text is None and "HTTP 500" in why and secs < 1.5, (why, secs)
    assert len([r for r in srv.requests if r["method"] == "POST"]) == 1


def test_a_failed_run_gives_up():
    text, why, secs, m, srv = ask("failed")
    assert text is None and "ended failed" in why, why


def test_connection_refused_fails_fast():
    text, why, secs, m, srv = ask("refused")
    assert text is None and "isn't reachable" in why and secs < 1.5, (why, secs)


def test_a_wrong_key_is_401_and_the_right_one_works():
    srv, url = fake_manager.start("good", key="s3cret")
    try:
        text, why = ManagerClient(url, key="wrong", poll_s=0.1).ask(ESC, deadline_s=5)
        assert text is None and "HTTP 401" in why, why
        text, why = ManagerClient(url, key="s3cret", poll_s=0.1).ask(ESC, deadline_s=5)
        assert text and check(text)[0], why
    finally:
        srv.shutdown()


def test_a_retried_post_gets_the_same_run_back():
    srv, url = fake_manager.start("good", delay=0.0)
    try:
        m = ManagerClient(url, poll_s=0.1)
        m.ask(ESC, deadline_s=5)
        first = m.last["run_id"]
        m.ask(ESC, deadline_s=5)
        assert m.last["run_id"] == first and m.last["http"] == [202], m.last
        text, why = m.ask({**ESC, "deadline_s": 30}, deadline_s=5)      # same key, another body: 409
        assert text is None and "HTTP 409" in why, why
    finally:
        srv.shutdown()


def test_the_dispatcher_fake_is_the_same_server_over_http():
    m, srv = fake_client("good", delay=0.0)
    try:
        text, why = m.ask(ESC, deadline_s=5)
        assert m.label == "fake" and m.url.startswith("http://127.0.0.1:") and check(text)[0], (m.url, why)
    finally:
        srv.shutdown()


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            t0 = time.time()
            fn()
            print(f"ok  {name}  ({time.time() - t0:.1f} s)")
