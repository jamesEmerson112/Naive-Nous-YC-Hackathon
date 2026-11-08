#!/usr/bin/env python3
"""Replay a prerecorded SO-101 motion, but only start it if the ultrasonic sensor says the way is clear.

  1. Read the HC-SR04 on the sensor pod for a moment. If anything is closer than --min-mm, or the
     sensor isn't giving trustworthy readings, refuse to move.
  2. Move slowly from wherever the arm is to the first frame of the recording.
  3. Play the recording at its recorded rate. The sensor is NOT checked during the motion (the arm
     grabs things right in front of the sensor), unless --live-check is given: then, if something
     comes close (or the sensor goes quiet), the arm holds where it is and the script stops.
  4. Move back to the pose the arm started in.

Distance comes from podd (firmware/healer) if it is running, otherwise straight from the pod's serial
port. Either way the data is the pod's "D" lines: "D t=<ms> mm=<dist> us=<echo> st=<status>".

Run it with the LeRobot venv:  ~/lerobot-venv/bin/python arm/safe_replay.py --help
"""
import argparse
import collections
import glob
import json
import os
import select
import sys
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "firmware" / "healer"))
from healer import client, proto, serialport  # noqa: E402  (stdlib-only package in this repo)

MIN_ECHO_US = 116          # firmware/pod/src/hcsr04.h: echoes shorter than this (~2 cm) are "out_of_range"
DEFAULT_MOTION = Path(__file__).resolve().parent / "wave.json"


# --------------------------------------------------------------------------- ultrasonic

def classify(rec, min_mm):
    """'clear', 'blocked' or 'unknown' for one D record."""
    st, mm, us = rec.get("st"), rec.get("mm", 0), rec.get("us", 0)
    if st == "ok":
        return "clear" if mm >= min_mm else "blocked"
    if st == "out_of_range":
        # Too short an echo means something is closer than 2 cm. Too long (or none before the
        # 60 ms timeout) means nothing within 4 m.
        return "blocked" if 0 < us < MIN_ECHO_US else "clear"
    return "unknown"       # no_echo (wiring/power), busy (sensor still ringing): don't trust it


def pod_port():
    """The pod's serial port: the debug probe's VCOM. Not just any ttyACM, because the arm's servo
    bus adapter is a ttyACM too."""
    for link in sorted(glob.glob("/dev/serial/by-id/*")):
        if any(p.lower() in link.lower() for p in serialport.PROBE_PATTERNS):
            return os.path.realpath(link)
    return None


class Ultrasonic:
    """Collects D samples in a background thread. samples(): [(host_time, record), ...]."""

    def __init__(self, port=None, baud=115200):
        self._buf = collections.deque(maxlen=300)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self.error = None
        try:
            client.call("ping", timeout=1.0)
            self.source = "podd"
            target = self._run_podd
        except (client.PoddUnavailable, OSError):
            port = port or pod_port()
            if not port:
                raise SystemExit("No sensor pod found: podd isn't running and no debug-probe serial port "
                                 "is in /dev/serial/by-id. Pass --pod-port.")
            self.source = port
            self._fd = serialport.open_serial(port, baud)
            target = self._run_serial
        self._thread = threading.Thread(target=target, daemon=True)
        self._thread.start()

    def _add(self, ts, rec):
        with self._lock:
            self._buf.append((ts, rec))

    def _run_podd(self):
        last = 0.0
        while not self._stop.is_set():
            try:
                for r in client.call("recent", timeout=2.0, kind="D", seconds=2, limit=50)["rows"]:
                    if r["ts"] > last:
                        last = r["ts"]
                        self._add(r["ts"], r)
            except Exception as e:          # podd restarting: samples go stale and the checks fail safe
                self.error = str(e)
            self._stop.wait(0.05)

    def _run_serial(self):
        buf = b""
        while not self._stop.is_set():
            try:
                r, _, _ = select.select([self._fd], [], [], 0.1)
                if not r:
                    continue
                buf += os.read(self._fd, 4096)
            except OSError as e:            # probe unplugged: samples go stale and the checks fail safe
                self.error = str(e)
                return
            *lines, buf = buf.split(b"\n")
            for line in lines:
                rec = proto.parse_line(line.decode("ascii", "replace").strip())
                if rec and rec["type"] == "D":
                    self._add(time.time(), rec)

    def samples(self, seconds):
        t0 = time.time() - seconds
        with self._lock:
            return [(t, r) for t, r in self._buf if t >= t0]

    def close(self):
        self._stop.set()
        self._thread.join(timeout=1)
        if hasattr(self, "_fd"):
            os.close(self._fd)


def check_clear(sensor, min_mm, window_s, min_samples):
    """(ok, message) over the last window_s seconds. Every sample must be clear."""
    s = sensor.samples(window_s)
    if len(s) < min_samples:
        why = " (%s)" % sensor.error if sensor.error else ""
        return False, "only %d distance samples in %.1f s; is the pod streaming?%s" % (len(s), window_s, why)
    verdicts = [classify(r, min_mm) for _, r in s]
    near = [r["mm"] for (_, r), v in zip(s, verdicts) if v == "blocked"]
    if near:
        return False, "object at %d mm (limit %d mm)" % (min(near), min_mm)
    if "unknown" in verdicts:
        bad = [r["st"] for (_, r), v in zip(s, verdicts) if v == "unknown"]
        return False, "sensor not trustworthy: %s" % ", ".join(sorted(set(bad)))
    ok_mm = [r["mm"] for _, r in s if r["st"] == "ok"]
    nearest = "%d mm" % min(ok_mm) if ok_mm else "> 4 m"
    return True, "clear: %d samples, nearest %s" % (len(s), nearest)


def describe_samples(samples, n=5):
    """The last n D samples as the pod sent them, e.g. 'mm=812 us=4734 st=ok; ...'."""
    rows = [r for _, r in samples[-n:]]
    return "; ".join("mm=%s us=%s st=%s" % (r.get("mm"), r.get("us"), r.get("st")) for r in rows) or "none"


# --------------------------------------------------------------------------- arm

def load_motion(path):
    """Two formats: extract_motion.py output (joints named "shoulder_pan.pos", "units" given) and
    kinesthetic recordings like wave.json (bare joint names, degrees, "meta" block, no "units")."""
    m = json.loads(Path(path).read_text())
    joints = [j if j.endswith(".pos") else j + ".pos" for j in m["joints"]]   # LeRobot's action/obs keys
    source = m.get("source") or m.get("meta", {}).get("source", "") or m.get("name", "")
    return joints, m["fps"], m["frames"], source, m.get("units", "degrees")


def connect_arm(port, robot_id, max_step, units):
    from lerobot.robots.so_follower import SO101Follower, SO101FollowerConfig
    # The recording's units decide how LeRobot normalizes joint positions. "range_m100_100" maps
    # -100..100 onto each joint's calibrated range, so it adapts to this arm's own calibration.
    cfg = SO101FollowerConfig(port=port, id=robot_id, max_relative_target=max_step,
                              use_degrees=(units == "degrees"))
    robot = SO101Follower(cfg)
    robot.connect()      # runs the interactive calibration if this robot id hasn't been calibrated
    return robot


def read_pose(robot, joints):
    obs = robot.get_observation()
    return [float(obs[j]) for j in joints]


def send_pose(robot, joints, pose):
    robot.send_action({j: float(v) for j, v in zip(joints, pose)})


def lerp_path(a, b, steps):
    return [[x + (y - x) * (i / steps) for x, y in zip(a, b)] for i in range(1, steps + 1)]


class Halt(Exception):
    pass


def play(robot, joints, frames, fps, sensor, args, label):
    """Send frames at fps. With a sensor (--live-check), check it before every step."""
    period = 1.0 / fps
    t_next = time.monotonic()
    for i, pose in enumerate(frames):
        if sensor is not None:
            ok, msg = check_clear(sensor, args.min_mm, args.live_window, 1)
            if not ok:
                raise Halt("%s, step %d/%d: %s" % (label, i + 1, len(frames), msg))
        if robot is not None:
            send_pose(robot, joints, pose)
        t_next += period
        time.sleep(max(0.0, t_next - time.monotonic()))


# --------------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--arm-port", help="servo bus adapter, e.g. /dev/ttyACM1 (find it with lerobot-find-port)")
    ap.add_argument("--robot-id", default="romulus_arm", help="name the calibration is saved under")
    ap.add_argument("--pod-port", help="sensor pod serial port (default: podd, else the debug probe's VCOM)")
    ap.add_argument("--motion", default=str(DEFAULT_MOTION), help="motion JSON (see extract_motion.py)")
    ap.add_argument("--min-mm", type=int, default=200, help="refuse to move if anything is closer (mm)")
    ap.add_argument("--precheck-s", type=float, default=1.5, help="how long to watch the sensor before moving")
    ap.add_argument("--live-check", action="store_true",
                    help="also watch the sensor during the motion and stop if something comes close "
                         "(default: the sensor only gates the start)")
    ap.add_argument("--live-window", type=float, default=0.5,
                    help="with --live-check: stop if no clear sample arrived in this many seconds")
    ap.add_argument("--speed", type=float, default=1.0, help="playback speed factor (0.5 = half speed)")
    ap.add_argument("--ramp-s", type=float, default=3.0, help="seconds to move to the first frame and back")
    ap.add_argument("--max-step", type=float, default=8.0,
                    help="LeRobot max_relative_target: largest per-step joint change it will send "
                         "(in the motion's units)")
    ap.add_argument("--no-return", action="store_true", help="stay at the last frame instead of going back")
    ap.add_argument("--dry-run", action="store_true", help="no arm: sensor checks and timing only")
    ap.add_argument("--check-only", action="store_true",
                    help="run the start gate and stop: exit 0 if it passes, 2 if not (no arm, no motion)")
    args = ap.parse_args()
    if not (args.dry_run or args.check_only) and not args.arm_port:
        ap.error("--arm-port is required (or use --dry-run / --check-only)")

    joints, fps, frames, source, units = load_motion(args.motion)
    fps *= args.speed
    print("motion: %d frames at %.0f fps (%.1f s) from %s" % (len(frames), fps, len(frames) / fps, source))

    sensor = Ultrasonic(args.pod_port)
    print("ultrasonic: reading from %s for %.1f s ..." % (sensor.source, args.precheck_s))
    time.sleep(args.precheck_s)
    ok, msg = check_clear(sensor, args.min_mm, args.precheck_s, min_samples=max(3, int(args.precheck_s * 4)))
    print("ultrasonic:", msg)
    print("ultrasonic: last samples:", describe_samples(sensor.samples(args.precheck_s)))
    if not ok:
        print("NOT MOVING.")
        sensor.close()
        return 2
    if args.check_only:
        print("start gate passed (--check-only: no motion).")
        sensor.close()
        return 0
    if not args.live_check:
        sensor.close()          # start gate passed; the motion itself ignores the sensor
        sensor = None
        print("ultrasonic: start gate passed, not watching during the motion")

    robot = None
    start = frames[0]
    try:
        if not args.dry_run:
            robot = connect_arm(args.arm_port, args.robot_id, args.max_step, units)
            start = read_pose(robot, joints)
            print("arm: connected, start pose", [round(v, 1) for v in start])
        ramp = max(1, int(args.ramp_s * fps))
        print("arm: moving to first frame (%.1f s)" % args.ramp_s)
        play(robot, joints, lerp_path(start, frames[0], ramp), fps, sensor, args, "ramp in")
        print("arm: playing recording")
        play(robot, joints, frames, fps, sensor, args, "playback")
        if not args.no_return:
            print("arm: returning to start pose")
            play(robot, joints, lerp_path(frames[-1], start, ramp), fps, sensor, args, "ramp out")
        print("done.")
        return 0
    except (Halt, KeyboardInterrupt) as e:
        print("STOPPED:", e if isinstance(e, Halt) else "Ctrl-C")
        if robot is not None:
            here = read_pose(robot, joints)
            send_pose(robot, joints, here)       # hold where it is instead of finishing the move
            print("arm: holding at", [round(v, 1) for v in here])
            input("Move the obstacle / support the arm, then press Enter to release torque ...")
        return 3
    finally:
        if robot is not None:
            robot.disconnect()
        if sensor is not None:
            sensor.close()


if __name__ == "__main__":
    sys.exit(main())
