"""The Fleet learning page (docs/recursive-self-improvement/demo.md §4) renders its three
states through HTTP: nothing yet (an empty state), lane A's recorded session plus the probe
(the staged safety hero), and a `review.accepted` skill whose candidate the gate promoted
(`0 clicks`). Every number on the page is computed from the probe or the events."""

import html
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from romulus_cloud import events
from romulus_cloud.domain import review as rv
from romulus_cloud.models import Candidate, Device
from romulus_cloud.services import learning

VERDICT = "A distance that never changes is a frozen sensor, not a clear path."
REVIEW_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "review"


def _text(resp) -> str:
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/html")
    return html.unescape(resp.text)


def _probe(**changes) -> dict:
    """A probe scorecard in lane A's shape (`cloud/scripts/lesson_probe.py`)."""
    probe = {
        "probe": "us-frozen-port",
        "kind": "ultrasonic_frozen",
        "port": {
            "from": "prototypes/arm-sim/armsim/level1.py:30-44",
            "to": ["firmware/healer/healer/detect.py", "arm/safe_replay.py"],
        },
        "before": {"caught": 0, "of": 120, "false_alarms": 2, "healthy": 600},
        "after": {"caught": 120, "of": 120, "false_alarms": 2, "healthy": 600},
        "limits": ["still misses a 3.5 s freeze (4 of 30)"],
        "command": "uv run python cloud/scripts/lesson_probe.py",
        "rev": "abc1234",
        "generated_at": datetime.now(UTC).isoformat(),
        "seconds": 0.74,
    }
    return probe | changes


def _recorded_session() -> dict:
    """A recorded cluster-review session in lane A's fixture shape (us_frozen_live.json)."""
    return {
        "session_id": "ses_test_frozen",
        "captured_at": "2026-11-08T17:31:00Z",
        "duration_s": 31.4,
        "cost_usd": 0.0712,
        "case_file": {  # what domain.review.check reads (lane A's case-file shape)
            "case": "review:us_frozen:test",
            "kind": "us_frozen",
            "runs": [
                {
                    "id": "run:pod-1/r1",
                    "verdict": "unverified",
                    "verifier": "device says fixed; nothing checked it",
                    "device_kind": "sim",
                }
            ],
            "probes": [{"id": "us-frozen-port"}],
            "device_classes": ["sensor_pod", "so101"],
        },
        "schema": {"type": "object"},
        "output": {
            "gap": "missed_detection",
            "title": "Port the arm-sim watchdog's frozen-echo check to the pods",
            "pattern": VERDICT,
            "evidence": ["run:pod-1/r1"],
            "applies_to": ["sensor_pod"],
            "proposal": {"kind": "rule", "probe": "us-frozen-port"},
            "confidence": 0.86,
        },
    }


@pytest.fixture
def learning_settings(app, tmp_path):
    """Point the running app at a tmp probe dir and (optionally) a recorded session."""
    probes = tmp_path / "probes"
    probes.mkdir()

    def configure(*, fixture: dict | None = None, probe: dict | None = None):
        if probe is not None:
            (probes / "probe_us_frozen.json").write_text(json.dumps(probe))
        path = None
        if fixture is not None:
            path = tmp_path / "us_frozen_live.json"
            path.write_text(json.dumps(fixture))
        update = {"probe_dir": str(probes), "review_fixture": str(path) if path else None}
        app.state.settings = app.state.settings.model_copy(update=update)

    return configure


def test_empty_state_shows_the_data_strip_and_no_review(client, learning_settings):
    learning_settings()
    page = _text(client.get("/learning"))
    assert '<a class="nav" href="/learning">Learning</a>' in page
    assert ".tc-verdict-text {" in page and ".tc-stages {" in page  # the Technician look
    assert "fleet-doctor" in page and "cluster review" in page
    assert "No cluster review yet" in page and "no cluster review yet" in page
    assert "THE FLEET'S OWN DATA" in page.upper()
    assert "heal runs" in page and "training rows" in page
    assert "Repeat rate after a change: <b>—</b>" in page
    assert "NEXT, not built" in page
    assert 'hx-get="/partials/learning"' in page and "every 3s" in page
    assert "recorded Naïve session" not in page and "staged for" not in page

    partial = _text(client.get("/partials/learning"))
    assert "<html" not in partial and "No cluster review yet" in partial


def test_recorded_session_and_probe_show_the_staged_hero(client, seeded, learning_settings):
    learning_settings(fixture=_recorded_session(), probe=_probe())
    page = _text(client.get("/learning"))
    # Head: measured duration, cost, and the real end state.
    assert "31 s" in page and "7¢" in page and "staged for Olir" in page
    assert "recorded Naïve session · ses_test_frozen" in page and "is-recorded" in page
    # Stages: three done, the engineer is next; each has a mark or word.
    assert page.count('class="st-done"') == 3 and 'class="st-active"' in page
    assert "waiting on its engineer" in page and ">now<" in page
    # The lesson is the proposal's pattern, the number is computed from the probe.
    assert f'<h1 class="tc-verdict-text">{VERDICT}</h1>' in page
    assert "0 → 120 of 120 freezes caught" in page
    assert "no new false alarms on 600 healthy windows" in page
    assert "safety code → staged for Olir (D25)" in page
    assert "still misses a 3.5 s freeze (4 of 30)" in page
    assert "staged, not shipped" in page and "touches safety" in page
    assert 'class="lr-sim"' in page and "1 real arm · 3 simulated" in page  # the seeded fleet
    # Below the fold: probe command and rev, the staged patch (from the probe, not the model).
    assert "uv run python cloud/scripts/lesson_probe.py" in page and "abc1234" in page
    # The staged patch is domain.review's sentence from the probe's port spec, not the model's.
    assert rv.port_sentence(_probe()) in page
    assert "Staged for Olir (D25): the rule touches arm/safe_replay.py" in page  # result.why
    assert '"gap": "missed_detection"' in page  # the typed proposal
    assert "from a recorded session (fixture)" in page


def test_lane_a_committed_session_and_probe_render_as_recorded(client, seeded, app):
    """The files the learn stack points at: review_spike.py's live session and
    lesson_probe.py's scorecard (with its 550 kB input windows in the same folder)."""
    live = json.loads((REVIEW_FIXTURES / "us_frozen_live.json").read_text())
    probe = json.loads((REVIEW_FIXTURES / "probe_us_frozen.json").read_text())
    app.state.settings = app.state.settings.model_copy(
        update={
            "review_fixture": str(REVIEW_FIXTURES / "us_frozen_live.json"),
            "probe_dir": str(REVIEW_FIXTURES),
        }
    )
    page = _text(client.get("/learning"))
    proposal = live["structured_output"]
    assert f'<h1 class="tc-verdict-text">{proposal["title"]}</h1>' in page
    assert learning.duration_label(live["duration_s"]) in page
    assert learning.cost_label(live["cost_usd"]) in page
    assert f"recorded Naïve session · {live['session_id']}" in page
    assert "staged for Olir" in page and "safety code → staged for Olir (D25)" in page
    b, a, of = probe["before"]["caught"], probe["after"]["caught"], probe["after"]["of"]
    assert f"{b} → {a} of {of} freezes caught" in page
    assert f"no new false alarms on {probe['after']['healthy']} healthy windows" in page
    assert "limit: a 3.5 s freeze" in page
    assert "checks passed in code" in page and "✓ the proposal validates" in page
    assert proposal["evidence"][0] in page and ">unverified<" in page


def test_the_number_is_computed_from_before_and_after():
    probe = _probe(
        before={"caught": 2, "of": 120, "false_alarms": 1},
        after={"caught": 117, "of": 120, "false_alarms": 2},
    )
    numbers = learning._numbers(probe)
    assert numbers["line"] == "2 → 117 of 120 freezes caught"
    assert numbers["alarms_line"] == "1 new false alarm"
    assert numbers["improved"] is False  # a new false alarm fails design §4.5 check 4


def test_a_promoted_skill_shows_lesson_to_every_arm_and_zero_clicks(
    client, v2, db_session, learning_settings, now
):
    learning_settings()
    cand = Candidate(
        id="cand_lesson1",
        fleet_id="default",
        type="harness",
        version=2,
        device_class="so101",
        status="promoted",
        canary_device="sim-2",
        stub_stages=["replay"],
        why="cluster review: skill gripper-reseat",
    )
    db_session.add(cand)
    for d in db_session.query(Device).all():
        if d.kind == "sim":
            d.running_harness = d.assigned_harness = 2
        else:
            d.assigned_harness = 2  # queued: the real arm hasn't pulled it yet
    t0 = now
    sid = "ses_skill_1"
    actor = {"actor": "agent:fleet-doctor", "actor_class": "agent"}
    events.record(
        db_session,
        **actor,
        kind="review.session_opened",
        refs={},
        ts=t0,
        why="Opened a cluster review session for gripper_stall.",
        data={"session_id": sid},
    )
    proposal = {
        "gap": "failed_heal",
        "title": "Reseat before retrying a stalled gripper",
        "pattern": "A gripper that stalls twice needs a reseat, not a third retry.",
        "evidence": ["inc:missing"],
        "applies_to": ["so101"],
        "proposal": {"kind": "skill", "slug": "gripper-reseat", "markdown": "Reseat first."},
        "confidence": 0.7,
    }
    result = {"outcome": "accepted", "failures": [], "touches_safety": False, "targets": []}
    events.record(
        db_session,
        **actor,
        kind="review.accepted",
        refs={"candidate": cand.id},
        ts=t0 + timedelta(seconds=30),
        why="Accepted the skill; built a harness candidate.",
        data={
            "proposal": proposal,
            "result": result,
            "session_id": sid,
            "duration_s": 30,
            "cost_usd": 0.07,
            "recorded": False,
        },
    )
    events.record(
        db_session,
        "gate",
        "automation",
        "candidate.promoted",
        {"candidate": cand.id},
        "The canary held; promoted harness v2 to so101.",
        ts=t0 + timedelta(seconds=160),
    )
    db_session.commit()

    page = _text(client.get("/learning"))
    assert "lesson → every arm" in page
    assert "2 min 40 s" in page  # session opened → promoted, canary included
    assert "7¢" in page
    assert '<span class="tc-num">0</span> clicks' in page
    assert page.count('class="st-done"') == 4
    assert "a sim arm tried it first" in page and "promoted by the gate" in page
    assert "A gripper that stalls twice needs a reseat, not a third retry." in page
    assert "the gate decides (D6)" in page
    assert "recorded Naïve session" not in page
    # Rollout: only installed devices get ✓; the real arm is queued.
    assert '<b>sim-1</b> <span class="lr-mark">✓</span> <span class="lr-tag">SIM</span>' in page
    assert '<span class="lr-tag">SIM·canary</span>' in page
    assert (
        '<b>arm-1</b> <span class="lr-mark">queued</span> <span class="lr-tag">REAL</span>' in page
    )
    assert "stub: a skill is prose" in page  # gate details say "stub" for skills
    assert "0 → " not in page  # no probe number for a skill


def test_labelled_counts_come_from_agent_runs(client, seeded, learning_settings):
    learning_settings()
    resp = client.post(
        "/v1/devices",
        json={"id": "pod-1", "kind": "real", "device_class": "sensor_pod", "by": "olir"},
    )
    assert resp.status_code == 201, resp.text
    started = datetime(2026, 11, 8, 10, 0, tzinfo=UTC)

    def report(run_id, state, status):
        body = {
            "run_id": run_id,
            "state": state,
            "started_at": started.isoformat(),
            "updated_at": (started + timedelta(seconds=30)).isoformat(),
            "agent": {"model": "qwen3.5:4b"},
            "outcome": {"status": status, "headline": f"{status} headline"},
            "steps": [{"n": 1, "action": "Ran triage", "effect": "No findings.", "ok": True}],
            "contract_version": "0.1",
        }
        if state == "finished":
            body["ended_at"] = (started + timedelta(minutes=3)).isoformat()
        return body

    for run_id, state, status in [
        ("r-broken", "finished", "broken"),
        ("r-fixed", "finished", "fixed"),  # the device says fixed; nothing checked it
        ("r-live", "running", "working"),
    ]:
        url = f"/v1/devices/pod-1/agent-runs/{run_id}"
        assert client.put(url, json=report(run_id, state, status)).status_code == 200

    page = _text(client.get("/learning"))
    assert 'labelled by a verifier</span><span class="lr-split">0 pass · 1 fail</span>' in page
    assert "Not labelled: 1 unverified" in page and "1 excluded" in page


def _review_event(db_session, kind, data, now, refs=None):
    events.record(
        db_session,
        "agent:fleet-doctor",
        "agent",
        kind,
        refs or {},
        f"Cluster review: {kind}.",
        data=data,
        ts=now,
    )
    db_session.commit()


def test_odd_list_fields_never_500(client, seeded, db_session, learning_settings, now):
    """A number where a list belongs (failures, evidence, port.to, limits) reads as empty."""
    learning_settings(probe=_probe(port={"from": "x.py", "to": 7}, limits=3))
    proposal = {"title": "T", "pattern": "P", "evidence": 3, "proposal": {"kind": "rule"}}
    data = {
        "proposal": proposal,
        "result": {"outcome": "staged", "failures": 5, "touches_safety": True, "targets": 1},
        "session_id": 12345,
        "probe_id": "us-frozen-port",
        "recorded": True,
        "case_file": {"runs": 9},
    }
    _review_event(db_session, "review.staged", data, now, refs={"candidate": 42})
    page = _text(client.get("/learning"))
    assert "staged for its owner" in page and "0 → 120 of 120 freezes caught" in page
    assert "recorded Naïve session · 12345" in page


def test_a_staged_skill_waits_on_its_owner(client, seeded, db_session, learning_settings, now):
    learning_settings()
    proposal = {
        "gap": "failed_heal",
        "title": "Reseat the visor cable before a reflash",
        "pattern": "A visor that drops twice needs its cable reseated.",
        "evidence": ["inc:x"],
        "proposal": {"kind": "skill", "slug": "visor-reseat", "markdown": "visor/ first."},
    }
    result = {"outcome": "staged", "failures": [], "touches_safety": True, "owner": "Olir"}
    data = {"proposal": proposal, "result": result, "session_id": "ses_s", "duration_s": 20}
    _review_event(db_session, "review.staged", data, now)
    page = _text(client.get("/learning"))
    assert "lesson → every arm" not in page and "a sim arm tried it first" not in page
    assert "waiting on its engineer" in page and "checked in code" in page
    assert "staged for Olir" in page and "safety code → staged for Olir (D25)" in page


def test_an_event_review_without_a_probe_id_shows_no_numbers(
    client, seeded, db_session, learning_settings, now
):
    """Another review's probe in PROBE_DIR is never borrowed."""
    learning_settings(probe=_probe())
    proposal = {"gap": "missed_detection", "title": "A rule", "proposal": {"kind": "rule"}}
    data = {"proposal": proposal, "result": {"outcome": "staged", "touches_safety": True}}
    _review_event(db_session, "review.staged", data, now)
    page = _text(client.get("/learning"))
    assert "A rule" in page and "freezes caught" not in page and "lesson_probe.py" not in page

    # It does use the probe its proposal cites.
    cited = {**proposal, "proposal": {"kind": "rule", "probe": "us-frozen-port"}}
    later = now + timedelta(seconds=5)
    _review_event(db_session, "review.staged", {**data, "proposal": cited}, later)
    assert "0 → 120 of 120 freezes caught" in _text(client.get("/learning"))


# --- R2's review events, keyed by refs.review (services/review.py, PR #82) ---------------------

US = "review:us_frozen:1"
SERVO = "review:servo_read_error:1"


def _r2(db_session, kind, case_id, ts, data, candidate=None):
    """One event as R2's service writes it: refs.review is the case, data carries `kind`."""
    refs = {"review": case_id, "naive_session": data["session_id"]}
    if candidate:
        refs["candidate"] = candidate
    events.record(
        db_session, "agent:fleet-doctor", "agent", kind, refs, f"{kind} {case_id}", data=data, ts=ts
    )


def _two_reviews(db_session, now):
    """us_frozen staged for Olir at t0; servo_read_error opened at t0+60 s, accepted at
    +90 s, its harness candidate promoted at +240 s (the canary included)."""
    us_case = {"runs": [{"id": "run:sim-pod-1/r1", "verdict": "unverified"}]}
    _r2(
        db_session,
        "review.session_opened",
        US,
        now,
        {"session_id": "ses_us", "kind": "us_frozen", "case_file": us_case, "recorded": True},
    )  # noqa: E501
    us = {
        "proposal": {
            "gap": "missed_detection",
            "title": "Port frozen-echo check into pod detect.py and arm start gate",
            "evidence": ["run:sim-pod-1/r1"],
            "proposal": {"kind": "rule", "probe": "us-frozen-port"},
        },
        "result": {"outcome": "staged", "touches_safety": True, "owner": "Olir", "lane": "rule"},
        "session_id": "ses_us",
        "duration_s": 12,
        "cost_usd": 0.03,
        "probe_id": "us-frozen-port",
        "case_id": US,
        "kind": "us_frozen",
        "recorded": True,
    }
    _r2(db_session, "review.staged", US, now + timedelta(seconds=12), us)
    cand = Candidate(
        id="cand_servo",
        fleet_id="default",
        type="harness",
        version=2,
        device_class="so101",
        status="promoted",
        canary_device="sim-2",
        stub_stages=["replay"],
        why="cluster review: skill servo-read-retry",
    )
    db_session.add(cand)
    t1 = now + timedelta(seconds=60)
    _r2(
        db_session,
        "review.session_opened",
        SERVO,
        t1,
        {"session_id": "ses_servo", "kind": "servo_read_error", "recorded": True},
    )  # noqa: E501
    servo = {
        "proposal": {
            "gap": "failed_heal",
            "title": "Retry a flaky servo read before giving up",
            "pattern": "One flaky servo read is noise; retry it before you stop the loop.",
            "evidence": ["inc:missing"],
            "proposal": {"kind": "skill", "slug": "servo-read-retry", "markdown": "Retry."},
        },
        "result": {"outcome": "accepted", "touches_safety": False, "lane": "skill"},
        "session_id": "ses_servo",
        "duration_s": 30,
        "cost_usd": 0.07,
        "probe_id": None,
        "case_id": SERVO,
        "kind": "servo_read_error",
        "recorded": True,
    }
    _r2(db_session, "review.accepted", SERVO, t1 + timedelta(seconds=30), servo, cand.id)
    events.record(
        db_session, "gate", "automation", "candidate.promoted", {"candidate": cand.id},
        "The canary held; promoted harness v2 to so101.", ts=t1 + timedelta(seconds=240),
    )  # fmt: skip
    db_session.commit()
    return cand


def test_the_switcher_pins_a_review_and_keeps_polling_on_it(
    client, v2, db_session, learning_settings, now
):
    learning_settings(probe=_probe())
    _two_reviews(db_session, now)

    page = _text(client.get("/learning"))  # the newest: the promoted skill
    assert "lesson → every arm" in page and "4 min" in page  # session opened → promoted
    assert 'aria-label="Recent reviews"' in page
    assert "<b>servo read error</b> · promoted · 4 min · 7¢" in page
    assert "<b>us frozen</b> · staged · 12 s · 3¢" in page
    assert 'href="/learning?case=review:us_frozen:1"' in page
    assert 'hx-get="/partials/learning"' in page

    pinned = _text(client.get("/learning", params={"case": US}))
    assert "staged for Olir" in pinned and "lesson → every arm" not in pinned
    assert "0 → 120 of 120 freezes caught" in pinned
    assert 'hx-get="/partials/learning?case=review:us_frozen:1"' in pinned
    assert (
        'class="lr-review is-staged is-current" href="/learning?case=review:us_frozen:1"' in pinned
    )

    partial = _text(client.get("/partials/learning", params={"case": US}))
    assert "staged for Olir" in partial and "<html" not in partial
    # An unknown case falls back to the newest.
    assert "lesson → every arm" in _text(client.get("/learning", params={"case": "nope"}))


def test_a_mirrored_skill_shows_its_naive_skills_version(
    client, v2, db_session, learning_settings, now
):
    learning_settings()
    cand = _two_reviews(db_session, now)
    page = _text(client.get("/learning"))
    assert "mirrored to Naïve Skills" not in page
    events.record(
        db_session,
        "agent:fleet-doctor",
        "agent",
        "review.published",
        {"candidate": cand.id, "harness": 2},
        "Mirrored the healer playbook to Naïve Skills as romulus-healer-playbook v2.",
        data={"skill_id": "skl_1", "slug": "romulus-healer-playbook", "version": 2, "url": None},
        ts=now + timedelta(seconds=300),
    )
    db_session.commit()
    page = _text(client.get("/learning"))
    assert "✓ mirrored to Naïve Skills · v2" in page
    assert "romulus-healer-playbook v2 · mirrored after the gate promoted it" in page


def test_evidence_shows_labelled_runs_from_runs_load(
    client, seeded, db_session, learning_settings, now
):
    from romulus_cloud.services import runs as runs_service

    learning_settings()
    body = {"id": "pod-9", "kind": "sim", "device_class": "sensor_pod", "by": "test"}
    assert client.post("/v1/devices", json=body).status_code == 201
    started = datetime(2026, 11, 8, 10, 0, tzinfo=UTC)
    report = {
        "run_id": "r-1",
        "state": "finished",
        "started_at": started.isoformat(),
        "updated_at": (started + timedelta(seconds=30)).isoformat(),
        "ended_at": (started + timedelta(minutes=3)).isoformat(),
        "agent": {"model": "qwen3.5:4b"},
        "outcome": {"status": "broken", "headline": "broken headline"},
        "steps": [{"n": 1, "action": "Ran triage", "effect": "No findings.", "ok": True}],
        "contract_version": "0.1",
    }
    assert client.put("/v1/devices/pod-9/agent-runs/r-1", json=report).status_code == 200
    run = next(r for r in runs_service.load(db_session) if r.id == "run:pod-9/r-1")
    proposal = {"title": "A rule", "evidence": [run.id], "proposal": {"kind": "rule"}}
    _r2(
        db_session,
        "review.staged",
        US,
        now,
        {
            "session_id": "s",
            "proposal": proposal,
            "result": {"outcome": "staged", "touches_safety": True},
        },
    )  # noqa: E501
    db_session.commit()
    page = _text(client.get("/learning"))
    assert f'<a class="mono" href="/agent-runs/pod-9/r-1">{run.id}</a>' in page
    assert f'<span class="chip">{run.verdict}</span> <span class="lr-sim">SIM</span>' in page
    assert f'<span class="lr-verifier">{run.verifier}</span>' in page
    assert "labelled runs (the verdict table)" in page
