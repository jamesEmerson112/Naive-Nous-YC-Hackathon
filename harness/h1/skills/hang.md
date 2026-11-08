# hang: the app stopped making progress (tier 2 hang or timeout)

1. Read stack_dump in ../incident.json. It shows where the app was when the supervisor stopped it, most recent call first. The first frame in a file of this directory is where it was stuck.
2. Look there for a loop that can wait forever: a `while` that waits for a value that may never come (a sensor reading that stays None), a retry with no limit, or a sleep inside a loop.
3. SPEC.md rule 4: a step must never block. A missing reading is invalid, not a reason to wait. Return None right away and let the controller ignore it.
4. Remove the waiting loop (one read per step), then run `python3 ../harness/tools/health.py`.
