"""Robot app: hold an object, and lift it clear while something is close. See SPEC.md.

Run: python3 app.py traces/<trace>.json  (prints the arm's command log as JSON)
"""
import json
import sys

import hw

GRIPPER = 6
SHOULDER = 2
CLOSED = 0.0


def load_config(path="config.toml"):
    """Read `key = number` lines (a small subset of TOML)."""
    raw = {}
    with open(path) as f:
        for line in f:
            line = line.split("#", 1)[0].strip()
            if line:
                key, value = (part.strip() for part in line.split("=", 1))
                raw[key] = float(value)
    return {
        "near_cm": raw["near_cm"],
        "min_valid_cm": raw["min_valid_cm"],
        "max_valid_cm": raw["max_valid_cm"],
        "lift_deg": raw["lift_deg"],
        "rest_deg": raw["rest_deg"],
        "echo_polls": int(raw["echo_polls"]),
    }


def with_retries(fn, *args, tries=5):
    """Servo reads can time out now and then; try again before giving up."""
    for attempt in range(tries):
        try:
            return fn(*args)
        except hw.BusReadError:
            if attempt == tries - 1:
                raise


def read_distance(sensor, cfg):
    """One sensor reading. Wait a bounded number of polls for the echo; None if it never comes."""
    for _ in range(cfg["echo_polls"]):
        if sensor.echo_ready():
            return sensor.read_cm()
    sensor.skip()
    return None


def valid(d, cfg):
    return d is not None and cfg["min_valid_cm"] <= d <= cfg["max_valid_cm"]


def run(trace, bus=None):
    cfg = load_config()
    bus = bus or hw.Bus()
    sensor = hw.Sensor(trace)
    bus.torque(True)
    with_retries(bus.read, GRIPPER)  # make sure the gripper answers before we hold anything
    bus.write(GRIPPER, CLOSED)  # holding the object
    last = cfg["max_valid_cm"]  # nothing close yet
    lifted = False
    while not sensor.done():
        d = read_distance(sensor, cfg)
        if valid(d, cfg):
            last = d
        want_lift = last < cfg["near_cm"]
        if want_lift != lifted:
            bus.write(SHOULDER, cfg["lift_deg"] if want_lift else cfg["rest_deg"])
            lifted = want_lift
        with_retries(bus.read, SHOULDER)  # confirm the shoulder position
    return bus.log


if __name__ == "__main__":
    with open(sys.argv[1]) as f:
        print(json.dumps(run(json.load(f))))
