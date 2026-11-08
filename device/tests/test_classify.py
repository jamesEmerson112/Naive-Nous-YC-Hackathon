"""supervisor/monitor.py classify(): a run result in, an incident (or None) out."""
import monitor
import pytest


def result(**changes):
    """A healthy run result, shaped like monitor.run_app's, with `changes` applied."""
    healthy = {"scenario": "normal", "exit_code": 0, "hung": False, "timed_out": False, "steps": 120,
               "steps_wanted": 120, "seconds": 0.4, "log_tail": ["EXIT ok steps=120"], "stack_dump": "",
               "telemetry_tail": [], "gripper_max": 10.0, "shoulder_lift_max": 15.0}
    return healthy | changes


def test_a_healthy_run_is_not_an_incident():
    assert monitor.classify(result()) is None


@pytest.mark.parametrize("changes, tier, kind", [
    ({"hung": True, "exit_code": -9, "steps": 30}, 2, "hang"),  # the supervisor's kill doesn't make it a crash
    ({"timed_out": True, "exit_code": -9}, 2, "timeout"),
    ({"exit_code": 1, "steps": 39}, 1, "crash"),
    ({"gripper_max": 90.0}, 3, "spec_violation"),
    ({"steps": 60}, 1, "stopped_early"),
], ids=["hang", "timeout", "crash", "spec_violation", "stopped_early"])
def test_each_failure_gets_its_tier(changes, tier, kind):
    incident = monitor.classify(result(**changes))
    assert (incident["tier"], incident["kind"]) == (tier, kind)


def test_a_crash_outranks_a_spec_violation():
    assert monitor.classify(result(exit_code=1, gripper_max=90.0))["kind"] == "crash"


def test_the_gripper_limit_is_above_15():
    assert monitor.classify(result(gripper_max=15.0)) is None
    assert monitor.classify(result(gripper_max=15.1))["kind"] == "spec_violation"


def test_a_run_that_sent_no_commands_stopped_early():
    assert monitor.classify(result(steps=0, gripper_max=None, shoulder_lift_max=None))["kind"] == "stopped_early"


def test_an_incident_carries_the_evidence():
    crashed = result(exit_code=1, steps=39, log_tail=["Traceback (most recent call last):", "KeyError: 'near_cm'"])
    incident = monitor.classify(crashed)
    assert incident["scenario"] == "normal"
    assert incident["exit_code"] == 1
    assert incident["log_tail"] == crashed["log_tail"]
    assert "39 of 120" in incident["detail"]
    assert {"incident_id", "occurred_at", "stack_dump", "telemetry_tail"} <= incident.keys()
    assert monitor.classify(crashed)["incident_id"] != incident["incident_id"]
