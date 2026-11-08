"""domain/digest.py: the overnight digest / autonomy ledger."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from romulus_cloud.domain.digest import ACTION_KINDS, EventView, render_text, summarize

T0 = datetime(2026, 11, 8, 0, 30, tzinfo=UTC)
T1 = T0 + timedelta(hours=6)


def ev(
    kind,
    actor_class="automation",
    actor="gate",
    minutes=10,
    why="did a thing",
    refs=None,
    data=None,
):
    return EventView(
        actor, actor_class, kind, T0 + timedelta(minutes=minutes), why, refs or {}, data
    )


def wo(kind, wo_id, minutes=10, why="wo", **refs):
    return ev(kind, "automation", "worker", minutes, why, {"work_order": wo_id, **refs})


def test_no_events():
    d = summarize([], T0, T1)
    assert d.total_events == 0 and d.actions == 0 and d.no_human_share is None
    assert d.actions_by_class == {"agent": 0, "automation": 0, "human": 0, "unknown": 0}
    text = render_text(d)
    assert "n/a" in text and "Human actions: 0" in text and "waiting for a person: 0" in text


def test_overnight_counts():
    events = [
        ev("incident.reported", "agent", "device:arm-1"),
        ev("incident.reported", "agent", "device:arm-2"),
        ev("recording.received", "agent", "device:arm-1"),
        ev("overlay.held", "agent", "device:arm-1"),
        ev("overlay.reverted", "agent", "device:arm-2"),
        ev("batch.ran"),
        ev("candidate.created", "agent", "agent:batch"),
        ev("candidate.promoted"),
        ev("candidate.rolled_back"),
        wo("workorder.created", "wo_1", status="pending"),
        wo("workorder.card_posted", "wo_1"),
        ev(
            "workorder.status_changed",
            "agent",
            "naive:ses_1",
            refs={"work_order": "wo_1", "from": "posted", "to": "done"},
        ),
    ]
    d = summarize(events, T0, T1)
    assert d.total_events == 12
    assert d.actions == 7  # intake (incident, recording, overlay held/reverted) excluded
    assert d.actions_by_class == {"agent": 2, "automation": 5, "human": 0, "unknown": 0}
    assert (d.incidents, d.heals, d.overlays_reverted) == (2, 1, 1)
    assert (d.candidates_created, d.candidates_promoted, d.candidates_rolled_back) == (1, 1, 1)
    assert (d.work_orders_created, d.work_orders_posted, d.freezes) == (1, 1, 0)
    assert d.no_human_share == 1.0 and d.human_actions == () and d.waiting_for_person == ()
    text = render_text(d)
    assert "Actions with no human: 100.0%" in text
    assert "Incidents: 2" in text and "1 promoted" in text and "1 rolled back" in text


def test_intake_only_night_has_no_share():
    events = [
        ev("incident.reported", "agent"),
        ev("recording.received", "agent"),
        ev("label.changed", "automation"),
        ev("device.first_seen", "automation"),
    ]
    d = summarize(events, T0, T1)
    assert d.actions == 0 and d.no_human_share is None and d.incidents == 1


def test_only_humans():
    events = [
        ev("fleet.frozen", "human", "human:kent", 5, "Kent froze the fleet"),
        ev("fleet.unfrozen", "human", "human:kent", 7, "Kent unfroze it"),
    ]
    d = summarize(events, T0, T1)
    assert d.no_human_share == 0.0 and d.freezes == 1
    assert [h.why for h in d.human_actions] == ["Kent froze the fleet", "Kent unfroze it"]
    text = render_text(d)
    assert "Actions with no human: 0.0%" in text and "Human actions: 2" in text
    assert "human:kent  fleet.frozen: Kent froze the fleet" in text


def test_human_intake_still_counts_as_human_action():
    d = summarize([ev("batch.ran"), ev("label.changed", "human", "human:kent")], T0, T1)
    assert d.actions == 2 and d.no_human_share == 0.5 and len(d.human_actions) == 1


def test_actor_class_is_case_insensitive():
    d = summarize([ev("batch.ran", "Human"), ev("batch.ran", " AGENT ")], T0, T1)
    assert d.actions_by_class["human"] == 1 and d.actions_by_class["agent"] == 1
    assert d.no_human_share == 0.5 and len(d.human_actions) == 1


def test_unknown_classes_never_count_as_no_human():
    d = summarize([ev("batch.ran", "robot"), ev("batch.ran", None), ev("batch.ran")], T0, T1)
    assert d.actions_by_class["unknown"] == 2
    assert d.no_human_share == pytest.approx(1 / 3)


def test_share_with_mixed_actors():
    events = [ev("batch.ran", "agent"), ev("batch.ran"), ev("batch.ran"), ev("batch.ran", "human")]
    assert summarize(events, T0, T1).no_human_share == pytest.approx(0.75)


def test_unknown_kinds_are_listed_but_not_actions():
    d = summarize([ev("mystery.kind")], T0, T1)
    assert d.total_events == 1 and d.by_kind == {"mystery.kind": 1}
    assert d.actions == 0 and d.no_human_share is None


def test_waiting_for_person_from_created_status():
    events = [
        wo("workorder.created", "wo_1", 1, "Over the spend cap", status="blocked_over_cap"),
        wo("workorder.created", "wo_2", 2, "No part", status="needs_human"),
        wo("workorder.created", "wo_3", 3, "Within caps", status="pending"),
    ]
    d = summarize(events, T0, T1)
    assert [(w.work_order, w.status) for w in d.waiting_for_person] == [
        ("wo_1", "blocked_over_cap"),
        ("wo_2", "needs_human"),
    ]
    text = render_text(d)
    assert "waiting for a person: 2" in text
    assert "wo_1  blocked_over_cap since" in text and "Over the spend cap" in text
    assert d.no_human_share == 1.0  # still 100%, but now shown next to the 2 waiting


def test_waiting_follows_latest_status():
    events = [
        wo("workorder.created", "wo_1", 1, status="pending"),
        wo(
            "workorder.status_changed",
            "wo_1",
            5,
            "Stopped: out of stock",
            **{"from": "posted", "to": "stopped"},
        ),
        wo("workorder.created", "wo_2", 1, status="blocked_over_cap"),
        ev(
            "workorder.status_changed",
            "human",
            "human:kent",
            9,
            "Kent approved",
            refs={"work_order": "wo_2", "from": "blocked_over_cap", "to": "pending"},
        ),
    ]
    d = summarize(events, T0, T1)
    assert [(w.work_order, w.status) for w in d.waiting_for_person] == [("wo_1", "stopped")]


def test_waiting_reads_status_from_data_and_ignores_missing():
    events = [
        ev("workorder.status_changed", refs={"work_order": "wo_1"}, data={"to": "NEEDS_HUMAN"}),
        ev("workorder.created", refs={"work_order": "wo_2"}),  # no status: ignored
        ev("workorder.created", refs={"status": "needs_human"}),  # no work_order: ignored
    ]
    d = summarize(events, T0, T1)
    assert [(w.work_order, w.status) for w in d.waiting_for_person] == [("wo_1", "needs_human")]


def test_waiting_includes_orders_from_before_the_window_but_not_after():
    events = [
        wo("workorder.created", "wo_old", -120, status="blocked_over_cap"),
        wo("workorder.created", "wo_late", 400, status="needs_human"),
    ]
    d = summarize(events, T0, T1)
    assert [w.work_order for w in d.waiting_for_person] == ["wo_old"]
    assert d.work_orders_created == 0  # the window counts stay inside the window


def test_window_is_since_inclusive_until_exclusive():
    events = [
        ev("incident.reported", minutes=-1),
        ev("incident.reported", minutes=0),
        ev("incident.reported", minutes=359),
        ev("incident.reported", minutes=360),
    ]
    assert summarize(events, T0, T1).incidents == 2
    assert summarize(events, T0).incidents == 3  # no upper bound


def test_human_actions_sorted_by_time():
    events = [ev("a", "human", minutes=30, why="later"), ev("b", "human", minutes=5, why="first")]
    assert [h.why for h in summarize(events, T0, T1).human_actions] == ["first", "later"]


def test_accepts_orm_like_rows_with_ts():
    @dataclass
    class Row:  # the ORM Event has `ts`, not `occurred_at`
        actor: str
        actor_class: str
        kind: str
        refs: dict
        why: str
        ts: datetime
        data: dict | None = None

    d = summarize([Row("gate", "automation", "candidate.promoted", {}, "ok", T0)], T0, T1)
    assert d.candidates_promoted == 1 and d.actions == 1


def test_accepts_a_generator():
    d = summarize((ev("incident.reported") for _ in range(3)), T0, T1)
    assert d.incidents == 3


def test_action_kinds_exclude_intake():
    intake = {
        "incident.reported",
        "recording.received",
        "label.changed",
        "device.first_seen",
        "device.health_changed",
        "install.reported",
        "overlay.held",
        "overlay.reverted",
    }
    assert not intake & ACTION_KINDS


def test_render_ends_with_newline_and_shows_window():
    text = render_text(summarize([], T0, None))
    assert text.endswith("\n") and "2026-11-08 00:30 UTC → now" in text


def test_device_agent_acting_and_budget_pruning_count_as_actions():
    events = [
        ev("incident.reported", "automation", "device:sim-1"),
        ev("incident.agent_acted", "agent", "device:sim-1"),
        ev("overlay.applied", "agent", "device:sim-1"),  # the same act; not counted twice
        ev("recording.blobs_pruned", "automation", "automation:telemetry"),
    ]
    d = summarize(events, T0, T1)
    assert d.actions == 2 and d.actions_by_class["agent"] == 1
    assert d.actions_by_class["automation"] == 1 and d.no_human_share == 1.0


def test_headline_reads_like_a_slide():
    events = [
        ev("incident.reported"),
        ev("incident.agent_acted", "agent"),
        ev("overlay.held", "automation"),
        ev("candidate.promoted"),
    ]
    text = render_text(summarize(events, T0, T1))
    headline = text.splitlines()[1]
    assert headline == (
        "In 6.0 h: 1 incidents, 1 healed on the device, 1 fleet fixes promoted, 0 rolled back, "
        "0 work orders; 0 human actions (100% of 2 actions with no human)."
    )


def test_render_in_a_time_zone_and_open_window_uses_now():
    from zoneinfo import ZoneInfo

    tz = ZoneInfo("America/Los_Angeles")
    text = render_text(summarize([], T0), tz, now=T0 + timedelta(hours=7))
    assert "2026-11-07 16:30 PST → now" in text and "In 7.0 h:" in text
    assert "Since then:" in render_text(summarize([], T0))
