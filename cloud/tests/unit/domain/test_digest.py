"""domain/digest.py: the overnight digest / autonomy ledger."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from romulus_cloud.domain.digest import EventView, render_text, summarize

T0 = datetime(2026, 11, 8, 0, 30, tzinfo=UTC)
T1 = T0 + timedelta(hours=6)


def ev(kind, actor_class="automation", actor="gate", minutes=10, why="did a thing"):
    return EventView(actor, actor_class, kind, T0 + timedelta(minutes=minutes), why)


def test_no_events():
    d = summarize([], T0, T1)
    assert d.total == 0 and d.no_human_share is None
    assert d.by_actor_class == {"agent": 0, "automation": 0, "human": 0}
    text = render_text(d)
    assert "no events" in text and "n/a" in text and "Human actions: 0" in text


def test_overnight_counts():
    events = [
        ev("incident.reported", "agent", "device:arm-1"),
        ev("incident.reported", "agent", "device:arm-2"),
        ev("overlay.held", "agent", "device:arm-1"),
        ev("overlay.reverted", "agent", "device:arm-2"),
        ev("batch.ran"),
        ev("candidate.created", "agent", "agent:batch"),
        ev("candidate.promoted"),
        ev("candidate.rolled_back"),
        ev("workorder.created"),
        ev("workorder.card_posted"),
        ev("workorder.status_changed", "agent", "naive:ses_1"),
    ]
    d = summarize(events, T0, T1)
    assert d.total == 11
    assert d.by_actor_class == {"agent": 6, "automation": 5, "human": 0}
    assert (d.incidents, d.heals, d.overlays_reverted) == (2, 1, 1)
    assert (d.candidates_created, d.candidates_promoted, d.candidates_rolled_back) == (1, 1, 1)
    assert (d.work_orders_created, d.work_orders_posted, d.freezes) == (1, 1, 0)
    assert d.no_human_share == 1.0
    assert d.human_actions == ()
    assert d.by_kind["incident.reported"] == 2
    text = render_text(d)
    assert "100.0% of actions" in text
    assert "Incidents: 2" in text and "1 promoted" in text and "1 rolled back" in text


def test_only_humans():
    events = [
        ev("fleet.frozen", "human", "human:kent", 5, "Kent froze the fleet"),
        ev("fleet.unfrozen", "human", "human:kent", 7, "Kent unfroze it"),
    ]
    d = summarize(events, T0, T1)
    assert d.no_human_share == 0.0 and d.freezes == 1
    assert [h.why for h in d.human_actions] == ["Kent froze the fleet", "Kent unfroze it"]
    text = render_text(d)
    assert "0.0% of actions" in text and "Human actions: 2" in text
    assert "human:kent  fleet.frozen: Kent froze the fleet" in text


def test_share_with_mixed_actors():
    events = [ev("x", "agent"), ev("x", "automation"), ev("x", "automation"), ev("x", "human")]
    assert summarize(events, T0, T1).no_human_share == pytest.approx(0.75)


def test_window_is_since_inclusive_until_exclusive():
    events = [
        ev("incident.reported", minutes=-1),
        ev("incident.reported", minutes=0),
        ev("incident.reported", minutes=359),
        ev("incident.reported", minutes=360),
    ]
    assert summarize(events, T0, T1).incidents == 2
    assert summarize(events, T0).incidents == 3  # no upper bound


def test_unknown_kinds_and_classes_still_counted():
    d = summarize([ev("mystery.kind", "robot")], T0, T1)
    assert d.total == 1 and d.by_kind == {"mystery.kind": 1}
    assert d.by_actor_class["robot"] == 1 and d.incidents == 0
    assert d.no_human_share == 1.0


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

    d = summarize([Row("gate", "automation", "candidate.promoted", {}, "ok", T0)], T0, T1)
    assert d.candidates_promoted == 1


def test_accepts_a_generator():
    d = summarize((ev("incident.reported") for _ in range(3)), T0, T1)
    assert d.incidents == 3


def test_render_ends_with_newline_and_shows_window():
    text = render_text(summarize([], T0, None))
    assert text.endswith("\n") and "2026-11-08 00:30 UTC → now" in text
