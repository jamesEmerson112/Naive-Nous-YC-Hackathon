"""domain/merge.py: deterministic overlay merge (D3) and patch classification (BUILD §3)."""

import copy
import json
from pathlib import Path

import pytest

from romulus_cloud.domain.merge import (
    MergeError,
    MergeResult,
    apply_overlay,
    base_compatible,
    classify_patch,
    config_touches_safety,
)
from romulus_contracts import FirmwareManifest, IncidentReport, Overlay, evaluate

EXAMPLES = Path(__file__).parents[4] / "packages" / "contracts" / "examples"

BASE = {
    "version": 7,
    "device_class": "so101",
    "code_rev": "a1b2c3d",
    "rules": {
        "camera_occluded": {"signal": "brightness", "below": 0.2, "action": "safe_pose"},
        "gripper_stall": {"signal": "gripper_current_ma", "above": 900, "action": "backoff_retry"},
    },
    "params": {"max_speed": 0.6, "pid": {"kp": 1.0, "ki": 0.1}, "joints": [1, 2, 3]},
    "policy": {"name": "pick_cube", "rev": "hf:org/act@3f9e"},
}


def base():
    return copy.deepcopy(BASE)


def merge(*args, **kwargs):
    return apply_overlay(*args, **kwargs).manifest


# --- rules ------------------------------------------------------------------------------


def test_apis_example_raises_one_threshold_and_keeps_the_rest():
    merged = merge(base(), {"rules": {"camera_occluded": {"below": 0.35}}})
    assert merged["rules"]["camera_occluded"] == {
        "signal": "brightness",
        "below": 0.35,
        "action": "safe_pose",
    }
    assert merged["rules"]["gripper_stall"] == BASE["rules"]["gripper_stall"]
    assert merged["params"] == BASE["params"]
    assert merged["version"] == 7 and merged["code_rev"] == "a1b2c3d"


def test_merged_rules_fire_the_resolving_action_on_the_incidents_signals():
    merged = merge(base(), {"rules": {"camera_occluded": {"below": 0.35}}})
    fired = evaluate(merged["rules"], {"brightness": [0.41, 0.33]})
    assert [f.action for f in fired] == ["safe_pose"]
    assert evaluate(BASE["rules"], {"brightness": [0.41, 0.33]}) == []


def test_null_field_removes_it_so_a_threshold_can_flip_direction():
    merged = merge(base(), {"rules": {"camera_occluded": {"below": None, "above": 0.9}}})
    assert merged["rules"]["camera_occluded"] == {
        "signal": "brightness",
        "above": 0.9,
        "action": "safe_pose",
    }


def test_add_a_complete_new_rule():
    rule = {"signal": "servo_temp_c", "above": 70, "action": "cool_down"}
    merged = merge(base(), {"rules": {"servo_hot": rule}})
    assert merged["rules"]["servo_hot"] == rule
    assert set(merged["rules"]) == {"camera_occluded", "gripper_stall", "servo_hot"}


def test_remove_a_rule_with_null():
    merged = merge(base(), {"rules": {"gripper_stall": None}})
    assert set(merged["rules"]) == {"camera_occluded"}


def test_extra_rule_keys_are_kept():
    merged = merge(base(), {"rules": {"camera_occluded": {"note": "judge test"}}})
    assert merged["rules"]["camera_occluded"]["note"] == "judge test"


# --- params -----------------------------------------------------------------------------


def test_params_deep_merge_replace_and_remove():
    merged = merge(
        base(),
        {"params": {"max_speed": 0.4, "pid": {"kp": 0.8, "ki": None}, "joints": [9], "new": "x"}},
    )
    assert merged["params"] == {"max_speed": 0.4, "pid": {"kp": 0.8}, "joints": [9], "new": "x"}


def test_params_scalar_replaces_a_mapping_and_mapping_replaces_a_scalar():
    merged = merge(base(), {"params": {"pid": 3, "max_speed": {"x": 1}}})
    assert merged["params"]["pid"] == 3
    assert merged["params"]["max_speed"] == {"x": 1}


def test_params_into_a_manifest_without_params():
    m = base()
    del m["params"]
    assert merge(m, {"params": {"a": {"b": 1}}})["params"] == {"a": {"b": 1}}


# --- no-ops, version, inputs ------------------------------------------------------------


@pytest.mark.parametrize("diff", [None, {}, {"rules": {}}, {"params": {}}])
def test_empty_diffs_change_nothing(diff):
    assert merge(base(), diff) == FirmwareManifest.model_validate(BASE).model_dump(mode="json")


def test_version_sets_the_candidate_number():
    assert merge(base(), {}, version=8)["version"] == 8


def test_inputs_are_not_mutated():
    m, diff = base(), {"rules": {"camera_occluded": {"below": 0.35}}, "params": {"pid": {"kp": 2}}}
    m_before, diff_before = copy.deepcopy(m), copy.deepcopy(diff)
    merged = merge(m, diff)
    merged["params"]["joints"].append(4)
    merged["rules"]["camera_occluded"]["below"] = 0
    assert m == m_before and diff == diff_before


def test_accepts_a_firmware_manifest_model():
    merged = merge(FirmwareManifest.model_validate(BASE), {"params": {"max_speed": 0.5}})
    assert merged["params"]["max_speed"] == 0.5


def test_result_is_the_dumped_manifest_form():
    m = base()
    m["version"] = "7"  # lax input is normalized, as firmware_response does
    merged = merge(m, {})
    assert merged["version"] == 7
    assert json.loads(json.dumps(merged)) == merged


def test_deterministic():
    diff = {"rules": {"camera_occluded": {"below": 0.35}}, "params": {"pid": {"kp": 2}}}
    assert merge(base(), diff) == merge(base(), diff)


# --- conflicts --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("diff", "needle"),
    [
        ({"version": 9}, "'version' can't change"),
        ({"code_rev": "x"}, "'code_rev' can't change"),
        ({"policy": {"name": "p", "rev": "r"}}, "'policy' can't change"),
        ({"device_class": "ur5"}, "'device_class' can't change"),
        ({"harness": {"skill": "x"}}, "'harness' can't change"),
        ({"rules": {"nope": None}}, "rule 'nope' is removed, but the base"),
        ({"params": {"nope": None}}, "params.nope is removed"),
        ({"params": {"pid": {"kd": None}}}, "params.pid.kd is removed"),
        ({"rules": {"camera_occluded": {"missing_field": None}}}, "rules.camera_occluded."),
        ({"rules": ["camera_occluded"]}, "diff 'rules' must be a mapping"),
        ({"rules": None}, "diff 'rules' must be a mapping"),
        ({"params": 3}, "diff 'params' must be a mapping"),
        ({"rules": {"camera_occluded": 0.35}}, "must be a mapping or null"),
    ],
)
def test_conflicts_raise_merge_error(diff, needle):
    with pytest.raises(MergeError) as err:
        apply_overlay(base(), diff)
    assert any(needle in p for p in err.value.problems), err.value.problems


def test_diff_that_is_not_a_mapping():
    with pytest.raises(MergeError, match="diff must be a mapping"):
        apply_overlay(base(), ["rules"])


@pytest.mark.parametrize(
    "diff",
    [
        {"rules": {"servo_hot": {"above": 70}}},  # new rule, incomplete
        {"rules": {"camera_occluded": {"below": None}}},  # no threshold left
        {"rules": {"camera_occluded": {"above": 0.1}}},  # below 0.2 >= above 0.1
        {"rules": {"camera_occluded": {"below": "low"}}},  # not a number
        {"rules": {"camera_occluded": {"action": ""}}},
    ],
)
def test_invalid_merged_rules_raise_merge_error(diff):
    with pytest.raises(MergeError, match="merged manifest is invalid"):
        apply_overlay(base(), diff)


def test_every_problem_is_listed():
    with pytest.raises(MergeError) as err:
        apply_overlay(base(), {"version": 9, "rules": {"nope": None}, "params": {"x": None}})
    assert len(err.value.problems) == 3


def test_merge_error_is_a_value_error():
    assert issubclass(MergeError, ValueError)


# --- classify_patch ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("overlay", "kinds"),
    [
        ({"diff": {"rules": {"x": {"below": 1}}}, "code_patch": None}, {"rules"}),
        ({"diff": {"params": {"a": 1}}}, {"params"}),
        ({"diff": {"rules": {"x": {}}, "params": {"a": 1}}}, {"rules", "params"}),
        ({"diff": {"harness": {"skills": ["x.md"]}}}, {"harness"}),
        ({"diff": {}, "code_patch": "--- a/x.py\n+++ b/x.py\n"}, {"code"}),
        ({"diff": {"rules": {"x": {}}}, "code_patch": "--- a/x\n+++ b/x\n"}, {"rules", "code"}),
        ({"diff": {"policy": {"rev": "r"}}}, {"policy"}),
        ({"diff": {}, "code_patch": "   \n"}, set()),
        ({"diff": {"rules": {}, "params": None}, "code_patch": ""}, set()),
        ({"diff": None, "code_patch": None}, set()),
        ({}, set()),
    ],
)
def test_classify_patch(overlay, kinds):
    assert classify_patch(overlay) == frozenset(kinds)


def test_classify_contract_overlays_from_the_examples():
    rules = IncidentReport.model_validate_json((EXAMPLES / "incident_rules_patch.json").read_text())
    code = IncidentReport.model_validate_json(
        (EXAMPLES / "incident_d1_code_patch.json").read_text()
    )
    assert classify_patch(rules.overlay) == {"rules"}
    assert classify_patch(code.overlay) == {"code"}
    assert classify_patch(Overlay(overlay_id="ov-1")) == frozenset()


def test_example_rules_overlay_merges_into_a_v1_manifest():
    report = IncidentReport.model_validate_json(
        (EXAMPLES / "incident_rules_patch.json").read_text()
    )
    merged = merge(base(), report.overlay.diff, version=8)
    fired = evaluate(merged["rules"], report.signals)
    assert report.outcome.resolving_action in [f.action for f in fired]


# --- what changed (review finding 5) ----------------------------------------------------

SAFE = {
    **BASE,
    "rules": {
        **BASE["rules"],
        "estop": {"signal": "dist_mm", "below": 30, "action": "estop"},
    },
}


def test_removing_an_unrelated_safety_rule_is_reported_and_named_in_the_why():
    """Reviewer's case: a camera fix that also deletes `estop` must not merge quietly."""
    r = apply_overlay(SAFE, {"rules": {"camera_occluded": {"below": 0.35}, "estop": None}})
    assert r.removed_rules == ("estop",)
    assert r.changed_rules == ("camera_occluded",)
    assert "removes rule estop" in r.why
    assert config_touches_safety(r, safety_rules={"estop"})
    assert not config_touches_safety(r, safety_rules={"gripper_stall"})


def test_merge_result_lists_added_changed_removed_and_params():
    r = apply_overlay(
        base(),
        {
            "rules": {
                "servo_hot": {"signal": "t", "above": 70, "action": "cool"},
                "camera_occluded": {"below": 0.3},
                "gripper_stall": None,
            },
            "params": {"max_speed": 0.4, "pid": {"kp": 1.0, "ki": None}, "joints": [1, 2, 3]},
        },
    )
    assert isinstance(r, MergeResult)
    assert r.added_rules == ("servo_hot",)
    assert r.changed_rules == ("camera_occluded",)
    assert r.removed_rules == ("gripper_stall",)
    # kp and joints were written with their old values: not changes
    assert r.changed_params == ("params.max_speed", "params.pid.ki")
    assert r.why == (
        "adds rule servo_hot; changes rule camera_occluded; removes rule gripper_stall; "
        "changes params.max_speed; changes params.pid.ki"
    )


def test_no_change_why():
    r = apply_overlay(base(), {"rules": {"camera_occluded": {"below": 0.2}}})
    assert r.changed_rules == () and r.why == "no change"


@pytest.mark.parametrize(
    ("changed", "safety", "expected"),
    [
        ({"params": {"limits": {"torque": 5}}}, {"limits.torque"}, True),
        ({"params": {"limits": {"torque": 5}}}, {"limits"}, True),  # below a safety path
        ({"params": {"limits": 0}}, {"limits.torque"}, True),  # above a safety path
        ({"params": {"limits": {"speed": 1}}}, {"limits.torque"}, False),
        ({"params": {"limitsx": 1}}, {"limits"}, False),
        ({"params": {"max_speed": 0.1}}, set(), False),
    ],
)
def test_config_touches_safety_params(changed, safety, expected):
    m = {**base(), "params": {"max_speed": 0.6, "limits": {"torque": 9, "speed": 2}}}
    assert config_touches_safety(apply_overlay(m, changed), safety_params=safety) is expected


def test_config_touches_safety_on_added_rule():
    r = apply_overlay(base(), {"rules": {"estop": {"signal": "d", "below": 1, "action": "e"}}})
    assert config_touches_safety(r, safety_rules=["estop"])


# --- non-finite numbers (review finding 6) ----------------------------------------------


@pytest.mark.parametrize(
    ("diff", "needle"),
    [
        ({"params": {"speed": float("nan")}}, "params.speed must be a finite number"),
        ({"params": {"speed": float("inf")}}, "params.speed"),
        ({"params": {"pid": {"kp": float("-inf")}}}, "params.pid.kp"),
        ({"params": {"joints": [1, float("nan")]}}, "params.joints.1"),
        ({"rules": {"camera_occluded": {"note": float("nan")}}}, "rules.camera_occluded.note"),
    ],
)
def test_non_finite_numbers_are_rejected(diff, needle):
    with pytest.raises(MergeError) as err:
        apply_overlay(base(), diff)
    assert any(needle in p for p in err.value.problems), err.value.problems


def test_nan_from_json_loads_is_rejected():
    """Reviewer's case: json.loads accepts NaN, and pydantic's dump would turn it into null."""
    with pytest.raises(MergeError, match="finite"):
        apply_overlay(base(), json.loads('{"params": {"speed": NaN}}'))


# --- base_compatible (open question c) --------------------------------------------------

V8 = {
    **BASE,
    "version": 8,
    "rules": {
        **BASE["rules"],
        "camera_occluded": {**BASE["rules"]["camera_occluded"], "below": 0.3},
    },
    "params": {**BASE["params"], "max_speed": 0.5},
}


def test_compatible_when_the_touched_keys_agree():
    c = base_compatible(BASE, V8, {"rules": {"gripper_stall": {"above": 950}}})
    assert c and c.ok and c.conflicts == ()
    assert "agree" in c.why


def test_incompatible_when_the_assigned_version_changed_a_touched_key():
    """A v7 overlay must not revert v8's camera fix (a lost update)."""
    c = base_compatible(BASE, V8, {"rules": {"camera_occluded": {"below": 0.25}}})
    assert not c and c.conflicts == ("rules.camera_occluded",)
    assert c.why == "assigned v8 changed rules.camera_occluded since the overlay's base v7"


def test_reviewer_case_rule_compared_whole():
    """Re-review nit 2: v8 changed x's signal; a v7 patch to x.below must not land on it."""
    v8 = {
        **BASE,
        "version": 8,
        "rules": {
            **BASE["rules"],
            "camera_occluded": {**BASE["rules"]["camera_occluded"], "signal": "luma"},
        },
    }
    c = base_compatible(BASE, v8, {"rules": {"camera_occluded": {"below": 0.35}}})
    assert not c and c.conflicts == ("rules.camera_occluded",)
    # an untouched rule may differ freely
    assert base_compatible(BASE, v8, {"rules": {"gripper_stall": {"above": 950}}})


@pytest.mark.parametrize(
    ("diff", "conflicts"),
    [
        ({"params": {"max_speed": 0.4}}, ("params.max_speed",)),
        ({"params": {"pid": {"kp": 2}}}, ()),  # params stay field by field
        ({"rules": {"camera_occluded": None}}, ("rules.camera_occluded",)),
        ({"rules": {"camera_occluded": {"action": "x"}}}, ("rules.camera_occluded",)),  # whole
        (
            {"rules": {"camera_occluded": {"action": "x", "below": 0.3}}},
            ("rules.camera_occluded",),  # reported once
        ),
        ({"rules": {"new": {"signal": "s", "above": 1, "action": "a"}}}, ()),  # absent in both
        ({}, ()),
        (None, ()),
    ],
)
def test_base_compatible_table(diff, conflicts):
    c = base_compatible(BASE, V8, diff)
    assert c.conflicts == conflicts and c.ok is (not conflicts)


def test_rule_added_in_assigned_conflicts_with_the_overlay_adding_it_too():
    v8 = {**V8, "rules": {**V8["rules"], "new": {"signal": "s", "above": 1, "action": "a"}}}
    c = base_compatible(BASE, v8, {"rules": {"new": {"signal": "s", "above": 2, "action": "a"}}})
    assert not c and c.conflicts == ("rules.new",)


def test_unknown_base_is_not_compatible():
    c = base_compatible(None, V8, {"params": {"max_speed": 0.4}})
    assert not c and "unknown" in c.why


def test_base_compatible_accepts_models():
    c = base_compatible(
        FirmwareManifest.model_validate(BASE), FirmwareManifest.model_validate(V8), {}
    )
    assert c.ok
