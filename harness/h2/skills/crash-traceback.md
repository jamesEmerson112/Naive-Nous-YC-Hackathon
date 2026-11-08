# crash-traceback: the app crashed with a Python traceback (tier 1 crash)

1. Read the traceback at the end of the log tail. The last line is the error. The `File "...", line N` lines above it say where it happened; the last one that names a file in this directory is usually the place to look.
2. Look at that code: `grep -n "the_name" *.py config.toml`, or `sed -n '30,50p' control.py`.
3. Common causes:
   - KeyError naming a config key (like `KeyError: 'near_cm'`): config.toml spells that key differently or lacks it. Run `grep -n "near_c" *.py config.toml` with the start of the key, and fix the key in config.toml to the name the code reads. Don't add a default value in the code.
   - NameError, AttributeError: a name is spelled differently in two places. Fix the misspelled one.
   - TypeError that mentions None: a value that can be None (a missing sensor reading) reached math. Read sensor-readings.md.
   - ConnectionError or "There is no status packet" from a servo read in the middle of a run: read servo-bus.md before you change anything.
   - "Missing motor IDs" or "motor check failed" at startup: hardware. Read servo-bus.md.
4. Make the smallest fix, then run `python3 ../harness/tools/health.py`.
