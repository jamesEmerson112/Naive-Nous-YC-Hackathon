"""Motion primitives for the SO-101 arm.

A *primitive* is a named joint trajectory recorded by moving the arm by hand, saved as JSON in
``prims/library/<name>.json``. A *pose* is a single named joint configuration saved in
``prims/library/poses.json``. The ``Arm`` class plays them back and lets you chain them:

    from prims import Arm

    with Arm() as arm:
        arm.goto("home")
        arm.play("wave", speed=1.2)
        arm.close_gripper()
        arm.play("present")
        arm.goto("home")

Units follow LeRobot 0.7 with ``use_degrees=True``: joints are in degrees, the gripper
is 0 (closed) to 100 (open).
"""

from __future__ import annotations

import json
import math
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

ARM_DIR = Path(__file__).resolve().parent.parent
LIBRARY = Path(__file__).resolve().parent / "library"
POSES_FILE = LIBRARY / "poses.json"

JOINTS = ["shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper"]

Pose = dict[str, float]  # {"shoulder_pan": deg, ..., "gripper": 0..100}


# --------------------------------------------------------------------------- config


def load_env(path: Path = ARM_DIR / "arm.env") -> dict[str, str]:
    """Read arm.env (KEY=VALUE lines) so Python and the bin/ scripts share one config."""
    env: dict[str, str] = {}
    if path.is_file():
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            env[key.strip()] = val.strip().strip('"').strip("'")
    return env


CALIBRATION_DIR = ARM_DIR / "calibration"  # checked in, so every machine uses the same calibration


def _port_ok(port: str | None) -> bool:
    return bool(port) and "XXXX" not in port and Path(port).exists()


def make_arm(env: dict[str, str] | None = None):
    """The SO-101 arm we record with and play back on (LeRobot's follower driver)."""
    from lerobot.robots.so_follower import SO101Follower, SO101FollowerConfig

    env = env or load_env()
    port = env.get("ARM_PORT") or env.get("FOLLOWER_PORT")
    if not _port_ok(port):
        raise SystemExit(
            f"ARM_PORT={port!r} not found. Plug in the arm (USB + 12 V), list ports with "
            "`ls /dev/tty.usbmodem*`, and set ARM_PORT in arm/arm.env."
        )
    mrt = env.get("MAX_RELATIVE_TARGET") or None
    cfg = SO101FollowerConfig(
        port=port,
        id=env.get("ARM_ID") or env.get("FOLLOWER_ID") or "naive_follower",
        calibration_dir=CALIBRATION_DIR,
        max_relative_target=float(mrt) if mrt else None,
    )
    return SO101Follower(cfg)


def connect_calibrated(arm) -> None:
    """Connect without LeRobot's interactive calibration, using the checked-in calibration file.

    If the motors hold a different calibration than the file, the file is written to them
    (torque off), which is what LeRobot does when you keep an existing calibration file.
    """
    if not arm.calibration:
        raise SystemExit(f"No calibration file at {arm.calibration_fpath}.")
    arm.connect(calibrate=False)
    if not arm.is_calibrated:
        arm.bus.disable_torque()
        arm.bus.write_calibration(arm.calibration)
        print(f"Wrote {arm.calibration_fpath.name} to the motors.")


# --------------------------------------------------------------------------- data


def to_vec(pose: Pose) -> list[float]:
    return [float(pose[j]) for j in JOINTS]


def to_pose(vec: Iterable[float]) -> Pose:
    return dict(zip(JOINTS, (float(v) for v in vec), strict=True))


def strip_pos(d: dict[str, float]) -> Pose:
    """{"shoulder_pan.pos": x, ...} -> {"shoulder_pan": x, ...}"""
    return {k.removesuffix(".pos"): float(v) for k, v in d.items() if k.endswith(".pos")}


def add_pos(pose: Pose) -> dict[str, float]:
    return {f"{j}.pos": float(pose[j]) for j in JOINTS}


@dataclass
class Primitive:
    name: str
    fps: int
    frames: list[list[float]]  # one row per tick, columns in JOINTS order
    joints: list[str] = field(default_factory=lambda: list(JOINTS))
    meta: dict = field(default_factory=dict)

    @property
    def duration(self) -> float:
        return max(len(self.frames) - 1, 0) / self.fps

    @property
    def start(self) -> Pose:
        return to_pose(self.frames[0])

    @property
    def end(self) -> Pose:
        return to_pose(self.frames[-1])

    def at(self, t: float) -> Pose:
        """Joint pose at time t (seconds), linearly interpolated between recorded frames."""
        x = min(max(t * self.fps, 0.0), len(self.frames) - 1)
        i = int(math.floor(x))
        if i >= len(self.frames) - 1:
            return to_pose(self.frames[-1])
        a = x - i
        return to_pose(p + (q - p) * a for p, q in zip(self.frames[i], self.frames[i + 1], strict=True))

    def trimmed(self, still_deg: float = 1.0) -> Primitive:
        """Drop the idle frames before the first and after the last real movement."""
        if len(self.frames) < 3:
            return self

        def moved(i: int, ref: list[float]) -> bool:
            return max(abs(a - b) for a, b in zip(self.frames[i], ref, strict=True)) > still_deg

        first, last = self.frames[0], self.frames[-1]
        lo = next((i for i in range(len(self.frames)) if moved(i, first)), 0)
        hi = next((i for i in reversed(range(len(self.frames))) if moved(i, last)), len(self.frames) - 1)
        lo, hi = max(lo - 1, 0), min(hi + 1, len(self.frames) - 1)
        if hi <= lo:
            return self
        return Primitive(self.name, self.fps, self.frames[lo : hi + 1], self.joints, self.meta)

    def reversed(self) -> Primitive:
        return Primitive(f"{self.name}_reversed", self.fps, self.frames[::-1], self.joints, self.meta)

    # -- storage
    def save(self, path: Path | None = None) -> Path:
        path = path or LIBRARY / f"{self.name}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "name": self.name,
            "fps": self.fps,
            "joints": self.joints,
            "duration_s": round(self.duration, 3),
            "meta": self.meta,
            "frames": [[round(v, 3) for v in row] for row in self.frames],
        }
        path.write_text(json.dumps(data, indent=None, separators=(",", ":")) + "\n")
        return path

    @classmethod
    def load(cls, name_or_path: str | Path) -> Primitive:
        p = Path(name_or_path)
        if not p.suffix:
            p = LIBRARY / f"{name_or_path}.json"
        if not p.is_file():
            raise FileNotFoundError(f"No primitive {name_or_path!r}. Known: {', '.join(list_primitives()) or 'none'}")
        d = json.loads(p.read_text())
        if d["joints"] != JOINTS:  # reorder columns if needed
            idx = [d["joints"].index(j) for j in JOINTS]
            d["frames"] = [[row[i] for i in idx] for row in d["frames"]]
        return cls(d["name"], int(d["fps"]), d["frames"], list(JOINTS), d.get("meta", {}))


def list_primitives() -> list[str]:
    return sorted(p.stem for p in LIBRARY.glob("*.json") if p.name != "poses.json")


def load_poses() -> dict[str, Pose]:
    return json.loads(POSES_FILE.read_text()) if POSES_FILE.is_file() else {}


def save_pose(name: str, pose: Pose) -> None:
    poses = load_poses()
    poses[name] = {j: round(float(pose[j]), 2) for j in JOINTS}
    POSES_FILE.parent.mkdir(parents=True, exist_ok=True)
    POSES_FILE.write_text(json.dumps(poses, indent=2) + "\n")


def _smoothstep(a: float) -> float:
    return a * a * (3 - 2 * a)  # zero velocity at both ends


# --------------------------------------------------------------------------- arm


class Arm:
    """Programmatic control of the arm with recorded primitives and poses.

    Every move is interpolated from where the arm actually is, so chaining primitives
    never jumps: ``play`` first eases to the primitive's start pose, then streams it.
    """

    def __init__(self, fps: int = 50, max_speed_dps: float = 90.0, env: dict[str, str] | None = None):
        self.fps = fps
        self.max_speed_dps = max_speed_dps  # used to time goto() moves
        self.robot = make_arm(env)
        self._last: Pose | None = None

    # -- lifecycle
    def connect(self) -> Arm:
        _goal_to_present(self.robot)  # so enabling torque holds still instead of jumping to a stale goal
        connect_calibrated(self.robot)
        self._last = self.read()
        return self

    def disconnect(self) -> None:
        if self.robot.is_connected:
            self.robot.disconnect()  # disables torque (disable_torque_on_disconnect=True)

    def __enter__(self) -> Arm:
        return self.connect()

    def __exit__(self, *exc) -> None:
        self.disconnect()

    # -- low level
    def read(self) -> Pose:
        return strip_pos(self.robot.get_observation())

    def send(self, pose: Pose) -> None:
        full = {**(self._last or self.read()), **pose}
        self.robot.send_action(add_pos(full))
        self._last = full

    def _resolve(self, target: str | Pose) -> Pose:
        if isinstance(target, str):
            poses = load_poses()
            if target in poses:
                return poses[target]
            if target in list_primitives():
                return Primitive.load(target).start
            raise KeyError(f"Unknown pose {target!r}. Poses: {', '.join(poses) or 'none'}")
        return target

    # -- motions
    def goto(self, target: str | Pose, duration: float | None = None) -> None:
        """Ease to a pose (by name or dict). Unspecified joints stay where they are."""
        start = self._last or self.read()
        goal = {**start, **self._resolve(target)}
        if duration is None:
            biggest = max(abs(goal[j] - start[j]) for j in JOINTS if j != "gripper")
            duration = max(0.4, biggest / self.max_speed_dps)
        steps = max(1, int(duration * self.fps))
        t0 = time.perf_counter()
        for k in range(1, steps + 1):
            a = _smoothstep(k / steps)
            self.send({j: start[j] + (goal[j] - start[j]) * a for j in JOINTS})
            _sleep_until(t0 + k / self.fps)

    def play(self, prim: str | Primitive, speed: float = 1.0, approach: bool = True) -> None:
        """Play a recorded primitive. speed=2.0 is twice as fast."""
        p = prim if isinstance(prim, Primitive) else Primitive.load(prim)
        if approach:
            self.goto(p.start)
        total = p.duration / speed
        t0 = time.perf_counter()
        while True:
            t = time.perf_counter() - t0
            if t >= total:
                break
            self.send(p.at(t * speed))
            _sleep_until(t0 + (math.floor(t * self.fps) + 1) / self.fps)
        self.send(p.end)

    def gripper(self, percent: float, duration: float = 0.5) -> None:
        self.goto({"gripper": percent}, duration)

    def open_gripper(self) -> None:
        self.gripper(100)

    def close_gripper(self) -> None:
        self.gripper(0)

    def wait(self, seconds: float) -> None:
        """Hold the current pose (torque on)."""
        time.sleep(seconds)

    def relax(self) -> None:
        """Torque off: the arm goes limp. Support it first!"""
        self.robot.bus.disable_torque()

    def stiffen(self) -> None:
        self._last = self.read()
        self.send(self._last)
        self.robot.bus.enable_torque()

    def remember(self, name: str) -> Pose:
        """Save the arm's current pose under a name (e.g. move it by hand after relax())."""
        pose = self.read()
        save_pose(name, pose)
        return pose

    def run(self, steps: Iterable[str | tuple | Callable[[Arm], None]]) -> None:
        """Run a program, e.g. ["home", ("play", "wave", 1.5), ("gripper", 0), ("wait", 1)].

        A bare string plays the primitive of that name, or goes to the pose of that name.
        """
        for step in steps:
            if callable(step):
                step(self)
            elif isinstance(step, str):
                if step in list_primitives():
                    self.play(step)
                else:
                    self.goto(step)
            else:
                op, *args = step
                getattr(self, op)(*args)


def _goal_to_present(robot) -> None:
    """With torque off, set every motor's Goal_Position to where it is now (raw ticks).

    A servo keeps its last goal while limp (after kinesthetic recording or calibration), and
    LeRobot's connect() re-enables torque, which would snap the arm back to that old goal.
    """
    bus = robot.bus
    bus.connect(handshake=False)
    try:
        bus.disable_torque()
        bus.sync_write("Goal_Position", bus.sync_read("Present_Position", normalize=False), normalize=False)
    finally:
        bus.disconnect(disable_torque=False)


def _sleep_until(deadline: float) -> None:
    dt = deadline - time.perf_counter()
    if dt > 0:
        time.sleep(dt)
