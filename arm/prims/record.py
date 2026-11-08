"""Teleoperate the follower with the leader and record named primitives / poses.

    python -m prims.record wave pick place      # record three primitives in a row
    python -m prims.record --pose home rest     # snapshot named poses instead
    python -m prims.record --kinesthetic wave   # no leader: move the limp follower by hand
    python -m prims.record --leader-only wave   # follower stays still: move the leader by hand

Teleoperation runs the whole time. Press Enter to start a take, Enter again to stop and
save it. Type 'r' + Enter while recording to discard and redo the take. Ctrl-C quits.
"""

from __future__ import annotations

import argparse
import queue
import sys
import threading
import time

from .core import (
    JOINTS,
    Primitive,
    connect_calibrated,
    make_follower,
    make_leader,
    save_pose,
    strip_pos,
    to_vec,
)


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
    ap.add_argument("--kinesthetic", action="store_true", help="no leader arm; follower torque off, move it by hand")
    ap.add_argument("--leader-only", action="store_true", help="record the leader arm alone; the follower is not used")
    ap.add_argument("--fps", type=int, default=50)
    ap.add_argument("--max-seconds", type=float, default=60.0, help="auto-stop a take after this long")
    ap.add_argument("--no-trim", action="store_true", help="keep idle frames at start/end")
    args = ap.parse_args()

    if args.kinesthetic and args.leader_only:
        ap.error("--kinesthetic and --leader-only are mutually exclusive")
    follower = None if args.leader_only else make_follower()
    leader = None if args.kinesthetic else make_leader()
    if follower:
        connect_calibrated(follower, "follower")
    if leader:
        connect_calibrated(leader, "leader")
    if args.leader_only:
        print("Leader-only mode: the follower is not connected. Move the leader by hand.")
    elif not leader:
        follower.bus.disable_torque()
        print("Kinesthetic mode: follower torque is OFF. Support the arm and move it by hand.")

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
            if args.leader_only:
                current = strip_pos(leader.get_action())
            elif leader:
                sent = follower.send_action(leader.get_action())
                current = strip_pos(sent)  # what we commanded; replays most faithfully
            else:
                current = strip_pos(follower.get_observation())

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
            elif recording and (key == "r"):
                recording = False
                print("\nDiscarded. Press Enter to try again.")
            elif recording and (key is not None or tick - t_start >= args.max_seconds):
                recording = False
                prim = Primitive(todo[0], args.fps, frames, meta={
                    "recorded_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "source": "kinesthetic" if args.kinesthetic else
                              "so101_leader_only" if args.leader_only else "so101_leader",
                })
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
        print("\nStopped.")
    finally:
        if leader:
            leader.disconnect()
        if follower:
            follower.disconnect()
    print("Done.")


if __name__ == "__main__":
    main()
