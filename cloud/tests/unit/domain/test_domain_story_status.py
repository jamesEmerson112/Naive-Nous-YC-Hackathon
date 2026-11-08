"""Story.status: where an incident stands, from the latest step whose kind moves it."""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from romulus_cloud.domain.story import STATUS, STATUS_TONES, build_story

T0 = datetime(2026, 11, 7, 22, 2, 11, tzinfo=UTC)
INC = "0b6c6f3e-8d2a-4c1b-b5e3-6f7a8b9c0d1e"


@dataclass
class Ev:
    kind: str
    ts: datetime = T0
    why: str = "because"
    actor: str = "device:arm-1"
    actor_class: str = "automation"
    refs: dict[str, Any] = field(default_factory=lambda: {"incident": INC, "device": "arm-1"})
    data: dict[str, Any] | None = None


def _later(kind: str, minutes: int) -> Ev:
    return Ev(kind, ts=T0 + timedelta(minutes=minutes))


def test_status_follows_the_latest_status_kind():
    evs = [Ev("incident.reported"), Ev("incident.agent_acted"), Ev("overlay.applied")]
    assert build_story(evs, INC).status == ("overlay on trial", "wait")
    assert build_story([*evs, _later("overlay.held", 20)], INC).status == ("overlay held", "good")
    reverted = [*evs, _later("overlay.held", 20), _later("overlay.reverted", 30)]
    assert build_story(reverted, INC).status == ("overlay reverted", "bad")


def test_kinds_that_do_not_move_the_status_are_skipped():
    evs = [Ev("incident.reported"), _later("overlay.held", 20), _later("recording.received", 25)]
    assert build_story(evs, INC).status == ("overlay held", "good")


def test_status_without_known_kinds():
    assert build_story([], INC).status == ("no events", "info")
    assert build_story([Ev("custom.note")], INC).status == ("open", "bad")


def test_every_status_has_a_known_tone():
    assert {tone for _, tone in STATUS.values()} <= set(STATUS_TONES)
