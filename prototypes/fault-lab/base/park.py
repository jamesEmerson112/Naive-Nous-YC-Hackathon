"""Park the arm: send every servo that answers to its rest pose, then turn torque off.

Use only for hardware faults (a servo that doesn't answer at all). Run: python3 park.py
"""
import json
import time

import hw

bus = hw.Bus()
answered, silent = [], []
for servo in range(1, 7):
    try:
        bus.write(servo, 0.0)
        answered.append(servo)
    except hw.BusReadError:
        silent.append(servo)
bus.torque(False)
state = {"parked": True, "torque": False, "silent_servos": silent, "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
with open("arm_state.json", "w") as f:
    json.dump(state, f)
print(f"Parked: servos {answered} at rest, torque off. Not answering: {silent or 'none'}.")
