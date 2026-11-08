#!/usr/bin/env python3
"""Park the arm (torque off). Use it for hardware faults instead of editing code."""
import sys
import tomllib
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
    print(f"PARKED: torque off through {how}")


if __name__ == "__main__":
    main()
