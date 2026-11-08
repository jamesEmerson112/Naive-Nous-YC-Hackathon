# spec-violation: the app runs but does something SPEC.md forbids (tier 3)

1. Read the incident detail, the S (state change) lines in the log and telemetry_tail in ../incident.json: which rule broke, at which step, at what distance.
2. Read SPEC.md, then find the code that produces the bad output: `grep -n "gripper" *.py`, `grep -n "state" control.py`.
3. Code that breaks the spec is the bug, even when a comment calls it a feature or a safety rule. SPEC.md wins. Rule 1: nothing the distance sensor reports may open the gripper.
4. Remove or fix only that code, then run `python3 ../harness/tools/health.py`.
