"""Fake hardware: an SO-101 servo bus and an HC-SR04 distance sensor.

DO NOT EDIT. This file and hardware.json stand in for the real arm and sensor.
"""
import json
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(_HERE, "hardware.json")) as f:
    HW = json.load(f)


class BusReadError(Exception):
    """A servo did not answer on the bus."""


class Bus:
    """Feetech-style servo bus. Logs every command so a run can be compared with another."""

    def __init__(self):
        self.positions = {int(s): 0.0 for s in HW["servos"]}
        self.drops_left = int(HW.get("bus_drops", 0))  # transient read timeouts still to come
        self.log = []

    def read(self, servo):
        if servo not in self.positions:
            raise BusReadError(f"servo {servo} not responding (no status packet)")
        if self.drops_left > 0:
            self.drops_left -= 1
            raise BusReadError(f"read timeout on servo {servo}")
        return self.positions[servo]

    def write(self, servo, pos):
        if servo not in self.positions:
            raise BusReadError(f"servo {servo} not responding (no status packet)")
        self.positions[servo] = float(pos)
        self.log.append(["write", servo, float(pos)])

    def torque(self, on):
        self.log.append(["torque", bool(on)])


class Sensor:
    """Replays a recorded distance trace. A number is an echo at that distance in cm;
    null means the echo never comes back."""

    def __init__(self, trace):
        self.trace = list(trace)
        self.i = 0

    def done(self):
        return self.i >= len(self.trace)

    def echo_ready(self):
        return self.trace[self.i] is not None

    def read_cm(self):
        value = self.trace[self.i]
        self.i += 1
        return float(value)

    def skip(self):
        """Give up on the current reading (no echo)."""
        self.i += 1
