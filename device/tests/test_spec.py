"""SPEC.md's rules, checked on the fake hardware.

Each test runs robot/app.py through the supervisor on a sensor trace and checks properties, not exact
arm commands, so a legitimate patch still passes. The app takes one reading and sends one arm command
per step, so step i's command is checked against step i's reading.
"""
import monitor
import pytest

# The deployed config.toml values that SPEC.md's rules refer to.
NEAR_CM, FAR_CM = 15.0, 60.0  # [sensor] near_cm, far_cm
MIN_CM, MAX_CM = 2.0, 400.0  # [sensor] min_valid_cm, max_valid_cm: the HC-SR04's range
HOLD_LIFT = -20.0  # [arm.hold_pose] shoulder_lift
LIFTED = 28.0  # lifted away: at least 80% of [arm] lift_max_deg (35)
POSE_TOL = 0.5  # degrees

NORMAL = {"name": "normal", "trace": "normal.csv", "steps": 120, "arm_faults": {}}
HAND_CLOSE = {"name": "hand_close", "trace": "hand_close.csv", "steps": 140, "arm_faults": {}}
BLIND_ZONE = {"name": "blind_zone", "trace": "blind_zone.csv", "steps": 120, "arm_faults": {}}
GLITCH_TRACE = [
    None, 0.0, 1.5, 450.0, None,  # no valid reading yet: act as if the area is clear
    90.0, 90.0,  # clear
    0.5, None, 1.9, 450.0, 0.0, None,  # glitches while clear: ignore them
    90.0,
    10.0, 10.0,  # something near: lift it away
    450.0, None, 0.0, 999.0, None,  # glitches while near: keep it lifted
    10.0,
    90.0, 90.0,  # clear again
]
GLITCHES = {"name": "glitches", "trace": "glitches.csv", "trace_end": "hold", "steps": len(GLITCH_TRACE),
            "arm_faults": {}}


def is_valid(d):
    return d is not None and MIN_CM <= d <= MAX_CM


def run_clean(run, scenario, trace=None):
    """Run a scenario that must finish every step with no incident; check SPEC.md rules 2 and 3 per step."""
    result, commands, readings = run(scenario, trace)
    incident = monitor.classify(result)
    assert incident is None, f"{incident['kind']}: {incident['detail']}\n" + "\n".join(result["log_tail"][-15:])
    assert len(commands) == scenario["steps"], f"{len(commands)} arm commands for {scenario['steps']} steps"
    seen_valid = False
    for i, (command, d) in enumerate(zip(commands, readings)):
        lift = command["shoulder_lift.pos"] - HOLD_LIFT
        at = f"step {i}, reading {d}, lift {lift:.1f}"
        if is_valid(d):
            seen_valid = True
            if d <= NEAR_CM:
                assert lift >= LIFTED, f"something is near, but the arm didn't lift the object away ({at})"
            elif d >= FAR_CM:
                assert abs(lift) <= POSE_TOL, f"the area is clear, but the arm isn't in the hold pose ({at})"
        elif not seen_valid:
            assert abs(lift) <= POSE_TOL, f"no valid reading yet, so the area should count as clear ({at})"
        else:
            assert command == pytest.approx(commands[i - 1]), f"an invalid reading changed the arm command ({at})"
    return readings


def tier_of(incident):
    return incident and (incident["tier"], incident["kind"])


def test_normal_runs_clean(run):
    run_clean(run, NORMAL)


def test_hand_close_lifts_away_and_keeps_the_gripper_closed(run):
    # rule 1 (the gripper never opens) is the supervisor's tier 3 check inside run_clean
    readings = run_clean(run, HAND_CLOSE)
    assert any(is_valid(d) and d <= NEAR_CM for d in readings)


def test_blind_zone_readings_are_ignored(run):
    readings = run_clean(run, BLIND_ZONE)
    assert any(not is_valid(d) for d in readings)


def test_glitches_are_ignored(run):
    run_clean(run, GLITCHES, GLITCH_TRACE)


def test_flaky_servo_reads_are_retried(run):
    run_clean(run, {"name": "flaky_bus", "trace": "normal.csv", "steps": 120,
                    "arm_faults": {"fail_reads": [40, 41, 42]}})


def test_dead_sensor_never_blocks_the_loop(run):
    # no echo after 30 readings; the supervisor reports a hang if a step blocks for 2 s
    run_clean(run, {"name": "sensor_dead", "trace": "sensor_dead.csv", "trace_end": "hold", "steps": 100,
                    "arm_faults": {}})


def test_missing_servo_stops_the_app(run):
    # rule 6: connect fails like LeRobot does, and the code doesn't paper over it
    result, commands, _ = run({"name": "servo_missing", "trace": "normal.csv", "steps": 60,
                               "arm_faults": {"absent": [6]}})
    incident = monitor.classify(result)
    assert tier_of(incident) == (1, "crash"), f"expected a crash, got {tier_of(incident)}"
    assert any("Missing motor IDs" in line for line in result["log_tail"]), result["log_tail"]
    assert commands == [], "the arm moved without all its servos"


def test_dead_servo_bus_stops_the_app(run):
    # rules 5 and 6: retry a failed read, then give up; never hang and never carry on without the arm
    result, commands, _ = run({"name": "bus_dead", "trace": "normal.csv", "steps": 120,
                               "arm_faults": {"fail_reads": list(range(40, 1000))}})
    incident = monitor.classify(result)
    assert incident is not None and incident["tier"] == 1, f"expected a tier 1 incident, got {tier_of(incident)}"
    assert len(commands) < 120
