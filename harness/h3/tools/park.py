#!/usr/bin/env python3
"""Park the arm (torque off). Use it for hardware faults instead of editing code."""
import json
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path

WORK = Path(__file__).resolve().parents[2]  # work/harness/tools/park.py -> work/
DEVICE = WORK / "device"


def main():
    try:
        sys.path.insert(0, str(DEVICE))
        import hal

        cfg = tomllib.loads((DEVICE / "config.toml").read_text())
        arm = hal.make_arm(cfg)
        how = f"the device hal (backend={cfg['app']['backend']})"
    except Exception as e:  # a broken config or hal must not stop a park
        print(f"the device hal is unusable ({e!r}); parking the fake arm in ../sim directly")
        sys.path.insert(0, str(WORK / "sim"))
        from fake_arm import FakeArm

        arm = FakeArm(WORK / "sim")
        how = "the sim FakeArm"
    arm.park()
    outbox = WORK / "outbox"
    outbox.mkdir(exist_ok=True)
    parked = {"how": how, "parked_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    (outbox / "parked.json").write_text(json.dumps(parked) + "\n")  # report.py --action park checks for this
    print(f"PARKED: torque off through {how}")


if __name__ == "__main__":
    main()
