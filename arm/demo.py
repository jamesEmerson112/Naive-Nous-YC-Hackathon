"""Example demo program built from primitives. Edit freely.

Record the pieces first (see README), e.g.:
    bin/prim-record.sh --pose home
    bin/prim-record.sh wave pick present
Then run:  bin/demo.sh
"""

from prims import Arm

with Arm() as arm:
    arm.goto("home")
    arm.play("wave", speed=1.2)
    arm.open_gripper()
    arm.play("pick")
    arm.close_gripper()
    arm.play("present")
    arm.wait(1.0)
    arm.goto("home")
