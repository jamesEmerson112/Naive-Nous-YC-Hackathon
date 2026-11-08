"""Hardware access.

backend = "real": LeRobot's SO101Follower and gpiozero's DistanceSensor (Pi 5 only).
backend = "mock": the fake arm and sensor in ../sim, or wherever $EDGE_SIM_DIR points.
"""
import os
import sys
from pathlib import Path


def sim_dir():
    return Path(os.environ.get("EDGE_SIM_DIR", Path(__file__).resolve().parent.parent / "sim"))


def make_arm(cfg):
    if cfg["app"]["backend"] == "real":
        return RealArm(cfg)
    sys.path.insert(0, str(sim_dir()))
    from fake_arm import FakeArm

    return FakeArm(sim_dir(), port=cfg["arm"]["port"])


def make_sensor(cfg):
    if cfg["app"]["backend"] == "real":
        return RealSensor(cfg)
    sys.path.insert(0, str(sim_dir()))
    from fake_sensor import TraceSensor

    return TraceSensor(sim_dir())


class RealArm:
    """SO-101 follower through LeRobot v0.7 (calibrate it once with lerobot-calibrate first)."""

    def __init__(self, cfg):
        from lerobot.robots.so_follower import SO101Follower, SO101FollowerConfig

        a = cfg["arm"]
        self.robot = SO101Follower(SO101FollowerConfig(port=a["port"], id=a["id"], use_degrees=True))

    def connect(self):
        self.robot.connect()

    def get_observation(self):
        return self.robot.get_observation()

    def send_action(self, action):
        return self.robot.send_action(action)

    def park(self):
        """Torque off every servo that answers. Untested on the real arm yet."""
        bus = self.robot.bus
        if not bus.is_connected:
            bus.connect(handshake=False)  # the handshake fails when a servo is missing
        for motor in bus.motors:
            try:
                bus.disable_torque(motor)
            except Exception as e:  # best effort: a missing servo can't be torqued off
                print(f"E code=PARK_TORQUE_OFF_FAILED motor={motor} err={e!r}", flush=True)

    def disconnect(self):
        self.robot.disconnect()


class RealSensor:
    """HC-SR04 through gpiozero with the lgpio pin factory (the Pi 5 needs lgpio)."""

    def __init__(self, cfg):
        os.environ.setdefault("GPIOZERO_PIN_FACTORY", "lgpio")
        from gpiozero import DistanceSensor

        s = cfg["sensor"]
        self.max_m = s["max_valid_cm"] / 100.0
        self.dev = DistanceSensor(echo=s["echo_pin"], trigger=s["trigger_pin"], max_distance=self.max_m)

    def read_cm(self):
        d = self.dev.distance * 100.0
        return None if d >= self.max_m * 100.0 - 0.5 else d  # gpiozero reports max_distance when there's no echo
