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
        "case_file": {
            "kind": "ultrasonic_frozen",
            "runs": [{"id": "run:pod-1/r1", "verdict": "fail"}],
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
    assert (
        "Port prototypes/arm-sim/armsim/level1.py:30-44 into "
        "firmware/healer/healer/detect.py and arm/safe_replay.py."
    ) in page
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
