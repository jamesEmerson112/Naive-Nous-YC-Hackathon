from datetime import timedelta

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.dialects import postgresql

from romulus_cloud import events
from romulus_cloud.models import Device, Event


def _count(session) -> int:
    return session.scalar(select(func.count()).select_from(Event))


def test_record_writes_one_event_in_the_callers_transaction(db_session, now):
    evt = events.record(
        db_session,
        actor="device:arm-1",
        actor_class="agent",
        kind="incident.reported",
        refs={"incident": "0b6c", "device": "arm-1"},
        why="  arm-1 reported camera_occluded; the agent held a safe pose.  ",
        ts=now,
    )
    assert evt.id.startswith("evt_")
    assert evt.why == "arm-1 reported camera_occluded; the agent held a safe pose."
    assert db_session.in_transaction()
    row = db_session.scalars(select(Event).where(Event.id == evt.id)).one()
    assert row.ts == now and row.refs == {"incident": "0b6c", "device": "arm-1"}
    assert row.actor_class == "agent" and row.fleet_id == "default"


def test_record_does_not_commit(db_session, now):
    """Rolling back the caller's transaction discards both the change and its event."""
    before = _count(db_session)
    nested = db_session.begin_nested()
    db_session.add(Device(id="arm-9", kind="real"))
    events.record(
        db_session,
        "human:kent",
        "human",
        "device.first_seen",
        {"device": "arm-9"},
        "Kent registered arm-9.",
        ts=now,
    )
    assert _count(db_session) == before + 1
    nested.rollback()
    assert _count(db_session) == before
    assert db_session.get(Device, "arm-9") is None


def test_committed_event_survives_with_its_change(db_session, now):
    db_session.add(Device(id="sim-1", kind="sim"))
    events.record(
        db_session,
        "gate",
        "automation",
        "device.first_seen",
        {"device": "sim-1"},
        "sim-1 checked in for the first time.",
        ts=now,
    )
    db_session.commit()
    assert db_session.get(Device, "sim-1") is not None
    assert [e.kind for e in events.find(db_session, {"device": "sim-1"})] == ["device.first_seen"]


def test_find_matches_refs_containment_in_time_order(db_session, now):
    later = now + timedelta(seconds=5)
    events.record(
        db_session,
        "gate",
        "automation",
        "candidate.created",
        {"incident": "i-1", "candidate": "cand_1"},
        "Candidate made.",
        ts=later,
    )
    events.record(
        db_session,
        "device:arm-1",
        "agent",
        "incident.reported",
        {"incident": "i-1", "device": "arm-1"},
        "arm-1 reported it.",
        ts=now,
    )
    events.record(
        db_session,
        "device:arm-2",
        "agent",
        "incident.reported",
        {"incident": "i-2", "device": "arm-2"},
        "arm-2 reported it.",
        ts=now,
    )
    story = events.find(db_session, {"incident": "i-1"})
    assert [e.kind for e in story] == ["incident.reported", "candidate.created"]
    assert [e.kind for e in events.find(db_session, {"incident": "i-1", "device": "arm-1"})] == [
        "incident.reported"
    ]
    assert events.find(db_session, {"incident": "i-1"}, kinds=["candidate.created"])[0].ts == later
    assert events.find(db_session, {"incident": "nope"}) == []


def test_find_uses_jsonb_containment_and_the_gin_index(db_session):
    sql = str(events.find_query({"incident": "i-1"}).compile(dialect=postgresql.dialect()))
    assert "events.refs @> " in sql
    db_session.execute(text("SET LOCAL enable_seqscan = off"))
    plan = db_session.execute(
        text("EXPLAIN SELECT id FROM events WHERE refs @> CAST(:r AS jsonb)"),
        {"r": '{"incident": "i-1"}'},
    ).scalars()
    assert "ix_events_refs" in "\n".join(plan)


def test_ts_defaults_to_now_and_is_aware(db_session):
    evt = events.record(
        db_session, "gate", "automation", "batch.ran", {}, "Batch ran; nothing held."
    )
    assert evt.ts.tzinfo is not None


@pytest.mark.parametrize(
    "kwargs",
    [
        {"actor_class": "robot"},
        {"why": "   "},
        {"why": ""},
        {"kind": ""},
        {"actor": ""},
    ],
)
def test_record_rejects_bad_input(db_session, kwargs):
    args = {
        "actor": "gate",
        "actor_class": "automation",
        "kind": "batch.ran",
        "refs": {},
        "why": "Batch ran.",
    } | kwargs
    with pytest.raises(ValueError):
        events.record(db_session, **args)


def test_isolation_part_1_commit_inside_a_test(db_session):
    db_session.add(Device(id="leak-check", kind="test"))
    db_session.commit()
    assert db_session.get(Device, "leak-check") is not None


def test_isolation_part_2_previous_commit_was_rolled_back(db_session):
    assert db_session.get(Device, "leak-check") is None
