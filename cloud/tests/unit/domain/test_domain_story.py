"""domain/story.py: pure, ordered by when things happened, generic over event kinds."""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from romulus_cloud.domain import story
from romulus_cloud.domain.story import build_story

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


def _overlay_refs(**extra):
    return {"incident": INC, "device": "arm-1", "overlay": "ov-12", **extra}


def test_orders_by_occurred_time_not_input_order():
    late = Ev("overlay.held", ts=T0 + timedelta(minutes=20), refs=_overlay_refs())
    early = Ev("incident.reported", data={"kind": "camera_occluded", "summary": "dark"})
    s = build_story([late, early], INC)
    assert [st.kind for st in s.steps] == ["incident.reported", "overlay.held"]


def test_same_timestamp_is_ordered_by_stage_then_input_order():
    evs = [
        Ev("overlay.applied", refs=_overlay_refs(), data={"rules": ["camera_occluded"]}),
        Ev("custom.b"),
        Ev("incident.agent_acted", actor_class="agent", data={"actions": ["safe_pose"]}),
        Ev("custom.a"),
        Ev("incident.reported", data={"kind": "camera_occluded"}),
    ]
    kinds = [st.kind for st in build_story(evs, INC).steps]
    assert kinds == [
        "incident.reported",
        "incident.agent_acted",
        "overlay.applied",
        "custom.b",
        "custom.a",
    ]


def test_lines_labels_and_chain_for_the_f2_path():
    evs = [
        Ev("incident.reported", data={"kind": "camera_occluded", "summary": "Frames went dark"}),
        Ev(
            "incident.agent_acted",
            actor_class="agent",
            why="Occlusion, not hardware.",
            data={
                "actions": ["safe_pose", {"tool": "patch"}],
                "resolved": True,
                "resolving_action": "safe_pose",
                "suspected_cause": "software",
            },
        ),
        Ev("overlay.applied", refs=_overlay_refs(), data={"rules": ["camera_occluded"]}),
        Ev("overlay.held", ts=T0 + timedelta(minutes=20), refs=_overlay_refs()),
    ]
    s = build_story(evs, INC)
    assert s.chain == "camera_occluded → agent acted → overlay ov-12 → held"
    lines = [st.line for st in s.steps]
    assert lines[0] == "arm-1 reported camera_occluded: Frames went dark"
    assert lines[1] == (
        "The agent on arm-1 did: safe_pose, patch. It resolved it with safe_pose; "
        "suspected cause: software."
    )
    assert lines[2] == "The agent patched arm-1 locally as overlay ov-12: rules camera_occluded."
    assert lines[3] == "Overlay ov-12 held on arm-1."
    assert s.steps[1].why == "Occlusion, not hardware."
    assert s.steps[1].actor_class == "agent"


def test_unknown_kinds_get_a_default_line_and_label():
    """Later milestones extend the story just by recording events."""
    ev = Ev("workorder.something_new", actor="worker", refs={"incident": INC, "work_order": "w1"})
    (step,) = build_story([ev], INC).steps
    assert step.line == "worker: workorder something new."
    assert step.label == "workorder something new"  # words, never the dotted raw kind
    assert step.refs == {"incident": INC, "work_order": "w1"}


def test_extending_lines_and_labels_changes_the_story(monkeypatch):
    monkeypatch.setitem(story.LINES, "candidate.promoted", lambda e: "Promoted to the fleet.")
    monkeypatch.setitem(story.LABELS, "candidate.promoted", lambda e: "fleet")
    s = build_story([Ev("candidate.promoted", actor="gate")], INC)
    assert s.steps[0].line == "Promoted to the fleet." and s.chain == "fleet"


def test_safety_and_code_paths_show_in_the_overlay_line():
    ev = Ev(
        "overlay.applied",
        refs=_overlay_refs(touches_safety=True),
        data={"code_paths": ["visor/watchdog.py"], "params": ["max_speed"]},
    )
    line = build_story([ev], INC).steps[0].line
    assert "params max_speed; code visor/watchdog.py" in line
    assert line.endswith("It touches the safety layer or visor.")


def test_empty_story_and_to_dict():
    assert build_story([], INC).steps == () and build_story([], INC).chain == ""
    d = build_story([Ev("overlay.reverted", refs=_overlay_refs())], INC).to_dict()
    assert d["incident_id"] == INC and d["chain"] == "reverted"
    (step,) = d["steps"]
    assert step["at"] == "2026-11-07T22:02:11+00:00"
    assert step["line"] == "Overlay ov-12 was reverted on arm-1."
    assert set(step) == {"at", "actor", "actor_class", "kind", "line", "why", "label", "refs"}


def test_missing_data_does_not_crash():
    for kind in ["incident.reported", "incident.agent_acted", "overlay.applied"]:
        (step,) = build_story([Ev(kind, data=None)], INC).steps
        assert step.line


def _wo_refs(status, **extra):
    return {"incident": INC, "device": "arm-1", "work_order": "wo_1", "status": status, **extra}


def test_work_order_events_read_as_a_chain_and_set_the_status():
    note = "RESULT: ordered ORDER_REF=PO-1 COST_USD=12.00"
    moved = {"from": "posted", "to": "in_progress"}
    evs = [
        # Same tick, given out of order: the loop stage puts them right.
        Ev("workorder.status_changed", refs=_wo_refs("in_progress", **moved)),
        Ev(
            "workorder.card_posted",
            refs=_wo_refs("posted"),
            data={"card_id": "crd_1", "assignee": "fleet-ops"},
        ),
        Ev(
            "workorder.created",
            refs=_wo_refs("pending"),
            data={"part_name": "servo", "quantity": 1},
        ),
        Ev(
            "workorder.status_changed",
            ts=T0 + timedelta(seconds=30),
            refs=_wo_refs("done", **{"from": "in_progress", "to": "done"}),
            data={"note": note},
        ),
    ]
    s = build_story(evs, INC)
    assert s.chain == f"work order wo_1 → card posted → in progress → done: {note}"
    assert s.status == ("part ordered", "good")
    assert "posted to Naïve as card crd_1, assigned to fleet-ops" in s.steps[1].line
    assert s.steps[0].line.endswith("(1 x servo); status pending.")


def test_a_blocked_work_order_shows_as_waiting_for_a_person():
    s = build_story([Ev("workorder.created", refs=_wo_refs("blocked_over_cap"))], INC)
    assert s.chain == "work order wo_1 (blocked over cap)"
    assert s.status == ("blocked: over cap", "bad")
    assert "no catalogued part" in s.steps[0].line
