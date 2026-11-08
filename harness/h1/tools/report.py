#!/usr/bin/env python3
"""Write the healer's report to ../outbox/report.json; the cloud picks it up at the next check-in."""
import argparse
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

WORK = Path(__file__).resolve().parents[2]  # work/harness/tools/report.py -> work/


def git(*args):
    try:
        out = subprocess.run(["git", *args], cwd=WORK / "device", capture_output=True, text=True, timeout=10)
        return out.stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cause", required=True, choices=["software", "hardware", "unknown"])
    p.add_argument("--action", required=True, choices=["patch", "park", "none"])
    p.add_argument("--summary", required=True, help="one or two sentences: what was wrong and what you did")
    args = p.parse_args()

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
    print(f"REPORTED cause={args.cause} action={args.action} files_changed={changed}")


if __name__ == "__main__":
    main()
