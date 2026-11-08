"""The real SO-101 behind the same interface as SimArm, so the arm host, the watchdog, the skills and
the Hermes plugin run unchanged on the bench (R1). It wraps Andre's `prims.Arm` (arm/prims/core.py:
LeRobot 0.7's SO101Follower, connect_calibrated, the checked-in calibration) and adds what his class
lacks: an arrival check, the gripper's load, and the servos' Status register.

    arm = RealArm()                    # ARM_PORT etc. from arm/arm.env, as Andre's bin/ scripts
    arm = RealArm(robot=MockRobot())   # tests without hardware (armsim/mockrobot.py)

What changes on the real arm, on purpose:
  - Distance always comes from podd (pod_mode), never from the arm.
  - Moves between taught poses go in joint space (`use_ik = False`): the MuJoCo mirror only becomes
    trustworthy for IK and distance prediction once its per-joint offsets are measured on the bench
    (real_calibration.json, see `kinematics_ok`). Until then the residual checks stay off.
  - Servo registers are probed at connect: one that LeRobot's table doesn't name is skipped, not fatal.
"""
import json
import time
from collections import deque
from pathlib import Path

import mujoco
import numpy as np

from .arm import ARM_JOINTS, JOINTS, OVERLOAD, OVERLOAD_AFTER_S, SCENE_OBJECTS, SimArm

REPO = Path(__file__).resolve().parents[3]
CALIBRATION = Path(__file__).resolve().parent / "real_calibration.json"
STALL_NM = 2.94          # STS3215 at 12 V, about 30 kg*cm: load fractions are reported in N*m against it
LOAD_ALARM = 0.95        # held at this fraction of full load for OVERLOAD_AFTER_S, report OVERLOAD


def _andre_arm(env=None):
    import sys
    arm_dir = REPO / "arm"
    if str(arm_dir) not in sys.path:
        sys.path.insert(0, str(arm_dir))
    from prims.core import Arm            # imports LeRobot lazily, inside make_arm()
    return Arm(env=env)


def andre_library(library=REPO / "arm" / "prims" / "library"):
    """The poses and the wave taught by hand on the real arm (Andre's prims library).
    poses.json is {name: {joint: deg}}; wave.json is {name, fps, joints, frames}."""
    poses_file = library / "poses.json"
    poses = json.loads(poses_file.read_text()) if poses_file.exists() else {}
    wave_file = library / "wave.json"
    wave = json.loads(wave_file.read_text()) if wave_file.exists() else None
    return poses, wave


class RealArm:
    kind = "real"
    taught_poses = None

    def __init__(self, *, robot=None, recorder=None, fps=50, env=None):
        self.prims_arm = None if robot is not None else _andre_arm(env)
        self.robot = robot if robot is not None else self.prims_arm.robot
        self.fps, self.recorder = fps, recorder
        self.hooks, self.watchers, self.faults = [], [], {}
        self.d_rows, self.pred_hist = deque(maxlen=200), deque(maxlen=200)
        self.pod_mode, self.halt_reason, self._parking = True, None, False
        self.mirror = SimArm()                # MuJoCo model for kinematics only, posed from readings
        cal = json.loads(CALIBRATION.read_text()) if CALIBRATION.exists() else {}
        self.offset = np.array(cal.get("offset_deg", [0.0] * 6), dtype=float)
        self.sign = np.array(cal.get("sign", [1.0] * 6), dtype=float)
        self.kinematics_ok = bool(cal.get("measured"))
        self.use_ik = self.kinematics_ok
        self.t0 = time.monotonic()
        self._goal = None
        self._torque_on = False
        self._at_limit = np.zeros(6)
        self._last_tick = None
        self.registers = {}

    # ---- lifecycle ---------------------------------------------------------------------------
    def connect(self):
        if self.prims_arm is not None:
            self.prims_arm.connect()          # goal = present, then connect with the checked-in calibration
        elif hasattr(self.robot, "connect"):
            self.robot.connect()
        self._torque_on = True
        self.registers = {name: self._probe(name) for name in ("Present_Load", "Status", "Present_Velocity")}
        self._goal = self._read_vec()
        return self

    def disconnect(self):
        if getattr(self.robot, "is_connected", True):
            self.robot.disconnect()

    def _probe(self, name):
        try:
            self.robot.bus.sync_read(name, normalize=False)
            return True
        except Exception:                   # LeRobot raises KeyError (or similar) for an unknown name
            return False

    # ---- the SimArm interface ----------------------------------------------------------------
    @property
    def t(self):
        return time.monotonic() - self.t0

    def _read_vec(self):
        obs = self.robot.get_observation()
        return np.array([float(obs[f"{j}.pos"]) for j in JOINTS])

    def read(self):
        return {f"{j}.pos": float(v) for j, v in zip(JOINTS, self._read_vec())}

    def _vec(self, pose):
        if isinstance(pose, dict):
            cur = self._goal if self._goal is not None else self._read_vec()
            return np.array([float(pose.get(f"{j}.pos", pose.get(j, c))) for j, c in zip(JOINTS, cur)])
        return np.array(pose, dtype=float)

    def send(self, action):
        # The host sets taught_poses from Andre's library; {} means poses.json is missing, and
        # host.Skills then falls back to the sim's poses, which don't match this arm's zero.
        if self.taught_poses is not None and not self.taught_poses:
            raise RuntimeError("no taught poses on the real arm (arm/prims/library/poses.json is missing): "
                               "teach them with Andre first: arm/bin/prim-record.sh --pose rest home "
                               "above_block pick")
        if not self._torque_on:
            self.robot.bus.enable_torque()
            self._torque_on = True
        self._goal = self._vec(action)
        self.robot.send_action({f"{j}.pos": float(v) for j, v in zip(JOINTS, self._goal)})

    def _pose_mirror(self, q_deg):
        m = self.mirror
        sim_deg = self.sign * np.asarray(q_deg, dtype=float) + self.offset
        m.d.qpos[m._qadr] = m.to_ctrl(sim_deg.tolist())
        mujoco.mj_kinematics(m.m, m.d)

    def _loads(self):
        if not self.registers.get("Present_Load"):
            return np.zeros(6)
        raw = self.robot.bus.sync_read("Present_Load", normalize=False)
        mags = []
        for j in JOINTS:
            v = int(raw.get(j, 0))
            mags.append((v & 0x3FF) / 1000.0)        # sign-magnitude: bit 10 is the direction
        return np.array(mags) * STALL_NM

    def tick(self):
        """One control period: read, update the load timers, log the predicted distance, pace."""
        q = self._read_vec()
        loads = self._loads()
        now = time.monotonic()
        dt = 0.0 if self._last_tick is None else min(0.2, now - self._last_tick)
        self._last_tick = now
        self._at_limit = np.where(loads >= LOAD_ALARM * STALL_NM, self._at_limit + dt, 0.0)
        self._q, self._load = q, loads
        self._pose_mirror(q)
        self.pred_hist.append((time.time(), self.predicted_mm(objects=SCENE_OBJECTS)))
        if self.recorder:
            self.recorder.joints(self.t, self._goal if self._goal is not None else q, q, loads, self.status())
        for hook in self.hooks:
            hook(self)
        nxt = now + 1.0 / self.fps
        while time.monotonic() < nxt:
            time.sleep(max(0.0, nxt - time.monotonic()))

    def halted(self):
        return self.halt_reason is not None and not self._parking

    def hold(self, seconds):
        for _ in range(max(1, round(seconds * self.fps))):
            self.tick()

    def goto(self, pose, duration=1.5, *, tol_deg=2.0, settle_timeout=2.0):
        start = self._read_vec() if self._goal is None else self._goal.copy()
        goal = self._vec(pose)
        n = max(1, round(duration * self.fps))
        for i in range(1, n + 1):
            if self.halted():
                return self._stop()
            a = i / n
            self.send(start + (goal - start) * (a * a * (3 - 2 * a)))
            self.tick()
        return self.wait_arrived(goal, tol_deg=tol_deg, timeout=settle_timeout)

    def _stop(self):
        self.send(self._read_vec())                   # hold where it is
        return {"arrived": False, "halted": self.halt_reason}

    def wait_arrived(self, goal=None, *, tol_deg=2.0, timeout=2.0):
        goal = self._goal if goal is None else np.asarray(goal, dtype=float)
        t0, prev = self.t, None
        while True:
            if self.halted():
                return self._stop()
            self.tick()
            err = np.abs(goal - self._q)[:5]
            moving = prev is not None and np.abs(self._q - prev)[:5].max() > 0.05
            prev = self._q.copy()
            if err.max() <= tol_deg and not moving:
                return {"arrived": True, "max_err_deg": round(float(err.max()), 2), "seconds": round(self.t - t0, 2)}
            if self.t - t0 >= timeout:
                return {"arrived": False, "max_err_deg": round(float(err.max()), 2), "seconds": round(self.t - t0, 2),
                        "worst_joint": ARM_JOINTS[int(err.argmax())]}

    def play(self, prim, speed=1.0):
        names = prim.get("joints") or list(JOINTS)
        cur = self._goal if self._goal is not None else self._read_vec()
        order = [names.index(j) if j in names else None for j in JOINTS]
        frames = [[f[i] if i is not None else c for i, c in zip(order, cur)] for f in prim["frames"]]
        r = self.goto(frames[0], duration=1.0)
        if r.get("halted"):
            return r
        src_fps = prim.get("fps", 50) * speed
        n = max(1, round(len(frames) / src_fps * self.fps))
        for i in range(n):
            if self.halted():
                return self._stop()
            self.send(frames[min(len(frames) - 1, int(i * src_fps / self.fps))])
            self.tick()
        return self.wait_arrived(timeout=1.0)

    def park(self, rest):
        self._parking = True
        try:
            r = self.goto(rest)
        finally:
            self._parking = False
        self.relax()
        return {**r, "torque": "off"}

    def relax(self):
        self.robot.bus.disable_torque()
        self._torque_on = False

    def status(self):
        """Servo alarm bits: the Status register where LeRobot names it, OR the overload timer."""
        bits = [OVERLOAD if s >= OVERLOAD_AFTER_S else 0 for s in self._at_limit]
        if self.registers.get("Status"):
            raw = self.robot.bus.sync_read("Status", normalize=False)
            bits = [b | int(raw.get(j, 0)) for b, j in zip(bits, JOINTS)]
        return bits

    def commanded(self):
        """The pose the servos are being told to hold (LeRobot units, JOINTS order)."""
        return (self._goal if self._goal is not None else self._read_vec()).tolist()

    def load_nm(self, joint):
        loads = getattr(self, "_load", None)
        return float(loads[JOINTS.index(joint)]) if loads is not None else 0.0

    def grip_limit_nm(self):
        return STALL_NM

    def grasp_check(self, *, min_open=4.0, min_load=0.25):
        q = getattr(self, "_q", None)
        if q is None:
            self.tick()
            q = self._q
        load = self.load_nm("gripper")
        return {"holding": float(q[5]) > min_open and load > min_load, "gripper": round(float(q[5]), 1),
                "load_nm": round(load, 3)}

    # ---- kinematics, through the mirror -----------------------------------------------------
    def tip(self):
        return self.mirror.tip()

    def predicted_mm(self, table_z=0.0, *, objects=()):
        return self.mirror.predicted_mm(table_z, objects=objects) if self.kinematics_ok else None

    def solve(self, xyz, *, seed=None, iters=200):
        if not self.use_ik:
            raise ValueError("IK is off on the real arm until real_calibration.json is measured")
        out = np.array(self.mirror.solve(xyz, seed=None if seed is None else
                                         (self.sign * np.asarray(seed, dtype=float) + self.offset).tolist(), iters=iters))
        return ((out - self.offset) * self.sign).tolist()

    # ---- sim-only hooks, refused politely ----------------------------------------------------
    def inject(self, fault, **params):
        raise ValueError("faults can't be planted on the real arm from software: plant them on the pod "
                         "(Olir's bad images) or by hand")

    def clear(self, fault=None):
        return None

    def distance(self):
        return self.d_rows[-1] if self.d_rows else {"mm": None, "us": None, "st": "no_data"}
