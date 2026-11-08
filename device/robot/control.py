"""Pure control logic: distance in, arm action out.

No hardware access here, so it can be tested anywhere (fault-catalog rule 3).
"""
import math

JOINTS = ("shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll")


class Controller:
    def __init__(self, cfg):
        arm, sensor = cfg["arm"], cfg["sensor"]
        self.pose = dict(arm["hold_pose"])
        self.gripper_closed = float(arm["gripper_closed"])
        self.gripper_open = float(arm["gripper_open"])
        self.lift_max = float(arm["lift_max_deg"])
        self.min_cm = float(sensor["min_valid_cm"])
        self.max_cm = float(sensor["max_valid_cm"])
        self.near = float(sensor["near_cm"])
        self.far = float(sensor["far_cm"])
        self.state = "HOLDING"
        self.last_valid = None

    def is_valid(self, dist_cm):
        return dist_cm is not None and self.min_cm <= dist_cm <= self.max_cm

    def lift_for(self, dist_cm):
        """Extra shoulder lift: all of it at `near`, none at `far`, cosine blend between."""
        if dist_cm <= self.near:
            return self.lift_max
        if dist_cm >= self.far:
            return 0.0
        x = (dist_cm - self.near) / (self.far - self.near)  # 0 at near, 1 at far
        return self.lift_max * 0.5 * (1.0 + math.cos(math.pi * x))

    def step(self, dist_cm):
        """Return (action, info). Invalid readings are ignored; the last valid one is used."""
        valid = self.is_valid(dist_cm)
        if valid:
            self.last_valid = dist_cm
        d = self.last_valid if self.last_valid is not None else self.far

        lift = self.lift_for(d)
        action = {f"{j}.pos": float(self.pose[j]) for j in JOINTS}
        action["shoulder_lift.pos"] += lift
        action["gripper.pos"] = self.gripper_closed

        info = {"dist_cm": dist_cm, "valid": valid, "used_cm": d, "lift": round(lift, 2), "state": self.state}
        return action, info
