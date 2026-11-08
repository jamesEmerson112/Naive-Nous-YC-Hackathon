"""Pins the behavior of rules.evaluate (APIs §2.7). The device and the gate both rely on it."""

import ast
import copy
import math
from pathlib import Path

import pytest
from romulus_contracts import rules as rules_module
from romulus_contracts.rules import (
    Fired,
    RulesError,
    Skipped,
    evaluate,
    fired_actions,
    replay_passes,
    replay_report,
    skipped_samples,
    validate_rules,
)

CAMERA = {"signal": "brightness", "below": 0.35, "action": "safe_pose"}
STALL = {"signal": "gripper_current_ma", "above": 900, "action": "backoff_retry"}
APIS_RULES = {"camera_occluded": CAMERA, "gripper_stall": STALL}


# --- the APIs §2.1 example -------------------------------------------------------------


def test_apis_example_below_fires_with_first_tripping_sample():
    fired = evaluate(APIS_RULES, {"brightness": [0.41, 0.18, 0.12, 0.33]})
    assert fired == [
        Fired(
            rule="camera_occluded",
            signal="brightness",
            action="safe_pose",
            direction="below",
            threshold=0.35,
            value=0.18,
            index=1,
        )
    ]


def test_apis_example_above_fires():
    fired = evaluate(APIS_RULES, {"gripper_current_ma": [400, 950, 1200]})
    assert [(f.rule, f.direction, f.value, f.index) for f in fired] == [
        ("gripper_stall", "above", 950, 1)
    ]


def test_both_rules_fire_sorted_by_rule_name():
    signals = {"gripper_current_ma": [1000], "brightness": [0.1]}
    assert [f.rule for f in evaluate(APIS_RULES, signals)] == ["camera_occluded", "gripper_stall"]


# --- comparison operators --------------------------------------------------------------


@pytest.mark.parametrize(
    ("sample", "fires"),
    [(0.34, True), (0.35, False), (0.36, False), (-1, True), (0, True)],
)
def test_below_is_strict(sample, fires):
    assert bool(evaluate({"r": CAMERA}, {"brightness": [sample]})) is fires


@pytest.mark.parametrize(
    ("sample", "fires"),
    [(901, True), (900, False), (900.0, False), (899, False), (900.0001, True)],
)
def test_above_is_strict(sample, fires):
    assert bool(evaluate({"r": STALL}, {"gripper_current_ma": [sample]})) is fires


def test_int_and_float_compare_numerically():
    rule = {"signal": "s", "above": 1, "action": "a"}
    assert evaluate({"r": rule}, {"s": [1.5]})[0].value == 1.5
    assert evaluate({"r": rule}, {"s": [1.0]}) == []


@pytest.mark.parametrize(
    ("samples", "expected"),
    [
        ([15], []),
        ([10], []),
        ([20], []),
        ([5], [("below", 5, 0)]),
        ([9.99], [("below", 9.99, 0)]),
        ([20.01], [("above", 20.01, 0)]),
        ([15, 25, 1], [("above", 25, 1)]),
        ([15, 1, 25], [("below", 1, 1)]),
    ],
)
def test_band_rule_fires_outside_band_reporting_first_trip(samples, expected):
    rule = {"signal": "temp", "below": 10, "above": 20, "action": "cool"}
    fired = evaluate({"r": rule}, {"temp": samples})
    assert [(f.direction, f.value, f.index) for f in fired] == expected


def test_band_rule_reports_matching_threshold():
    rule = {"signal": "temp", "below": 10, "above": 20, "action": "cool"}
    assert evaluate({"r": rule}, {"temp": [30]})[0].threshold == 20
    assert evaluate({"r": rule}, {"temp": [3]})[0].threshold == 10


def test_null_threshold_counts_as_unset():
    rule = {"signal": "s", "below": None, "above": 5, "action": "a"}
    assert [f.direction for f in evaluate({"r": rule}, {"s": [-100, 6]})] == ["above"]


def test_negative_thresholds():
    rule = {"signal": "s", "below": -5, "action": "a"}
    assert evaluate({"r": rule}, {"s": [-4, -5, -6]})[0].value == -6


# --- ordering ---------------------------------------------------------------------------


def test_order_is_sorted_rule_name_not_insertion_order():
    rules = {
        name: {"signal": "s", "above": 0, "action": f"act_{name}"}
        for name in ["zeta", "Alpha", "beta", "alpha", "_x", "10", "9"]
    }
    fired = evaluate(rules, {"s": [1]})
    assert [f.rule for f in fired] == sorted(rules)
    assert [f.rule for f in fired] == ["10", "9", "Alpha", "_x", "alpha", "beta", "zeta"]


def test_one_fired_per_rule_even_if_many_samples_trip():
    fired = evaluate({"r": STALL}, {"gripper_current_ma": [1000, 1100, 1200]})
    assert len(fired) == 1 and fired[0].index == 0


def test_several_rules_on_same_signal():
    rules = {
        "warn": {"signal": "t", "above": 50, "action": "slow_down"},
        "crit": {"signal": "t", "above": 70, "action": "safe_pose"},
    }
    assert [(f.rule, f.action) for f in evaluate(rules, {"t": [60, 75]})] == [
        ("crit", "safe_pose"),
        ("warn", "slow_down"),
    ]
    assert [f.rule for f in evaluate(rules, {"t": [60]})] == ["warn"]


def test_result_is_deterministic_across_calls():
    signals = {"brightness": [0.1], "gripper_current_ma": [1000]}
    assert evaluate(APIS_RULES, signals) == evaluate(dict(reversed(APIS_RULES.items())), signals)


# --- missing and odd signals -----------------------------------------------------------


def test_no_match_returns_empty_list():
    assert evaluate(APIS_RULES, {"brightness": [0.9], "gripper_current_ma": [100]}) == []


def test_empty_rules_and_empty_signals():
    assert evaluate({}, {}) == []
    assert evaluate({}, {"brightness": [0.0]}) == []
    assert evaluate(APIS_RULES, {}) == []


def test_missing_signal_does_not_fire():
    assert evaluate(APIS_RULES, {"other_signal": [0.0, 99999]}) == []


def test_empty_sample_list_does_not_fire():
    assert evaluate(APIS_RULES, {"brightness": []}) == []


def test_scalar_signal_is_one_sample():
    fired = evaluate({"r": CAMERA}, {"brightness": 0.1})
    assert fired[0].value == 0.1 and fired[0].index == 0


def test_tuple_samples_work():
    assert evaluate({"r": CAMERA}, {"brightness": (0.5, 0.1)})[0].index == 1


@pytest.mark.parametrize(
    "junk",
    [None, "0.1", True, False, float("nan"), [0.1], {"v": 0.1}, b"\x00"],
)
def test_unusable_samples_are_skipped_not_errors(junk):
    # Only the junk sample is "below"; nothing fires and nothing raises.
    assert evaluate({"r": CAMERA}, {"brightness": [0.9, junk, 0.8]}) == []


@pytest.mark.parametrize("junk", [None, "0.1", True, float("nan"), {"v": 0.1}, b"\x00"])
def test_unusable_scalar_signal_does_not_fire(junk):
    assert evaluate({"r": CAMERA}, {"brightness": junk}) == []


def test_skipped_samples_keep_original_indexes():
    fired = evaluate({"r": CAMERA}, {"brightness": [None, "x", float("nan"), 0.1]})
    assert fired[0].index == 3


def test_infinity_samples_compare():
    assert evaluate({"r": STALL}, {"gripper_current_ma": [math.inf]})[0].value == math.inf
    assert evaluate({"r": CAMERA}, {"brightness": [-math.inf]})[0].direction == "below"
    assert evaluate({"r": CAMERA}, {"brightness": [math.inf]}) == []


def test_bool_samples_never_count_even_against_numeric_thresholds():
    rule = {"signal": "flag", "above": 0, "action": "a"}
    assert evaluate({"r": rule}, {"flag": [True, True]}) == []
    assert evaluate({"r": rule}, {"flag": [1]})[0].value == 1


# --- malformed rules -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("rules", "fragment"),
    [
        ([CAMERA], "must be a mapping"),
        (None, "must be a mapping"),
        ({"r": "not a rule"}, "rule 'r' must be a mapping"),
        ({"": CAMERA}, "non-empty string"),
        ({1: CAMERA}, "non-empty string"),
        ({"r": {"below": 1, "action": "a"}}, "'signal' must be a non-empty string"),
        ({"r": {"signal": "", "below": 1, "action": "a"}}, "'signal' must be a non-empty string"),
        ({"r": {"signal": 3, "below": 1, "action": "a"}}, "'signal' must be a non-empty string"),
        ({"r": {"signal": "s", "below": 1}}, "'action' must be a non-empty string"),
        ({"r": {"signal": "s", "below": 1, "action": None}}, "'action' must be"),
        ({"r": {"signal": "s", "action": "a"}}, "needs 'below' or 'above'"),
        ({"r": {"signal": "s", "below": None, "above": None, "action": "a"}}, "needs 'below'"),
        ({"r": {"signal": "s", "below": "0.3", "action": "a"}}, "'below' must be a finite"),
        ({"r": {"signal": "s", "above": True, "action": "a"}}, "'above' must be a finite"),
        ({"r": {"signal": "s", "above": float("nan"), "action": "a"}}, "'above' must be"),
        ({"r": {"signal": "s", "below": math.inf, "action": "a"}}, "'below' must be"),
        ({"r": {"signal": "s", "below": [1], "action": "a"}}, "'below' must be"),
        ({"r": {"signal": "s", "below": 20, "above": 10, "action": "a"}}, "must be smaller"),
        ({"r": {"signal": "s", "below": 10, "above": 10, "action": "a"}}, "must be smaller"),
    ],
)
def test_malformed_rules_raise(rules, fragment):
    with pytest.raises(RulesError) as exc:
        evaluate(rules, {"s": [0]})
    assert any(fragment in p for p in exc.value.problems), exc.value.problems
    assert validate_rules(rules) == exc.value.problems


def test_one_bad_rule_fails_the_whole_set_and_lists_every_problem():
    rules = {
        "good": CAMERA,
        "bad1": {"signal": "s", "action": "a"},
        "bad2": {"signal": "", "below": 1, "action": "a"},
    }
    with pytest.raises(RulesError) as exc:
        evaluate(rules, {"brightness": [0.0]})
    assert len(exc.value.problems) == 2
    assert "bad1" in str(exc.value) and "bad2" in str(exc.value)


def test_rules_error_is_a_value_error():
    assert issubclass(RulesError, ValueError)


def test_signals_must_be_a_mapping():
    with pytest.raises(TypeError):
        evaluate(APIS_RULES, [("brightness", [0.1])])
    with pytest.raises(TypeError):
        evaluate(APIS_RULES, None)


def test_valid_rules_have_no_problems():
    assert validate_rules(APIS_RULES) == []
    assert validate_rules({}) == []


def test_extra_rule_keys_are_ignored():
    rule = {**CAMERA, "description": "dark frames", "enabled": False, "priority": 9}
    assert validate_rules({"r": rule}) == []
    assert evaluate({"r": rule}, {"brightness": [0.1]})[0].action == "safe_pose"


# --- purity ----------------------------------------------------------------------------


def test_inputs_are_not_mutated():
    rules = copy.deepcopy(APIS_RULES)
    signals = {"brightness": [0.41, 0.18], "gripper_current_ma": (1000,)}
    signals_before = copy.deepcopy(signals)
    evaluate(rules, signals)
    assert rules == APIS_RULES and signals == signals_before


def test_fired_is_immutable():
    fired = evaluate({"r": CAMERA}, {"brightness": [0.1]})[0]
    with pytest.raises(AttributeError):
        fired.action = "other"  # type: ignore[misc]


def test_rules_module_imports_only_stdlib():
    tree = ast.parse(Path(rules_module.__file__).read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
    assert imported <= {"math", "collections", "dataclasses", "typing"}, imported


# --- replay (APIs §2.7) ----------------------------------------------------------------


def test_fired_actions_are_distinct_in_order():
    rules = {
        "a": {"signal": "s", "above": 0, "action": "safe_pose"},
        "b": {"signal": "s", "above": 0, "action": "alert"},
        "c": {"signal": "s", "above": 0, "action": "safe_pose"},
    }
    assert fired_actions(evaluate(rules, {"s": [1]})) == ["safe_pose", "alert"]
    assert fired_actions([]) == []


def test_replay_passes_when_resolving_action_fires():
    # APIs §2.5 example: v1 had below 0.2; the overlay raised it to 0.35.
    candidate = {"camera_occluded": {**CAMERA, "below": 0.35}}
    assert replay_passes(candidate, {"brightness": [0.41, 0.18, 0.12, 0.33]}, "safe_pose")


def test_replay_fails_when_resolving_action_does_not_fire():
    assert not replay_passes(APIS_RULES, {"brightness": [0.9]}, "safe_pose")
    assert not replay_passes(APIS_RULES, {"brightness": [0.1]}, "backoff_retry")


def test_replay_threshold_change_decides_the_result():
    signals = {"brightness": [0.3]}
    old = {"camera_occluded": {**CAMERA, "below": 0.2}}
    new = {"camera_occluded": {**CAMERA, "below": 0.35}}
    assert not replay_passes(old, signals, "safe_pose")
    assert replay_passes(new, signals, "safe_pose")


@pytest.mark.parametrize("action", [None, ""])
def test_replay_without_resolving_action_never_passes(action):
    assert not replay_passes(APIS_RULES, {"brightness": [0.0]}, action)


def test_replay_with_malformed_rules_raises():
    with pytest.raises(RulesError):
        replay_passes({"r": {"signal": "s"}}, {"s": [0]}, "a")


# --- skipped samples and replay reports ------------------------------------------------


def test_skipped_samples_reasons_in_order():
    signals = {
        "brightness": [0.5, None, "0.2", True, float("nan"), [0.1], 0.4],
        "gripper_current_ma": 1000,
    }
    assert skipped_samples(APIS_RULES, signals) == [
        Skipped("brightness", 1, "null"),
        Skipped("brightness", 2, "string"),
        Skipped("brightness", 3, "boolean"),
        Skipped("brightness", 4, "NaN"),
        Skipped("brightness", 5, "not a number (list)"),
    ]


def test_skipped_samples_missing_empty_and_bad_scalar():
    rules = {
        "a": {"signal": "x", "above": 0, "action": "a"},
        "b": {"signal": "y", "above": 0, "action": "b"},
        "c": {"signal": "z", "above": 0, "action": "c"},
        "d": {"signal": "z", "below": 0, "action": "d"},
    }
    assert skipped_samples(rules, {"y": [], "z": "7"}) == [
        Skipped("x", None, "missing"),
        Skipped("y", None, "empty"),
        Skipped("z", None, "string"),
    ]


def test_skipped_samples_only_for_signals_rules_use():
    assert skipped_samples({"r": CAMERA}, {"brightness": [0.5], "other": [None]}) == []


def test_skipped_samples_validates_like_evaluate():
    with pytest.raises(RulesError):
        skipped_samples({"r": {}}, {})
    with pytest.raises(TypeError):
        skipped_samples({"r": CAMERA}, [])


def test_replay_report_pass():
    candidate = {"camera_occluded": CAMERA}
    report = replay_report(candidate, {"brightness": [0.41, 0.18]}, "safe_pose")
    assert report.passed and report.skipped == ()
    assert (
        report.why()
        == "replay passed: camera_occluded fired safe_pose (brightness[1] = 0.18 < 0.35)"
    )
    assert replay_report(APIS_RULES, {"gripper_current_ma": [950]}, "backoff_retry").why() == (
        "replay passed: gripper_stall fired backoff_retry (gripper_current_ma[0] = 950 > 900)"
    )


def test_replay_report_explains_string_samples():
    report = replay_report({"r": CAMERA}, {"brightness": ["0.1", "0.2"]}, "safe_pose")
    assert not report.passed
    assert report.why() == (
        "replay failed: safe_pose did not fire (fired: nothing); ignored brightness: 2 string"
    )


def test_replay_report_lists_other_fired_rules():
    report = replay_report(
        APIS_RULES, {"gripper_current_ma": [1000], "brightness": [0.9]}, "safe_pose"
    )
    assert (
        report.why()
        == "replay failed: safe_pose did not fire (fired: gripper_stall->backoff_retry)"
    )


@pytest.mark.parametrize("action", [None, ""])
def test_replay_report_without_resolving_action(action):
    report = replay_report(APIS_RULES, {"brightness": [0.0]}, action)
    assert not report.passed
    assert report.why() == "replay failed: the incident has no resolving_action"


@pytest.mark.parametrize(
    ("signals", "action"),
    [
        ({"brightness": [0.1]}, "safe_pose"),
        ({"brightness": [0.9]}, "safe_pose"),
        ({"gripper_current_ma": [901]}, "safe_pose"),
        ({}, "backoff_retry"),
        ({"brightness": [0.1]}, None),
    ],
)
def test_replay_report_agrees_with_replay_passes(signals, action):
    assert replay_report(APIS_RULES, signals, action).passed == replay_passes(
        APIS_RULES, signals, action
    )
