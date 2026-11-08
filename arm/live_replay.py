#!/usr/bin/env python3
"""Same as safe_replay.py, but the ultrasonic sensor is watched for the WHOLE run, not just before it.

  1. Pre-check: refuse to start if anything is closer than --min-mm (same as safe_replay.py).
  2. Ramp in, play the recording, ramp back. Before every step, the last --live-window seconds
     (default 0.5 s) of sensor data must hold at least one sample, all of them clear. If something
     comes closer than --min-mm, or the sensor goes quiet or reports no_echo/busy, the arm holds where
     it is and the script stops (exit code 3). After you press Enter, torque is released.

This is safe_replay.py with --live-check always on. All of its flags work here too:
  ~/lerobot-venv/bin/python arm/live_replay.py --dry-run
  ~/lerobot-venv/bin/python arm/live_replay.py --arm-port /dev/serial/by-id/<adapter> --min-mm 150

Note: the HC-SR04 can't tell the arm from an intruder. If the motion swings the arm into the sensor's
cone (~15 degrees), this script stops itself. Aim the sensor past the arm's sweep, or lower --min-mm.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import safe_replay  # noqa: E402

if __name__ == "__main__":
    if "--live-check" not in sys.argv:
        sys.argv.insert(1, "--live-check")
    safe_replay.__doc__ = __doc__          # so --help describes this script
    sys.exit(safe_replay.main())
