"""python -m armsim <command>

  poses   write library/poses.json (solved for the block in the scene) and library/wave.json
  demo    prototype 1: home -> wave -> home, with a run record and a GIF in runs/
  view    the same moves in MuJoCo's viewer window (on macOS, run it with mjpython)
"""
import sys
from pathlib import Path

from . import prims
from .arm import JOINTS, SimArm
from .record import RunRecorder

RUNS = Path(__file__).resolve().parent.parent / "runs"


def cmd_poses():
    arm = SimArm()
    p = prims.make_poses(arm)
    prims.save({"units": "degrees; gripper 0-100 (0 = closed)", "joints": list(JOINTS), "poses": p}, "poses")
    prims.save(prims.make_wave(p["present"]), "wave")
    for name, pose in p.items():
        print(f"{name:12s} {pose}")


def wave_routine(arm):
    p = prims.poses()
    steps = [("home", arm.goto(p["home"])), ("wave", arm.play(prims.load("wave"))), ("home", arm.goto(p["home"]))]
    return steps


def cmd_demo():
    from .render import Gif
    rec = RunRecorder(RUNS, task="wave, then go home", joints=JOINTS)
    arm = SimArm(recorder=rec, start=prims.REST)
    gif = Gif(arm, RUNS / f"{rec.run_id}.gif")
    steps = wave_routine(arm)
    ok = all(r["arrived"] for _, r in steps)
    for name, r in steps:
        print(f"{name:5s} {r}")
    print("distance now:", arm.distance(), " grasp:", arm.grasp_check(), " status:", arm.status())
    path = rec.close("ok" if ok else "did_not_arrive")
    print("run record:", path, rec.rows)
    print("gif:", gif.save())


def cmd_view():
    import mujoco.viewer
    arm = SimArm(start=prims.REST)
    with mujoco.viewer.launch_passive(arm.m, arm.d) as v:
        arm.hooks.append(lambda a: v.sync())
        while v.is_running():
            wave_routine(arm)


if __name__ == "__main__":
    {"poses": cmd_poses, "demo": cmd_demo, "view": cmd_view}.get(
        sys.argv[1] if len(sys.argv) > 1 else "", lambda: print(__doc__))()
