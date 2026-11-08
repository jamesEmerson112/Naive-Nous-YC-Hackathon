"""domain/story.py with M3's batch and gate kinds: labels, order, the chain and the status."""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from romulus_cloud.domain.story import build_story

T0 = datetime(2026, 11, 7, 22, 0, tzinfo=UTC)
INC = "inc-1"


@dataclass
class Ev:
    kind: str
    ts: datetime = T0
    actor: str = "gate"
    actor_class: str = "automation"
    refs: dict[str, Any] = field(default_factory=lambda: {"incident": INC})
    why: str = "because"
    data: dict[str, Any] | None = None


def _cand(kind, type_="firmware", version=2, at=T0, **data):
    return Ev(kind, ts=at, data={"type": type_, "version": version, **data})


def test_one_batch_and_gate_run_reads_as_a_short_chain():
    t1, t2, t3 = (T0 + timedelta(seconds=s) for s in (10, 20, 30))
    events = [
        Ev("overlay.held", ts=T0, refs={"incident": INC, "overlay": "ov-1"}),
        # Same ts, written in this order by one batch run and one gate tick.
        _cand("candidate.created", at=t1),
        _cand("candidate.created", "harness", at=t1),
        Ev("overlay.locked_in", ts=t1, refs={"incident": INC, "overlay": "ov-1"}),
        _cand("candidate.replayed", at=t2, replay={"passed": True, "stub": False}),
        Ev(
            "candidate.canary_started",
            ts=t2,
            refs={"incident": INC, "device": "sim-2"},
            data={"type": "firmware", "version": 2},
        ),
        _cand("candidate.replayed", "harness", at=t2, replay={"passed": True, "stub": True}),
        Ev(
            "candidate.canary_started",
            ts=t2,
            refs={"incident": INC, "device": "sim-2"},
            data={"type": "harness", "version": 2},
        ),
        _cand("candidate.promoted", at=t3),
        _cand("candidate.promoted", "harness", at=t3),
        Ev(
            "overlay.superseded",
            ts=t3 + timedelta(seconds=5),
            refs={"incident": INC, "overlay": "ov-1"},
            data={"before": "locked_in"},
        ),
    ]
    story = build_story(events, INC)
    assert story.chain == (
        "held → locked in → firmware v2 → harness v2 → replay ✓ → canary sim-2 → fleet"
    )
    assert story.status == ("in the fleet", "good")  # the acknowledgment doesn't move it
    assert len(story.steps) == len(events)  # hidden from the chain, still in the steps


def test_superseded_without_a_cloud_decision_is_still_news():
    ev = Ev(
        "overlay.superseded", refs={"incident": INC, "overlay": "ov-1"}, data={"before": "held"}
    )
    story = build_story([ev], INC)
    assert story.chain == "superseded" and story.status == ("superseded", "info")


def test_rolled_back_and_code_labels():
    events = [
        _cand("candidate.created", "code", version=3, code_rev="abc1234"),
        _cand("candidate.replayed", "code", version=3, replay={"passed": False, "stub": False}),
        _cand("candidate.rolled_back", "code", version=3, at=T0 + timedelta(seconds=1)),
    ]
    story = build_story(events, INC)
    assert story.chain == "code abc1234 (firmware v3) → replay ✗ → rolled back"
    assert story.status == ("rolled back", "bad")
    assert (
        story.steps[0].line
        == "The batch locked the fix in as candidate code abc1234 (firmware v3)."
    )
