"""services/digest and services/overnight: the window, the DB read, the overnight flag."""

from datetime import UTC, datetime, timedelta

import pytest

from romulus_cloud import events
from romulus_cloud.errors import ApiError
from romulus_cloud.models import Blob, Device, FleetState
from romulus_cloud.services import digest, overnight


@pytest.mark.parametrize(
    "text,expected",
    [
        ("1h", timedelta(hours=1)),
        ("12h", timedelta(hours=12)),
        ("30m", timedelta(minutes=30)),
        ("2d", timedelta(days=2)),
        (" 90s ", timedelta(seconds=90)),
        ("1.5h", timedelta(minutes=90)),
    ],
)
def test_parse_since_durations(text, expected, now):
    assert digest.parse_since(text, now) == now - expected


def test_parse_since_iso(now):
    assert digest.parse_since("2026-11-08T00:30:00-08:00", now) == datetime(
        2026, 11, 8, 8, 30, tzinfo=UTC
    )
    assert digest.parse_since("2026-11-08T08:30:00", now) == datetime(
        2026, 11, 8, 8, 30, tzinfo=UTC
    )  # naive means UTC


def test_parse_since_rejects_nonsense(now):
    with pytest.raises(ApiError) as exc:
        digest.parse_since("yesterday", now)
    assert exc.value.status == 422 and "30m, 12h or 2d" in exc.value.detail


def _ev(session, kind, ts, actor_class="automation", **refs):
    events.record(session, "gate", actor_class, kind, refs, f"{kind} happened", ts=ts)


def test_report_carries_the_fleet_doctor_metrics(db_session, now):
    data = {"incident_kind": "camera_occluded", "cost_usd": 0.004}
    when = now - timedelta(hours=1)
    events.record(db_session, "fleet-doctor", "agent", "doctor.asked", {}, "asked", ts=when)
    events.record(
        db_session, "fleet-doctor", "agent", "doctor.advised", {}, "advised", data=data, ts=when
    )
    db_session.flush()
    d = digest.report(db_session, now).to_dict()
    assert d["doctor_asks"] == 1 and d["doctor_consults"] == 0
    assert d["doctor_spend_usd"] == 0.004
    assert d["doctor_by_kind"] == {"unknown": {**dict.fromkeys(_METRICS, 0), "asks": 1}}
    assert "Fleet Doctor: 1 asks" in digest.report(db_session, now).text()


_METRICS = (
    "asks",
    "consults",
    "proposals_offered",
    "proposals_applied",
    "consults_held",
    "gave_up",
)


def test_report_counts_the_window_and_old_waiting_orders(db_session, now):
    _ev(db_session, "incident.reported", now - timedelta(hours=13))  # before the window
    old_order = {"work_order": "wo_1", "status": "needs_human"}
    _ev(db_session, "workorder.created", now - timedelta(hours=20), **old_order)
    _ev(db_session, "incident.reported", now - timedelta(hours=2))
    _ev(db_session, "incident.agent_acted", now - timedelta(hours=2), "agent")
    _ev(db_session, "candidate.promoted", now - timedelta(hours=1))
    db_session.add(Blob(sha256="a" * 64, size_bytes=1000, path="a" * 64))
    seen = now - timedelta(minutes=5)
    db_session.add_all(
        [
            Device(id="sim-1", kind="sim", last_seen_at=seen),
            Device(id="sim-4", kind="unknown", last_seen_at=seen),  # auto-registered
            Device(id="arm-1", kind="real", last_seen_at=seen),
            Device(id="arm-2", kind="real", last_seen_at=now - timedelta(days=1)),  # idle
        ]
    )
    db_session.flush()

    r = digest.report(db_session, now, budget_gb=1)
    assert r.digest.since == now - timedelta(hours=12)
    assert r.digest.incidents == 1 and r.digest.candidates_promoted == 1
    assert r.digest.actions_by_class["agent"] == 1
    assert r.digest.actions_by_class["automation"] == 1
    assert [w.work_order for w in r.digest.waiting_for_person] == ["wo_1"]
    assert r.blob_bytes == 1000 and r.budget_bytes == 1024**3

    d = r.to_dict()
    assert d["incidents"] == 1 and d["no_human_share"] == 1.0
    assert d["storage"] == {"blob_bytes": 1000, "budget_bytes": 1024**3}
    assert d["waiting_for_person"][0]["status"] == "needs_human"
    text = r.text("America/Los_Angeles")
    assert "In 12.0 h on 1 simulated arms + 1 real + 1 of unknown kind: 1 incidents" in text
    assert "0 human actions, 1 waiting for a person" in text
    assert d["fleet"] == {"simulated": 1, "real": 1, "unknown": 1}
    assert "PST" in text and "Blob store: 1000 B of a 1.0 GB budget (under)" in text

    assert digest.report(db_session, now, since="3h", until="90m").digest.incidents == 1
    assert digest.report(db_session, now, since="1h").digest.incidents == 0


def test_overnight_flag_sets_the_default_window(db_session, now):
    assert overnight.started_at(db_session) is None
    start = now - timedelta(hours=7)
    assert overnight.start(db_session, start, "kent") == start
    assert overnight.start(db_session, now, "kent") == start  # once
    assert overnight.started_at(db_session) == start
    (event,) = events.find(db_session, {}, kinds=[overnight.STARTED])
    assert event.actor == "human:kent" and event.actor_class == "human"

    r = digest.report(db_session, now)
    assert r.digest.since == start and r.overnight_started_at == start
    assert [h.actor for h in r.digest.human_actions] == ["human:kent"]  # the launch itself

    assert overnight.stop(db_session, now, "kent") is True
    assert overnight.stop(db_session, now, "kent") is False
    assert db_session.get(FleetState, "default").overnight_started_at is None
    assert len(events.find(db_session, {}, kinds=[overnight.STOPPED])) == 1
