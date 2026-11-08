"""R1: RealArm against a mock servo bus (no hardware). Run from prototypes/arm-sim:

    PYTHONPATH=. .venv/bin/python tests/test_realarm.py      (or: python -m pytest tests/)
"""
import threading
import time

import numpy as np

from armsim import level1
from armsim.arm import JOINTS
from armsim.host import Skills, lift, romulus_identity
from armsim.mockrobot import MockRobot
from armsim.real import STALL_NM, RealArm

POSES = {   # taught on the real arm with arm/bin/prim-record.sh --pose ... (made-up values here)
    "rest": dict(zip(JOINTS, [0, -95, 90, 55, 90, 0])),
    "home": dict(zip(JOINTS, [5, -30, 30, 90, 90, 60])),
    "above_block": dict(zip(JOINTS, [4, -11, 6, 95, 90, 60])),
    "pick": dict(zip(JOINTS, [4, -5, 33, 63, 90, 60])),
}
WAVE = {"name": "wave", "fps": 50, "joints": list(JOINTS),
        "frames": [[0, -40, 30, 20 + 10 * np.sin(i / 8), 90, 60] for i in range(60)]}


def arm_with(**kw):
    arm = RealArm(robot=MockRobot(**kw)).connect()
    arm.taught_poses = POSES
    return arm, Skills(arm, poses=POSES, wave=WAVE)


def test_moves_arrive_and_a_dead_servo_stalls():
    arm, skills = arm_with()
    assert all(arm.registers.values()), arm.registers
    assert skills.go_home()["arrived"]
    arm.robot.stuck = {2}                                   # elbow servo dead
    r = skills.reach()
    assert not r["arrived"] and r["worst_joint"] == "elbow_flex", r
    found = level1.check_arrival(r, load_nm=arm.load_nm("elbow_flex"))
    assert found and found[0]["kind"] == "joint_stall", found


def test_grip_backs_off_below_the_overload_and_lifts_in_joint_space():
    arm, skills = arm_with(block_at=18.0)
    skills.go_home()
    arm.goto(POSES["pick"], duration=0.6)
    g = skills.grip()
    assert g["grasp"]["holding"], g
    assert 0.4 * STALL_NM <= g["grasp"]["load_nm"] < 0.9 * STALL_NM, g
    assert not any(g["status"]), g
    r = lift(arm)
    assert r["arrived"] and r["mode"] == "joint" and r["grasp"]["holding"], r


def test_overload_bit_when_held_at_the_limit():
    arm, _ = arm_with(block_at=18.0)
    arm.goto({**POSES["pick"], "gripper": 0}, duration=0.3, settle_timeout=0.1)
    arm.hold(0.8)
    assert arm.status()[5] == 32, arm.status()


def test_unknown_registers_are_skipped():
    arm = RealArm(robot=MockRobot(known=("Present_Position",))).connect()
    assert arm.registers == {"Present_Load": False, "Status": False, "Present_Velocity": False}
    arm.hold(0.1)
    assert arm.status() == [0] * 6 and arm.load_nm("gripper") == 0.0


def test_approach_steps_in_joint_space_on_the_sensor():
    arm, skills = arm_with()
    skills.go_home()
    above = np.array([POSES["above_block"][j] for j in JOINTS])
    pick = np.array([POSES["pick"][j] for j in JOINTS])
    stop = threading.Event()

    def pod():                                              # a pod reading 110 mm over the block, 60 at pick
        while not stop.is_set():
            q = arm._read_vec()
            f = float(np.clip(np.dot(q - above, pick - above) / np.dot(pick - above, pick - above), 0, 1))
            mm = int(110 - 50 * f)
            arm.d_rows.append({"ts": time.time(), "mm": mm, "us": int(mm * 2000 / 343), "st": "ok", "pred_mm": None})
            time.sleep(0.1)

    threading.Thread(target=pod, daemon=True).start()
    try:
        r = skills.approach()
    finally:
        stop.set()
    assert r.get("mode") == "joint" and r["arrived"] and r["switched_at_mm"] <= 75, r
    assert not arm.use_ik and arm.predicted_mm() is None   # no prediction until calibrated


def test_the_romulus_device_follows_the_arm_not_the_pod():
    assert romulus_identity("real") == ("real", "arm-1")
    assert romulus_identity("sim") == ("sim", "arm-twin-1")     # also with --pod real: still the MuJoCo arm
    assert romulus_identity("sim", "arm-twin-9") == ("sim", "arm-twin-9")
    assert romulus_identity("real", "arm-7") == ("real", "arm-7")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            t0 = time.time()
            fn()
            print(f"ok  {name}  ({time.time() - t0:.1f} s)")
