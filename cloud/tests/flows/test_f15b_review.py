"""F15b · Fleet learning, cluster review (docs/recursive-self-improvement/design.md §4, §5, §9;
draft D31): parts 2-4 of F15.

The fleet is the learning demo's (`cloud/scripts/learning_demo.py`, run unmodified): the seed
plus sim-pod-1/2 as `sensor_pod`, and its runs, each a harness transcript turned into a
report by the unmodified `edge_bridge.agent_runs.build()` and sent through the device API.
Two kinds qualify: `servo_read_error` (sim-1 failed, sim-3 passed: transfer) and `us_frozen`
(sim-pod-1 unverified, and the probe names the arm watchdog's detector: transfer).

Each review replays a recorded Naïve session (`learning_mode=fixture`): the bundled
`fixtures/review/<kind>.json`, or a hand-built proposal for the rejection. Then:
2. the servo_read_error skill becomes harness v2 for so101 → stub replay → canary sim-2 →
   promoted to every so101 device, and the pods' harness doesn't move;
3. the frozen rule touches `arm/safe_replay.py`: staged for Olir, no candidate;
4. a proposal citing an invented run is rejected, and the kind cools down.
"""

import importlib.util
import json
import sys
from datetime import timedelta
from functools import partial
from pathlib import Path

import pytest

from romulus_cloud import events, worker
from romulus_cloud.models import Candidate, Device, FleetState, HarnessVersion
from romulus_cloud.services import review
from romulus_cloud.worker import Step, run_tick

S = timedelta(seconds=1)
CLOUD = Path(__file__).resolve().parents[2]
FIXTURES = CLOUD / "tests" / "fixtures" / "review"
SKILL_RUNS = ("run:sim-1/learn-d3-qwen-watch", "run:sim-3/learn-d3-opus-arm-heal")
FROZEN_RUN = "run:sim-pod-1/learn-frozen-ollama-watch"


def _demo_module():
    spec = importlib.util.spec_from_file_location(
        "learning_demo", CLOUD / "scripts" / "learning_demo.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["learning_demo"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(autouse=True)
def _fresh_clients(monkeypatch):
    """Recorded sessions are kept per process; each test starts with none."""
    monkeypatch.setattr(review, "_clients", {})


@pytest.fixture
def learn(make_settings, tmp_path, now):
    probes = tmp_path / "probes"
    probes.mkdir()
    probe = json.loads((FIXTURES / "probe_us_frozen.json").read_text())
    probe["generated_at"] = (now - timedelta(hours=1)).isoformat()
    (probes / "probe_us_frozen.json").write_text(json.dumps(probe))
    return make_settings(learning_mode="fixture", probe_dir=str(probes))


@pytest.fixture
def fleet(seeded, device_http, tmp_path, clock, now, capsys):
    """The learning demo's pods and runs, sent the way its `seed` sends them."""
    demo = _demo_module()
    clock.at(now)
    demo.register_pods(device_http)
    t = now.timestamp()
    ctx = {"frozen": demo.frozen_window(t - 15 * 60)}
    for run in demo.RUNS:
        assert demo.send(device_http, run, t, ctx, tmp_path / "runs") == "sent"
    capsys.readouterr()
    return demo


def _review(sessions, learn, t, **kw):
    results = run_tick(
        [Step("review", partial(review.tick, settings=learn, **kw))], sessions, t, {}
    )
    assert results == {"review": "ok"}, results


def _kinds(db_session, case_id=None):
    db_session.expire_all()
    evs = review.review_events(db_session, "default")
    return [(e.kind, e.refs.get("review")) for e in evs if case_id in (None, e.refs.get("review"))]


def _outcome(db_session, kind):
    db_session.expire_all()
    (ev,) = [
        e
        for e in review.review_events(db_session, "default")
        if e.kind in review.OUTCOMES and (e.data or {}).get("kind") == kind
    ]
    return ev


def test_part2_a_skill_goes_stub_replay_canary_promote(
    fleet, learn, sessions, db_session, arms, tick_arms, work, now, count_events, monkeypatch
):
    tick_arms(now)
    tick_arms(now + 10 * S)  # every arm runs harness v1
    pods_before = {
        d.id: d.assigned_harness
        for d in db_session.query(Device).filter(Device.device_class == "sensor_pod")
    }

    # Transfer: sim-1 failed servo_read_error, sim-3's fix passed the bench check.
    _review(sessions, learn, now + 20 * S)
    salt = review.case_salt(db_session, "default")
    case_id = f"review:servo_read_error:{salt}:1"
    assert len(salt) == 6 and _kinds(db_session) == [("review.session_opened", case_id)]
    opened = db_session.scalars(events.find_query({"review": case_id}, kinds=[review.OPENED])).one()
    case = opened.data["case_file"]
    assert {r["id"] for r in case["runs"]} == set(SKILL_RUNS)
    assert case["handler"]["run"] == SKILL_RUNS[1] and case["probes"] == []
    assert opened.data["request"]["metadata"]["romulus_case"] == case_id
    assert opened.data["request"]["structured_output_required"] is True
    assert opened.refs.get("incident")  # sim-1's incident

    # The recorded proposal: a skill naming no safety path → accepted → a harness candidate.
    _review(sessions, learn, now + 25 * S)
    ev = _outcome(db_session, "servo_read_error")
    assert ev.kind == "review.accepted" and ev.actor == "agent:fleet-doctor"
    assert ev.actor_class == "agent"
    assert set(ev.data) == {
        "proposal",
        "result",
        "session_id",
        "duration_s",
        "cost_usd",
        "probe_id",
        "case_id",
        "kind",
        "recorded",
    }
    assert ev.data["recorded"] is True and ev.data["duration_s"] == 5.0
    assert ev.data["result"]["outcome"] == "accepted" and ev.data["result"]["lane"] == "skill"
    assert ev.data["result"]["targets"] == ["skills/servo-read-retry.md"]
    cand = db_session.get(Candidate, ev.refs["candidate"])
    assert cand.type == "harness" and cand.device_class == "so101" and cand.from_overlays == []
    assert cand.version == 2 and not cand.touches_safety
    hv = db_session.get(HarnessVersion, ("default", 2))
    assert hv.parent_version == 1 and hv.source == "review"
    (created,) = events.find(db_session, {"candidate": cand.id}, kinds=["candidate.created"])
    assert created.data["skill"]["slug"] == "servo-read-retry"
    assert created.data["proposal"]["evidence"] == list(SKILL_RUNS)

    # The gate takes it with no extra wiring: stub replay, canary on sim-2, then promote.
    work(now + 41 * S)
    db_session.expire_all()
    assert cand.status == "canary" and cand.canary_device == "sim-2"
    assert cand.replay["stub"] and "replay" in cand.stub_stages
    assert ("harness", 2, "installed") in tick_arms(now + 50 * S)["sim-2"]
    for t in (90, 130, 155):
        tick_arms(now + t * S)
        work(now + t * S + S)
    work(now + 162 * S)
    db_session.expire_all()
    assert db_session.get(Candidate, cand.id).status == "promoted"
    assert count_events("candidate.promoted", {"candidate": cand.id}) == 1
    devs = {d.id: d for d in db_session.query(Device)}
    assert all(d.assigned_harness == 2 for d in devs.values() if d.device_class == "so101")
    assert {i: devs[i].assigned_harness for i in pods_before} == pods_before  # pods untouched

    # T1: the learning tick then mirrors the promoted skill to Naïve Skills (FakeNaive).
    publish = learn.model_copy(update={"review_publish": True})
    monkeypatch.setattr(worker, "get_settings", lambda: publish)
    (step,) = [s for s in worker.steps_for(publish) if s.name == "learning.tick"]
    assert run_tick([step], sessions, now + 170 * S, {}) == {"learning.tick": "ok"}
    assert count_events("review.published", {"candidate": cand.id}) == 1


def test_part3_the_frozen_rule_is_staged_with_no_candidate(fleet, learn, sessions, db_session, now):
    for i in range(4):  # servo_read_error opens and settles first; then us_frozen
        _review(sessions, learn, now + (20 + 5 * i) * S)
    ev = _outcome(db_session, "us_frozen")
    result = ev.data["result"]
    assert ev.kind == "review.staged" and result["outcome"] == "staged"
    assert result["touches_safety"] and result["owner"] == "Olir" and result["lane"] == "rule"
    assert result["targets"] == ["firmware/healer/healer/detect.py", "arm/safe_replay.py"]
    assert result["scorecard"] == "before 0/120 caught · after 120/120 · false alarms 0→0"
    assert result["staged_patch"].startswith("Port `prototypes/arm-sim/armsim/level1.py:30-44")
    assert ev.data["probe_id"] == "us-frozen-port" and ev.data["recorded"] is True
    assert ev.data["proposal"]["evidence"] == [FROZEN_RUN]
    assert "candidate" not in ev.refs
    assert db_session.query(Candidate).filter(Candidate.type == "firmware").count() == 0


def test_part3_a_passing_rule_off_safety_is_still_staged(fleet, learn, sessions, db_session, now):
    """D31: no firmware candidate is ever built from a rule; the rule lane is T2."""
    probe = json.loads(next(Path(learn.probe_dir).glob("*.json")).read_text())
    probe["port"]["to"] = ["firmware/healer/healer/detect.py"]
    (Path(learn.probe_dir) / "probe_us_frozen.json").write_text(json.dumps(probe))
    for i in range(4):
        _review(sessions, learn, now + (20 + 5 * i) * S)
    ev = _outcome(db_session, "us_frozen")
    assert ev.kind == "review.staged" and not ev.data["result"]["touches_safety"]
    assert "rule lane isn't built yet (T2)" in ev.data["result"]["why"]
    assert ev.data["result"]["owner"] == "Olir" and "candidate" not in ev.refs
    assert db_session.query(Candidate).count() == 1  # the servo_read_error skill only


def test_part4_an_invented_run_is_rejected_and_the_kind_cools_down(
    fleet, learn, sessions, db_session, now
):
    from romulus_cloud.adapters.naive import FixtureNaive

    recorded = json.loads((FIXTURES / "servo_read_error.json").read_text())
    output = recorded["session"]["structured_output"]
    output["evidence"] = ["run:sim-1/never-happened"]
    naive = FixtureNaive(recorded, now=lambda: now)

    _review(sessions, learn, now + 20 * S, naive=naive)
    _review(sessions, learn, now + 25 * S, naive=naive)
    ev = _outcome(db_session, "servo_read_error")
    assert ev.kind == "review.rejected"
    assert ev.data["result"]["failures"] == [
        "The evidence cites runs that aren't in the case file: run:sim-1/never-happened.",
        "No evidence run is a failed or unverified run from the case file.",
    ]
    assert "candidate" not in ev.refs and db_session.query(Candidate).count() == 0

    # It cools down: the next session is the other kind, not servo_read_error again.
    _review(sessions, learn, now + 30 * S)
    salt = review.case_salt(db_session, "default")
    assert _kinds(db_session)[-1] == ("review.session_opened", f"review:us_frozen:{salt}:1")
    _review(sessions, learn, now + 35 * S)
    cooling, _ = review.skip_sets(
        db_session,
        review.review_events(db_session, "default"),
        [],
        now + 40 * S,
        learn,
    )
    assert "servo_read_error" in cooling
    cooled, _ = review.skip_sets(
        db_session, review.review_events(db_session, "default"), [], now + 1000 * S, learn
    )
    assert "servo_read_error" not in cooled  # REVIEW_COOLDOWN_S (900) has passed
    assert "us_frozen" in cooled  # staged: it waits for its owner or a newer run


def test_frozen_fleet_and_caps(fleet, learn, sessions, db_session, now, make_settings):
    capped = make_settings(
        learning_mode="fixture", probe_dir=learn.probe_dir, review_max_sessions_per_hour=1
    )
    _review(sessions, capped, now + 20 * S)
    _review(sessions, capped, now + 25 * S)  # settles servo_read_error
    _review(sessions, capped, now + 30 * S)  # the cap: no second session this hour
    assert [k for k, _ in _kinds(db_session)].count(review.OPENED) == 1

    db_session.merge(FleetState(fleet_id="default", frozen=True, frozen_by="kent"))
    db_session.commit()
    _review(sessions, capped, now + 3700 * S)  # frozen (D11): nothing, even past the cap
    assert [k for k, _ in _kinds(db_session)].count(review.OPENED) == 1
    db_session.merge(FleetState(fleet_id="default", frozen=False))
    db_session.commit()
    _review(sessions, capped, now + 3705 * S)
    assert [k for k, _ in _kinds(db_session)].count(review.OPENED) == 2


def test_the_worker_step_runs_review(fleet, learn, sessions, db_session, now, monkeypatch):
    monkeypatch.setattr(worker, "get_settings", lambda: learn)
    (step,) = [s for s in worker.steps_for(learn) if s.name == "learning.tick"]
    assert run_tick([step], sessions, now + 20 * S, {}) == {"learning.tick": "ok"}
    assert _kinds(db_session)[-1][0] == review.OPENED


def test_a_session_with_no_proposal_in_time_is_cancelled_and_rejected(
    fleet, learn, sessions, db_session, now
):
    from romulus_cloud.adapters.naive import FixtureNaive

    slow = FixtureNaive(FIXTURES / "us_frozen_live.json", now=lambda: now, play_seconds=10_000)
    _review(sessions, learn, now + 20 * S, naive=slow)
    _review(sessions, learn, now + 300 * S, naive=slow)  # still running: waits
    assert [k for k, _ in _kinds(db_session)] == [review.OPENED]
    _review(sessions, learn, now + 640 * S, naive=slow)
    ev = _outcome(db_session, "servo_read_error")
    assert ev.kind == "review.rejected" and slow.cancelled == [ev.data["session_id"]]
    assert ev.data["result"]["failures"][0].startswith("The session ended without a proposal")


class _Refusing:
    """A Naïve whose session create fails for good (an unknown agent: 404)."""

    def find_session(self, agent_id, case_id):
        return None

    def create_session(self, body, *, idempotency_key):
        from romulus_cloud.adapters.naive import NaiveError

        raise NaiveError("agent fleet-doctor not found", status=404, code="not_found")


def test_a_session_naive_wont_start_is_rejected_and_the_step_survives(
    fleet, learn, sessions, db_session, now
):
    _review(sessions, learn, now + 20 * S, naive=_Refusing())  # the step stays "ok"
    ev = _outcome(db_session, "servo_read_error")
    assert ev.kind == "review.rejected" and ev.data["session_id"] is None
    assert ev.data["result"]["failures"] == [
        "Naïve wouldn't start the session (agent fleet-doctor not found)."
    ]
    assert [k for k, _ in _kinds(db_session)].count(review.OPENED) == 0
    _review(sessions, learn, now + 25 * S, naive=_Refusing())  # cools down: the other kind next
    assert _outcome(db_session, "us_frozen").kind == "review.rejected"
    salt = review.case_salt(db_session, "default")
    assert ev.refs["review"] == f"review:servo_read_error:{salt}:1"


def test_a_dead_session_waits_then_is_rejected_past_the_timeout(
    fleet, learn, sessions, db_session, now
):
    from romulus_cloud.adapters.naive import FixtureNaive, NaiveError

    class Dead(FixtureNaive):
        broken = False

        def get_session(self, session_id):
            if self.broken:
                raise NaiveError("upstream 502", status=502, code="bad_gateway")
            return super().get_session(session_id)

    dead = Dead(FIXTURES / "servo_read_error.json", now=lambda: now)
    _review(sessions, learn, now + 20 * S, naive=dead)
    dead.broken = True  # the session exists; now every read of it fails
    _review(sessions, learn, now + 300 * S, naive=dead)  # a Naïve error inside the window: wait
    assert [k for k, _ in _kinds(db_session)] == [review.OPENED]
    _review(sessions, learn, now + 640 * S, naive=dead)
    ev = _outcome(db_session, "servo_read_error")
    assert ev.kind == "review.rejected" and ev.data["cost_usd"] is None
    assert "didn't answer" in ev.data["result"]["failures"][0]


def test_the_case_salt_is_stable_and_differs_per_database(db_session, seeded):
    salt = review.case_salt(db_session, "default")
    assert salt == review.case_salt(db_session, "default")
    assert salt != review.case_salt(db_session, "another-fleet")  # no events: a fleet-id hash
