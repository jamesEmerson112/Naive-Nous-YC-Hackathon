"""Play primitives and poses on the follower, or list what's recorded.

    python -m prims.play --list
    python -m prims.play home wave wave@1.5 present home   # name@speed sets playback speed
    python -m prims.play wave --loop 3

Each name is a recorded primitive or a saved pose. Moves are eased so nothing jumps.
Ctrl-C stops and disconnects (torque off, so support the arm).
"""

from __future__ import annotations

import argparse

from .core import Arm, Primitive, list_primitives, load_poses


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("steps", nargs="*")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--loop", type=int, default=1)
    ap.add_argument("--hold", type=float, default=0.0, help="seconds to hold at the end before torque off")
    args = ap.parse_args()

    if args.list or not args.steps:
        print("Primitives:")
        for n in list_primitives():
            p = Primitive.load(n)
            print(f"  {n:20s} {p.duration:5.1f}s  {len(p.frames)} frames @ {p.fps} fps")
        print("Poses:")
        for n in load_poses():
            print(f"  {n}")
        return

    try:
        with Arm() as arm:
            for _ in range(args.loop):
                for step in args.steps:
                    name, _, speed = step.partition("@")
                    print(f"-> {step}")
                    if name in list_primitives():
                        arm.play(name, speed=float(speed or 1))
                    else:
                        arm.goto(name)
            arm.wait(args.hold)
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
