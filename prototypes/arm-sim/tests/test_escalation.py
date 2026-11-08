"""The two healer messages and check_plan (armsim/escalation.py): a table of plans against one packet,
no host, no network. Run from prototypes/arm-sim:

    PYTHONPATH=. .venv/bin/python tests/test_escalation.py      (or: python -m pytest tests/)
"""
import copy
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from armsim.escalation import (ESCALATION_SCHEMA, FIXPLAN_SCHEMA, SAFETY, allowed_actions,  # noqa: E402
                               check_plan, parse_plan, validate)

NOW = time.time()
ESC = {   # a stuck sensor during approach, in a pick job
    "schema": "arm-escalation/v1", "incident_id": "inc-3f9a1c2e-1-1", "device_id": "arm-twin-1",
    "category": "fault", "opened_at": "2026-11-08T11:00:00+00:00",
    "findings": [{"src": "watchdog", "kind": "us_stuck", "severity": "critical",
                  "summary": "Distance stuck at 130 mm while the arm moved 29 mm"}],
    "worker": None, "window": "WINDOW src=arm-sim span=6.0s kinematics=on holding=no\nD01 +0.0 mm=130 us=757 st=ok pred=131.2",
    "state": {"mode": "sim", "kinematics": True, "parked": True, "pod_image": "good",
              "job": {"card_id": "crd_1", "steps": ["go_home", "reach", "approach", "grip", "lift", "release", "go_home"],
                      "failed_step": "approach"},
              "tried": []},
    "allowed_actions": ["restart_sensor", "verify", "park", "handoff_human", "no_action"],
    "deadline_s": 90}
WRONG = {**ESC, "category": "wrong_action", "allowed_actions": ["redo_step", "park", "handoff_human", "no_action"],
         "findings": [{"src": "dispatcher", "kind": "grasp_missed", "severity": "warn",
                       "summary": "grip: the gripper isn't holding the block"}],
         "state": {**ESC["state"], "job": {**ESC["state"]["job"], "failed_step": "grip"}}}
STATE = {"incident_open": True, "halted": True, "now": NOW + 5, "asked_at": NOW, "heals_this_job": 0,
         "redos_this_job": 0, "tried": []}


def plan(**over):
    p = {"schema": "arm-fixplan/v1", "incident_id": ESC["incident_id"], "action": "restart_sensor", "args": {},
         "kind": "us_frozen", "confidence": 0.86, "touches_safety": False,
         "why": "us is identical across the window while pred moved 29 mm", "expect": "fresh rows with jitter"}
    p.update(over)
    return p


J = json.dumps
# (name, plan text or dict, packet, state changes, ok?, a phrase the reason must hold)
CASES = [
    ("valid", J(plan()), ESC, {}, True, "ok"),
    ("valid in one code fence", "```json\n" + J(plan()) + "\n```", ESC, {}, True, "ok"),
    ("two JSON objects", J(plan()) + "\n" + J(plan(action="verify")), ESC, {}, False, "second JSON value"),
    ("prose before the JSON", "Here is the plan: " + J(plan()), ESC, {}, False, "not one JSON object"),
    ("broken JSON", '{"schema": "arm-fixplan/v1", "action": ', ESC, {}, False, "not one JSON object"),
    ("a JSON list", J([plan()]), ESC, {}, False, "not one object"),
    ("missing field", J({k: v for k, v in plan().items() if k != "why"}), ESC, {}, False, "missing 'why'"),
    ("action not in the enum", J(plan(action="set_joint")), ESC, {}, False, "schema"),
    ("an angle in the args", J(plan(args={"elbow_flex": 30})), ESC, {}, False, "unexpected key 'elbow_flex'"),
    ("wrong incident", J(plan(incident_id="inc-other-incident")), ESC, {}, False, "not 'inc-3f9a1c2e-1-1'"),
    ("incident closed meanwhile", J(plan()), ESC, {"incident_open": False}, False, "no longer open"),
    ("action not on the list", J(plan(action="pod_rollback")), ESC, {}, False, "not on this incident's list"),
    ("args on a no-arg action", J(plan(action="verify", args={"from_step": "grip"})), ESC, {}, False,
     "takes no arguments"),
    ("touches_safety", J(plan(touches_safety=True)), ESC, {}, False, SAFETY),
    ("a third heal", J(plan()), ESC, {"heals_this_job": 2}, False, "2 heals"),
    ("the same action after a failed check", J(plan()), ESC,
     {"tried": [{"action": "restart_sensor", "verified": False}]}, False, "not tried again"),
    ("no_action after a failed verify", J(plan(action="no_action")), ESC,
     {"tried": [{"action": "verify", "verified": False}]}, False, "not tried again"),
    ("a late plan", J(plan()), ESC, {"now": NOW + 95}, False, "past the 90 s deadline"),
    ("the arm already resumed", J(plan()), ESC, {"halted": False}, False, "no longer halted"),
    ("redo with no job", J(plan(action="redo_step", args={"from_step": "grip"})),
     {**WRONG, "state": {**WRONG["state"], "job": None}}, {}, False, "has none"),
    ("redo valid", J(plan(action="redo_step", kind="grasp_missed", args={"from_step": "approach"})), WRONG, {},
     True, "ok"),
    ("redo past the failed step", J(plan(action="redo_step", args={"from_step": "lift"})), WRONG, {}, False,
     "after the failed step"),
    ("redo a second time", J(plan(action="redo_step", args={"from_step": "grip"})), WRONG, {"redos_this_job": 1},
     False, "redo_step already ran"),
    ("a second pod_rollback", J(plan(action="pod_rollback", kind="us_mismatch")),
     {**ESC, "allowed_actions": ["pod_rollback", "verify", "park", "handoff_human", "no_action"]},
     {"tried": [{"action": "pod_rollback", "verified": True}]}, False, "pod_rollback already ran"),
]


def test_the_table():
    failed = []
    for name, text, esc, change, want_ok, phrase in CASES:
        ok, reason = check_plan(text, esc, {**STATE, **change})
        if ok != want_ok or phrase not in reason:
            failed.append(f"{name}: got ({ok}, {reason!r}), want ({want_ok}, ...{phrase!r}...)")
    assert not failed, "\n".join(failed)
    assert len(CASES) >= 15


def test_the_packet_validates_and_bad_packets_dont():
    assert validate(ESC, ESCALATION_SCHEMA) == [], validate(ESC, ESCALATION_SCHEMA)
    assert validate(WRONG, ESCALATION_SCHEMA) == []
    bad = copy.deepcopy(ESC)
    bad.update(incident_id="inc-1", deadline_s=1, opened_at="11:00", allowed_actions=["restart_sensor", "fly"])
    bad["findings"][0]["src"] = "me"
    errs = " | ".join(validate(bad, ESCALATION_SCHEMA))
    for want in ("$.incident_id", "$.deadline_s: 1 is below 3", "not an ISO date-time", "'fly' is not one of",
                 "$.findings[0].src"):
        assert want in errs, (want, errs)
    assert "unexpected key 'extra'" in " ".join(validate({**ESC, "extra": 1}, ESCALATION_SCHEMA))
    assert validate({**ESC, "findings": []}, ESCALATION_SCHEMA) == ["$.findings: fewer than 1 items"]
    assert validate(plan(confidence=True), FIXPLAN_SCHEMA), "a bool is not a number"


def test_allowed_actions_follow_the_table():
    assert allowed_actions({"kind": "us_stuck"})[0] == "restart_sensor"
    assert allowed_actions({"kind": "us_offset", "pod_incidents": ["x"]})[0] == "pod_rollback"   # image 07
    assert allowed_actions({"kind": "us_offset"}, {"pod_side": True})[0] == "pod_rollback"
    assert allowed_actions({"kind": "pod_us_mismatch"})[0] == "pod_rollback"
    assert allowed_actions({"kind": "joint_stall"}) == ["verify", "park", "handoff_human", "no_action"]
    assert allowed_actions({"kind": "grasp_missed"}, {"category": "wrong_action"})[0] == "redo_step"
    for kind in ("us_frozen", "pod_clock_skew", "joint_stall", "who_knows"):
        acts = allowed_actions({"kind": kind})
        assert "no_action" in acts and "handoff_human" in acts and "redo_step" not in acts, (kind, acts)


def test_parse_plan_tolerates_one_fence_only():
    assert parse_plan("```\n" + J(plan()) + "\n```")[0]["action"] == "restart_sensor"
    obj, why = parse_plan("```json\n" + J(plan()) + "\n```\n```json\n" + J(plan()) + "\n```")
    assert obj is None and "code fence" in why, why
    assert parse_plan("")[0] is None and parse_plan(None)[0] is None


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            t0 = time.time()
            fn()
            print(f"ok  {name}  ({time.time() - t0:.1f} s)")
