# crash-traceback: the app crashed with a Python traceback (tier 1 crash)

1. Read the traceback at the end of the log tail. The last line is the error. The `File "...", line N` lines above it say where it happened; the last one that names a file in this directory is usually the place to look.
2. Look at that code: `grep -n "the_name" *.py config.toml`, or `sed -n '30,50p' control.py`.
3. Common causes:
   - KeyError, NameError, AttributeError: a name is spelled differently in two places. Compare the name in the error with config.toml and the code, and fix the one that is wrong (the odd one out).
   - TypeError that mentions None: a value that can be None (a missing sensor reading) reached math. Read sensor-readings.md.
   - ConnectionError or "There is no status packet" from a servo read in the middle of a run: read servo-bus.md before you change anything.
   - "Missing motor IDs" or "motor check failed" at startup: hardware. Read servo-bus.md.
4. Make the smallest fix, then run `python3 ../harness/tools/health.py`.
