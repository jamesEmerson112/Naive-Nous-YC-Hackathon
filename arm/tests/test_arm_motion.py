"""The arm's obstacle reflex and its run record, on the simulated arm: the reflex is bounded (base only, hard
caps on swing and speed, back home at the end), irregular on noisy readings, and lets the job continue once
the way really clears; arm_status.py's report reads the record correctly."""
import contextlib
import io
import sys
import time
import types

import simpod
from simpod import JOINTS, REST, SimPod
import agent
import safe_replay

PAN = JOINTS.index("shoulder_pan")
JOB = "cd .. && python3 -u arm/safe_replay.py --min-mm 200 --on-blocked reflex --arm-port sim-arm "


def reflex_poses(pod):
    """The commanded poses up to the first frame of the wave ramp (the reflex only: it's all that ran)."""
    return pod.arm.history


HELD = [JOINTS.index(j) for j in ("shoulder_lift", "elbow_flex", "wrist_flex")]
ROLL, GRIP = JOINTS.index("wrist_roll"), JOINTS.index("gripper")


def max_step(poses, i):
    return max(abs(b[i] - a[i]) for a, b in zip(poses, poses[1:]))


def test_reflex_stays_within_its_limits_and_returns_home():
    pod = SimPod("v1.7")
    code, out, _, _ = pod.sh(JOB)
    poses = reflex_poses(pod)
    assert code == 4 and "REFLEX GAVE UP" in out and len(poses) > 100
    for p in poses:
        assert all(abs(p[i] - REST[i]) < 1e-9 for i in HELD)      # shoulder, elbow, wrist flex never move
        assert abs(p[PAN] - REST[PAN]) <= 20.0 + 1e-6
        assert abs(p[ROLL] - REST[ROLL]) <= safe_replay.REFLEX_ROLL_MAX + 1e-6
        assert 0.0 <= p[GRIP] <= safe_replay.REFLEX_GRIP_MAX + 1e-6
    assert max_step(poses, PAN) <= 55.0 / 50 + 1e-6               # default 55 deg/s at 50 Hz
    assert max_step(poses, ROLL) <= safe_replay.REFLEX_ROLL_SPEED / 50 + 1e-6
    assert max_step(poses, GRIP) <= safe_replay.REFLEX_GRIP_SPEED / 50 + 1e-6
    assert all(abs(poses[-1][i] - REST[i]) < 1e-6 for i in range(len(JOINTS)))   # back where it started
    assert max(p[GRIP] for p in poses) > 5 and max(abs(p[ROLL] - REST[ROLL]) for p in poses) > 5   # visibly moving


def test_hard_caps_hold_whatever_the_flags_ask():
    pod = SimPod("v1.7")
    code, _, _, _ = pod.sh(JOB + "--reflex-amp 90 --reflex-speed 500")
    poses = reflex_poses(pod)
    assert code == 4
    assert max(abs(p[PAN] - REST[PAN]) for p in poses) <= safe_replay.REFLEX_AMP_MAX + 1e-6
    assert max_step(poses, PAN) <= safe_replay.REFLEX_SPEED_MAX / safe_replay.REFLEX_HZ + 1e-6
    assert max(abs(p[ROLL] - REST[ROLL]) for p in poses) <= safe_replay.REFLEX_ROLL_MAX + 1e-6
    assert all(abs(p[i] - REST[i]) < 1e-9 for p in poses for i in HELD)


def test_noisy_readings_make_an_irregular_back_and_forth():
    rx = agent.run_arm(SimPod("v1.7"), agent.ArmConfig(port="sim-arm"))["motion_report"]["obstacle_reflex"]
    targets = rx["base_targets_deg"]
    assert rx["swings"] >= 6 and rx["direction_changes"] >= 4
    assert len({round(t) for t in targets}) >= 4                     # not one fixed pair of angles


def test_a_real_obstacle_that_moves_away_lets_the_job_run():
    """Good firmware, a hand in front for a moment: the reflex swings, the way clears, the arm calmly goes back to
    where it started and the wave plays."""
    pod = SimPod("v1.0", distance_mm=900)
    pod.plan_object(0.0, 4.0, 120)                      # in front from the job's start, for 4 s
    r = agent.run_arm(pod, agent.ArmConfig(port="sim-arm"))
    rx, pb = r["motion_report"]["obstacle_reflex"], r["motion_report"]["playback"]
    assert r["exit"] == 0 and rx["ran"] and rx["cleared"] and pb["complete"]
    first_ramp = next(i for i, p in enumerate(pod.arm.history) if abs(p[JOINTS.index("elbow_flex")] - REST[2]) > 1e-6)
    assert all(abs(a - b) < 1e-6 for a, b in zip(pod.arm.history[first_ramp - 1], REST))   # back home before the wave


def test_no_echo_refuses_without_moving():
    pod = SimPod("v1.0")
    pod.echo_ok = False                                 # ECHO wire off: every ping is no_echo
    code, out, _, _ = pod.sh(JOB)
    assert code == 2 and "not trustworthy" in out and pod.arm.moves == 0


def test_report_compares_measured_with_commanded():
    pod = SimPod("v1.0")
    pod.arm.send_action = lambda a, _s=pod.arm.send_action: _s({k: (v * 0.5 if k == "elbow_flex.pos" else v)
                                                                for k, v in a.items()})   # elbow reaches half
    rep = agent.run_arm(pod, agent.ArmConfig(port="sim-arm"))["motion_report"]["playback"]
    assert rep["complete"] and rep["range_coverage"]["shoulder_pan"] == 1.0
    assert rep["range_coverage"]["elbow_flex"] < 0.9 and rep["median_tracking_error_deg"]["elbow_flex"] > 5
    assert rep["motion_file"]["frames"] == 663 and rep["motion_file"]["joint_range"]["shoulder_pan"] == [-47.4, 46.2]


def test_connect_gives_each_servo_retries_for_torque_on():
    """LeRobot's configure() turns torque on with one try per motor; one lost reply failed a bench run (Nov 8).
    The wrapper gives every call the retries, and a caller's own num_retry (disconnect passes 5) still wins."""
    calls = []

    class Bus:
        def enable_torque(self, motors=None, num_retry=0):
            calls.append(("on", motors, num_retry))

        def disable_torque(self, motors=None, num_retry=0):
            calls.append(("off", motors, num_retry))

    bus = safe_replay.with_torque_retries(Bus())
    bus.disable_torque(None)                    # what MotorsBus.torque_disabled() calls...
    bus.enable_torque(None)                     # ...and this is the call that timed out on the bench
    bus.disable_torque(num_retry=7)             # an explicit num_retry is kept
    assert calls == [("off", None, safe_replay.TORQUE_RETRIES), ("on", None, safe_replay.TORQUE_RETRIES),
                     ("off", None, 7)]


def test_an_unreachable_arm_stops_the_job_before_anything_moves(monkeypatch):
    """The servo bus doesn't answer even with the retries: exit 1 with a clear message, before any motion."""
    pod = SimPod("v1.0")
    clock = simpod.FakeClock(pod.t)

    def unreachable(*args):
        raise ConnectionError("Failed to enable torque after 6 tries on 'shoulder_lift' (motor ID 2): timed out")

    monkeypatch.setattr(safe_replay, "connect_arm", unreachable)
    monkeypatch.setattr(safe_replay, "Ultrasonic", lambda port=None: simpod.SimSensor(pod, clock))
    monkeypatch.setattr(safe_replay, "time", types.SimpleNamespace(
        sleep=clock.sleep, time=clock.time, monotonic=clock.monotonic, localtime=time.localtime, strftime=time.strftime))
    monkeypatch.setattr(sys, "argv", ["safe_replay.py", "--min-mm", "200", "--arm-port", "x", "--no-record"])
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = safe_replay.main()
    assert code == 1 and "ARM NOT REACHABLE" in buf.getvalue() and pod.arm.moves == 0
