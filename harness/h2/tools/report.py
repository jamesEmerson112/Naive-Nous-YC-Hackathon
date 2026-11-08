#!/usr/bin/env python3
"""Finish the heal: check that the report matches the device, then write ../outbox/report.json.

  --action patch   you fixed it: health.py must pass (this runs it)
  --action park    a hardware fault: park.py must have run
  --action none    you couldn't fix it; the cloud takes over
A report that doesn't match is refused, and the heal goes on.
"""
import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
WORK = TOOLS.parents[1]  # work/harness/tools/ -> work/
DONE_MARKER = "HEALER_DONE"  # the healer's runner ends the run when report.py prints this line


def git(*args):
    try:
        out = subprocess.run(["git", *args], cwd=WORK / "device", capture_output=True, text=True, timeout=10)
        return out.stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def refuse(message):
    print(f"NOT REPORTED: {message}")
    sys.exit(1)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--cause", required=True, choices=["software", "hardware", "unknown"])
    p.add_argument("--action", required=True, choices=["patch", "park", "none"])
    p.add_argument("--summary", required=True, help="one or two sentences: what was wrong and what you did")
    args = p.parse_args()

    if args.action == "patch":
        health = subprocess.run([sys.executable, str(TOOLS / "health.py")], cwd=WORK / "device",
                                capture_output=True, text=True, timeout=300)
        if health.returncode != 0:
            print(health.stdout.rstrip())
            refuse("health.py still fails, so it isn't fixed. Keep fixing it, or report with "
                   "--cause unknown --action none if you can't.")
    elif args.action == "park" and not (WORK / "outbox" / "parked.json").exists():
        refuse("the arm isn't parked. Run python3 ../harness/tools/park.py first.")

    incident_file = WORK / "incident.json"
    incident = json.loads(incident_file.read_text()) if incident_file.exists() else {}
    changed = [line[3:] for line in git("status", "--porcelain").splitlines()]
    report = {
        "incident_id": incident.get("incident_id"),
        "cause": args.cause,
        "action": args.action,
        "summary": args.summary,
        "files_changed": changed,
        "diff": git("diff"),
        "reported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    outbox = WORK / "outbox"
    outbox.mkdir(exist_ok=True)
    (outbox / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(DONE_MARKER)
    print(f"REPORTED cause={args.cause} action={args.action} files_changed={changed}")


if __name__ == "__main__":
    main()
