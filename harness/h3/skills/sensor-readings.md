# sensor-readings: the arm reacts to bad distance readings, or a missing (None) reading crashes the math

SPEC.md rule 3: the HC-SR04 is valid from 2 to 400 cm (min_valid_cm and max_valid_cm in config.toml). A reading outside that range, or None (no echo), is invalid. Ignore it and keep using the last valid distance. With no valid reading yet, act as if the area is clear (far_cm).

- In control.py, `is_valid` checks the range. `step` must use only valid readings: it sets `self.last_valid` only when the reading is valid, and falls back to `self.far` when there has been no valid reading yet.
- telemetry_tail in ../incident.json shows each step's dist_cm, valid and used_cm. valid must be false for None, 0, 1.5 or 500, and used_cm must never be one of those values.
- sensors.read_distance returns one reading per step and never waits (SPEC.md rule 4).

Fix whichever of these is broken, then run `python3 ../harness/tools/health.py`.
