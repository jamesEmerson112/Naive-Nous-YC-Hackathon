# What the robot must do

The arm holds an object, and the ultrasonic sensor drives it.

1. **Hold.** The gripper holds the object at `gripper_closed`. Nothing the distance sensor reports may open the gripper.
2. **Lift away.** When something comes close, the arm lifts the object away:
   - at or below `near_cm`: shoulder lift raised by the full `lift_max_deg`;
   - at or above `far_cm`: the normal hold pose;
   - in between: a smooth (cosine) blend.
3. **Bad readings.** The HC-SR04 is valid from 2 to 400 cm. A reading outside that range, or no reading at all (no echo), is invalid. Ignore it and keep using the last valid distance; with no valid reading yet, act as if the area is clear.
4. **Never block.** The control loop runs at `loop_hz`. No step may wait on the sensor or the arm for long. A missing reading is invalid, not a reason to wait.
5. **Flaky servo bus.** A failed servo read is retried (`read_retries`) before giving up.
6. **Hardware faults.** If a servo is missing or the arm can't be reached, stop. Park the arm and report it. Don't paper over hardware with code.

## Logging contract
- `BOOT ...` once at start, `EXIT ok steps=N` on a clean stop.
- `T step=... dist_cm=... valid=... lift=... gripper=... state=...` every `log_every` steps.
- `S <old>-><new> ...` on every state change.
- `E code=<CODE> ...` on every error.
- Every step: one JSON line in `logs/telemetry.jsonl`, and the heartbeat in `run/heartbeat`.
