# Robot app spec

The arm holds an object in its gripper and lifts it clear while something is close.

1. **Hold:** close the gripper (servo 6) at start and keep it closed for the whole run. The gripper must never open while holding.
2. **Lift:** while the last valid distance is under `near_cm`, raise the shoulder (servo 2) to `lift_deg`. Lower it back to `rest_deg` once the last valid distance is `near_cm` or more.
3. **Bad readings:** a reading is valid only if it is between `min_valid_cm` and `max_valid_cm`. Ignore anything else, and keep the last valid distance.
4. **Missing echoes:** the sensor's echo sometimes never comes back. Wait at most `echo_polls` polls, then treat that reading as invalid. Never wait forever.
5. **Bus glitches:** servo reads can time out now and then. Retry them before giving up.
6. **Hardware faults:** if a servo doesn't answer at all, it's hardware. The code can't fix it: park the arm (`python3 park.py`) and report it.
7. **Settings** live in `config.toml`. `hw.py` and `hardware.json` are the (fake) hardware; never edit them.
