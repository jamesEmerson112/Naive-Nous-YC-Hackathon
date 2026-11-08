"""Record movements by moving the arm by hand (one arm, no teleoperation).

    python -m prims.record wave pick place      # record three primitives in a row
    python -m prims.record --pose home rest     # snapshot named poses instead

The arm's torque is switched off so it can be moved freely: support it before starting.
Press Enter to start a take, Enter again to stop and save it. Type 'r' + Enter while
recording to discard and redo the take. Ctrl-C quits (an unsaved take is lost).
"""

from __future__ import annotations

import argparse
import queue
import sys
import threading
import time

from .core import JOINTS, Primitive, connect_calibrated, make_arm, save_pose, strip_pos, to_vec


def _stdin_lines() -> queue.Queue[str]:
    q: queue.Queue[str] = queue.Queue()

    def pump() -> None:
        for line in sys.stdin:
            q.put(line.strip().lower())

    threading.Thread(target=pump, daemon=True).start()
    return q


_SHORT = {"shoulder_pan": "pan", "shoulder_lift": "lift", "elbow_flex": "elb",
          "wrist_flex": "wfl", "wrist_roll": "wrl", "gripper": "grip"}


def _fmt(pose: dict[str, float]) -> str:
    return " ".join(f"{_SHORT[j]}{pose[j]:5.0f}" for j in JOINTS)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("names", nargs="+", help="names to record, in order")
    ap.add_argument("--pose", action="store_true", help="save single poses instead of trajectories")
    ap.add_argument("--fps", type=int, default=50)
    ap.add_argument("--max-seconds", type=float, default=120.0, help="auto-stop and save a take after this long")
    ap.add_argument("--no-trim", action="store_true", help="keep idle frames at start/end")
    args = ap.parse_args()

    arm = make_arm()
    connect_calibrated(arm)
    arm.bus.disable_torque()
    print("Torque is OFF. Support the arm and move it by hand.")

    keys = _stdin_lines()
    period = 1.0 / args.fps
    todo = list(args.names)
    frames: list[list[float]] = []
    recording = False
    t_start = 0.0
    last_print = 0.0

    def prompt() -> None:
        what = "pose" if args.pose else "primitive"
        print(f"\n>>> Next {what}: '{todo[0]}'. Press Enter to {'save' if args.pose else 'start'}.")

    prompt()
    try:
        while todo:
            tick = time.perf_counter()
            current = strip_pos(arm.get_observation())
            if recording:
                frames.append(to_vec(current))

            try:
                key = keys.get_nowait()
            except queue.Empty:
                key = None

            if args.pose and key is not None:
                save_pose(todo[0], current)
                print(f"\nSaved pose '{todo[0]}': {_fmt(current)}")
                todo.pop(0)
                if todo:
                    prompt()
            elif key is not None and not recording:
                frames, recording, t_start = [], True, tick
                print(f"\n● REC '{todo[0]}'  (Enter = stop & save, r + Enter = redo)")
            elif recording and key == "r":
                recording = False
                print("\nDiscarded. Press Enter to try again.")
            elif recording and (key is not None or tick - t_start >= args.max_seconds):
                recording = False
                meta = {"recorded_at": time.strftime("%Y-%m-%d %H:%M:%S"), "source": "kinesthetic"}
                prim = Primitive(todo[0], args.fps, frames, meta=meta)
                if not args.no_trim:
                    prim = prim.trimmed()
                path = prim.save()
                print(f"\nSaved '{prim.name}': {prim.duration:.1f}s, {len(prim.frames)} frames -> {path}")
                todo.pop(0)
                if todo:
                    prompt()

            if tick - last_print > 0.25 and todo:
                state = f"REC {tick - t_start:4.1f}s" if recording else "Enter=rec"
                print(f"\r\033[K[{state}] {_fmt(current)}", end="", flush=True)
                last_print = tick
            dt = period - (time.perf_counter() - tick)
            if dt > 0:
                time.sleep(dt)
    except KeyboardInterrupt:
        print("\nStopped." + (" The take in progress was NOT saved." if recording else ""))
    finally:
        arm.disconnect()
    print("Done.")


if __name__ == "__main__":
    main()
