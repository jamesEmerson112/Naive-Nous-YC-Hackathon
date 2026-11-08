# servo-bus: errors from the SO-101's servo bus (Feetech STS3215 servos, IDs 1-6)

Tell a flaky bus (the software must cope) from a hardware fault (park the arm):

- Flaky bus, a software fault: one read fails in the middle of a run that was working ("Failed to sync read ... There is no status packet!" after many good steps). SPEC.md rule 5 says every servo read is retried `read_retries` times. Check that app.py reads the arm through `with_retries(...)`. If a read bypasses it, put the retry back, then run `python3 ../harness/tools/health.py`.
- Hardware fault: the arm fails at startup ("motor check failed", "Missing motor IDs: N"), or a read fails on every retry. No code change brings back a servo that doesn't answer. SPEC.md rule 6: stop, park and report. Don't edit any file, don't catch or hide the error, and don't edit ../sim. Run `python3 ../harness/tools/park.py`, then report with `--cause hardware --action park`.

Servo IDs: 1 shoulder_pan, 2 shoulder_lift, 3 elbow_flex, 4 wrist_flex, 5 wrist_roll, 6 gripper.
