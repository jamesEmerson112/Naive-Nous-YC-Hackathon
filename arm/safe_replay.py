#!/usr/bin/env python3
"""Replay a prerecorded SO-101 motion, but only start it if the ultrasonic sensor says the way is clear.

  1. Read the HC-SR04 on the sensor pod for a moment. If anything is closer than --min-mm, or the
     sensor isn't giving trustworthy readings, refuse to move.
  2. Move slowly from wherever the arm is to the first frame of the recording.
  3. Play the recording at its recorded rate. The sensor is NOT checked during the motion (the arm
     grabs things right in front of the sensor), unless --live-check is given: then, if something
     comes close (or the sensor goes quiet), the arm holds where it is and the script stops.
  4. Move back to the pose the arm started in.

With --on-blocked reflex, a blocked start gate doesn't just refuse: the arm runs its obstacle reflex, swinging
its base away from the reported obstacle (alternating sides with quick twitches, within +-20 deg by default,
never more than +-25 deg) while the wrist rolls and the gripper flinches, until the way is clear, then starts
the job; if it never clears within --reflex-s it returns to the start pose and exits 4. The shoulder, elbow
and wrist flex hold their pose throughout. Every run writes a record (gate samples, phases, reflex swings, commanded and measured
joint positions) to arm/runs/; arm/arm_status.py summarises it.

Exit codes: 0 the job ran, 2 refused to start, 3 stopped mid-motion, 4 the reflex gave up.

Distance comes from podd (firmware/healer) if it is running, otherwise straight from the pod's serial
port. Either way the data is the pod's "D" lines: "D t=<ms> mm=<dist> us=<echo> st=<status>".

Run it with the LeRobot venv:  ~/lerobot-venv/bin/python arm/safe_replay.py --help
"""
import argparse
import collections
import glob
import json
import logging
import math
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
RUNS_DIR = Path(os.environ.get("ARM_RUNS_DIR") or Path(__file__).resolve().parent / "runs")
CALIBRATION_DIR = Path.home() / ".cache" / "huggingface" / "lerobot" / "calibration" / "robots" / "so_follower"
REFLEX_AMP_MAX = 25        # degrees: hard cap on the reflex's base swing, whatever the flags say
REFLEX_SPEED_MAX = 60      # degrees/s, base
REFLEX_ROLL_MAX = 20       # degrees: wrist roll jitter around its start
REFLEX_ROLL_SPEED = 90     # degrees/s
REFLEX_GRIP_MAX = 25       # gripper flinch: at most 25 % open
REFLEX_GRIP_SPEED = 100    # % per second
REFLEX_HZ = 50
JOINT_LIMIT_DEG = 110


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


class ClampCounter(logging.Filter):
    """LeRobot logs a multi-line warning every time --max-step (max_relative_target) clamps a command. Count
    them instead, so the job's log stays readable; the count goes in the run record."""
    n = 0

    def filter(self, record):
        if "had to be clamped" in record.getMessage():
            ClampCounter.n += 1
            return False
        return True


def connect_arm(port, robot_id, max_step, units):
    logging.getLogger("lerobot.robots.utils").addFilter(ClampCounter())
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


# --------------------------------------------------------------------------- run record

class Recorder:
    """What the job did, for arm/arm_status.py and the healer's arm_report tool: the gate's verdict and samples,
    each phase, every reflex swing, and the arm's trajectory (commanded and measured joint positions, sampled
    together at about 10 Hz; every frame is counted)."""

    MEASURE_EVERY = 5          # frames: a bus read takes ~14 ms, so measuring every 50 fps frame would slow playback

    def __init__(self, args, joints, fps, n_frames, source):
        self.t0 = time.time()
        self.run_id = time.strftime("%Y%m%d-%H%M%S", time.localtime(self.t0)) + "-%03d" % (self.t0 % 1 * 1000)
        self.rec = {"run_id": self.run_id, "started": self.t0, "motion": Path(args.motion).name, "source": source,
                    "fps": fps, "joints": [j.removesuffix(".pos") for j in joints], "min_mm": args.min_mm,
                    "arm": args.arm_port or None, "mode": "check-only" if args.check_only else
                    ("dry-run" if args.dry_run else "arm"), "on_blocked": args.on_blocked,
                    "frames_planned": n_frames, "frames_sent": 0, "gate": None, "reflex": None,
                    "phases": [], "track": []}
        self.dir = Path(args.runs_dir) if args.runs_dir else RUNS_DIR
        self.enabled = not args.no_record
        self.n = 0

    def phase(self, name):
        self.rec["phases"].append({"name": name, "t0": round(time.time() - self.t0, 3), "t1": None, "frames": 0})

    def end_phase(self):
        if self.rec["phases"] and self.rec["phases"][-1]["t1"] is None:
            self.rec["phases"][-1]["t1"] = round(time.time() - self.t0, 3)

    def frame(self, robot, joints, cmd, playback=False):
        """Called after each command. Measures the arm every MEASURE_EVERY frames."""
        self.rec["phases"][-1]["frames"] += 1
        if playback:
            self.rec["frames_sent"] += 1
        meas = None
        if robot is not None and self.n % self.MEASURE_EVERY == 0:
            try:
                meas = [round(v, 2) for v in read_pose(robot, joints)]
            except Exception as e:          # a read glitch must not stop the arm mid-motion
                meas = "read error: %s" % e
        if meas is not None or robot is None and self.n % self.MEASURE_EVERY == 0:
            self.rec["track"].append([round(time.time() - self.t0, 3), self.rec["phases"][-1]["name"],
                                      [round(v, 2) for v in cmd], meas])
        self.n += 1

    def save(self, code, verdict):
        self.end_phase()
        self.rec |= {"ended": time.time(), "exit": code, "verdict": verdict, "clamped_commands": ClampCounter.n}
        if not self.enabled:
            return None
        self.dir.mkdir(parents=True, exist_ok=True)
        path = self.dir / ("%s.json" % self.run_id)
        path.write_text(json.dumps(self.rec, indent=1) + "\n")
        return path


# --------------------------------------------------------------------------- motion

def play(robot, joints, frames, fps, sensor, args, label, rec=None):
    """Send frames at fps. With a sensor (--live-check), check it before every step."""
    period = 1.0 / fps
    t_next = time.monotonic()
    if rec:
        rec.phase(label)
    for i, pose in enumerate(frames):
        if sensor is not None:
            ok, msg = check_clear(sensor, args.min_mm, args.live_window, 1)
            if not ok:
                raise Halt("%s, step %d/%d: %s" % (label, i + 1, len(frames), msg))
        if robot is not None:
            send_pose(robot, joints, pose)
        if rec:
            rec.frame(robot, joints, pose, playback=(label == "playback"))
        t_next += period
        time.sleep(max(0.0, t_next - time.monotonic()))
    if rec:
        rec.end_phase()


def reflex(robot, joints, home, sensor, args, rec):
    """The obstacle reflex: while the sensor reports something within --min-mm, the arm tries to get away from
    it and waits for the way to clear. The base swings away, alternating sides, with quick small twitches mixed
    in; the wrist rolls and the gripper flinches. Sizes, speeds and pauses follow the readings (closer means
    bigger), so noisy readings give an irregular, jerky motion. Only shoulder_pan, wrist_roll and the gripper
    move, each within hard caps around where it started; shoulder_lift, elbow_flex and wrist_flex hold their
    pose. Returns True if the way cleared within --reflex-s."""
    pan, roll, grip = (joints.index(j + ".pos") for j in ("shoulder_pan", "wrist_roll", "gripper"))
    amp = min(args.reflex_amp, REFLEX_AMP_MAX)
    speed = min(args.reflex_speed, REFLEX_SPEED_MAX)
    roll_amp = min(amp, REFLEX_ROLL_MAX)
    period = 1.0 / REFLEX_HZ
    caps = {pan: speed, roll: REFLEX_ROLL_SPEED, grip: REFLEX_GRIP_SPEED}          # per joint, units per second
    limits = {pan: (home[pan] - amp, home[pan] + amp), roll: (home[roll] - roll_amp, home[roll] + roll_amp),
              grip: (min(home[grip], 0.0), max(home[grip], REFLEX_GRIP_MAX))}
    pose, side, swings = list(home), 1, []
    rec.phase("reflex")
    rec.rec["reflex"] = {"amp_deg": amp, "speed_deg_s": speed, "limit_s": args.reflex_s, "home_pan": round(home[pan], 2),
                         "swings": [], "cleared": False}

    def clamp(i, v):
        lo, hi = limits[i]
        return max(lo, min(hi, v, JOINT_LIMIT_DEG), -JOINT_LIMIT_DEG)

    def move(targets, rate=1.0):
        """Move the reflex joints to targets ({index: value}) together, none faster than rate x its cap."""
        goal = list(pose)
        for i, v in targets.items():
            goal[i] = clamp(i, v)
        n = max([1] + [math.ceil(abs(goal[i] - pose[i]) / (caps[i] * min(rate, 1.0) * period) - 1e-9) for i in targets])
        t_next = time.monotonic()
        for step in lerp_path(pose, goal, n):
            if robot is not None:
                send_pose(robot, joints, step)
            rec.frame(robot, joints, step)
            t_next += period
            time.sleep(max(0.0, t_next - time.monotonic()))
        pose[:] = goal

    def hold(seconds):
        t_end = time.monotonic() + seconds
        while time.monotonic() < t_end:
            rec.frame(robot, joints, pose)
            time.sleep(period)

    t_end = time.monotonic() + args.reflex_s
    try:
        while time.monotonic() < t_end:
            ok, _ = check_clear(sensor, args.min_mm, args.precheck_s, min_samples=max(3, int(args.precheck_s * 4)))
            if ok:
                rec.rec["reflex"]["cleared"] = True
                return True
            mms = [r["mm"] for _, r in sensor.samples(0.6) if r.get("st") == "ok"] or [0]
            near = min(mms)
            closeness = 1.0 - min(near, args.min_mm) / float(args.min_mm)
            jitter = (sum(mms) * 0.6180339 + len(swings) * 0.37) % 1.0      # the readings' noise, as 0..1
            j2, j3 = (jitter * 7.0) % 1.0, (jitter * 13.0) % 1.0
            twitch = j2 < 0.3                  # now and then a quick small jerk instead of a full swing
            if jitter > 0.2:                   # usually dodge to the other side; now and then the same side again
                side = -side
            size = (0.25 + 0.15 * j3) if twitch else (0.55 + 0.3 * closeness + 0.15 * j3)
            target = home[pan] + side * amp * size
            roll_t = home[roll] + (j3 * 2.0 - 1.0) * roll_amp
            grip_t = home[grip] + (REFLEX_GRIP_MAX * j2 if j2 > 0.55 else 0.0)
            swings.append({"t": round(time.time() - rec.t0, 2), "nearest_mm": near,
                           "pan_target": round(clamp(pan, target), 1), "twitch": twitch})
            print("reflex: object at %d mm, %s the base to %.1f deg" % (near, "jerking" if twitch else "swinging",
                                                                      clamp(pan, target)), flush=True)
            move({pan: target, roll: roll_t, grip: grip_t})
            hold(0.04 + 0.2 * j2)
        print("reflex: still blocked after %.0f s, giving up" % args.reflex_s, flush=True)
        move({pan: home[pan], roll: home[roll], grip: home[grip]}, rate=0.6)   # back where it started, calmly
        return False
    finally:
        rec.rec["reflex"]["swings"] = swings
        rec.end_phase()


# --------------------------------------------------------------------------- main

def default_robot_id():
    """The calibration to use: romulus_arm if it exists, else the only one saved for the SO follower."""
    saved = sorted(CALIBRATION_DIR.glob("*.json")) if CALIBRATION_DIR.exists() else []
    names = [p.stem for p in saved]
    if "romulus_arm" in names or len(names) != 1:
        return "romulus_arm"
    return names[0]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--arm-port", help="servo bus adapter, e.g. /dev/ttyACM1 (find it with lerobot-find-port)")
    ap.add_argument("--robot-id", default=None, help="name the calibration is saved under (default: romulus_arm, "
                                                     "or the only calibration saved for the SO follower)")
    ap.add_argument("--pod-port", help="sensor pod serial port (default: podd, else the debug probe's VCOM)")
    ap.add_argument("--motion", default=str(DEFAULT_MOTION), help="motion JSON (see extract_motion.py)")
    ap.add_argument("--min-mm", type=int, default=200, help="refuse to move if anything is closer (mm)")
    ap.add_argument("--precheck-s", type=float, default=1.5, help="how long to watch the sensor before moving")
    ap.add_argument("--on-blocked", choices=["refuse", "reflex"], default="refuse",
                    help="when the gate sees something close: refuse to start (exit 2), or run the obstacle reflex "
                         "(swing the base away, alternating sides) until it clears or --reflex-s runs out (exit 4)")
    ap.add_argument("--reflex-s", type=float, default=8.0, help="how long the reflex keeps trying")
    ap.add_argument("--reflex-amp", type=float, default=20.0,
                    help="largest base swing from the start pose, degrees (capped at %d)" % REFLEX_AMP_MAX)
    ap.add_argument("--reflex-speed", type=float, default=55.0,
                    help="base swing speed, degrees/s (capped at %d)" % REFLEX_SPEED_MAX)
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
    ap.add_argument("--runs-dir", help="where run records go (default: arm/runs/)")
    ap.add_argument("--no-record", action="store_true", help="don't write a run record")
    args = ap.parse_args()
    if not (args.dry_run or args.check_only) and not args.arm_port:
        ap.error("--arm-port is required (or use --dry-run / --check-only)")
    args.robot_id = args.robot_id or default_robot_id()

    joints, fps, frames, source, units = load_motion(args.motion)
    fps *= args.speed
    print("motion: %d frames at %.0f fps (%.1f s) from %s" % (len(frames), fps, len(frames) / fps, source))
    rec = Recorder(args, joints, fps, len(frames), source)
    code, verdict = 1, "error"
    robot, sensor = None, None
    try:
        sensor = Ultrasonic(args.pod_port)
        print("ultrasonic: reading from %s for %.1f s ..." % (sensor.source, args.precheck_s))
        rec.phase("gate")
        time.sleep(args.precheck_s)
        ok, msg = check_clear(sensor, args.min_mm, args.precheck_s, min_samples=max(3, int(args.precheck_s * 4)))
        samples = sensor.samples(args.precheck_s)
        rec.rec["gate"] = {"ok": ok, "message": msg, "samples": [{k: r.get(k) for k in ("mm", "us", "st")}
                                                                  for _, r in samples[-10:]]}
        rec.end_phase()
        print("ultrasonic:", msg)
        print("ultrasonic: last samples:", describe_samples(samples))
        reflex_wanted = not ok and msg.startswith("object at") and args.on_blocked == "reflex" \
            and not (args.check_only or args.dry_run)
        if not ok and not reflex_wanted:
            print("NOT MOVING.")
            code, verdict = 2, "refused to start: %s" % msg
            return code
        if ok and args.check_only:
            print("start gate passed (--check-only: no motion).")
            code, verdict = 0, "start gate passed (check only, no motion)"
            return code

        start = frames[0]
        if not args.dry_run:
            robot = connect_arm(args.arm_port, args.robot_id, args.max_step, units)
            start = read_pose(robot, joints)
            print("arm: connected (%s), start pose %s" % (args.robot_id, [round(v, 1) for v in start]))
        if reflex_wanted:
            print("arm: obstacle reflex for up to %.0f s (base +-%.0f deg at %.0f deg/s, wrist roll, gripper)"
                  % (args.reflex_s, min(args.reflex_amp, REFLEX_AMP_MAX), min(args.reflex_speed, REFLEX_SPEED_MAX)))
            if not reflex(robot, joints, start, sensor, args, rec):
                print("REFLEX GAVE UP: the sensor never reported a clear way. Back at the start pose; not running the job.")
                code, verdict = 4, "obstacle reflex gave up after %.0f s: never clear (%d swings)" % (
                    args.reflex_s, len(rec.rec["reflex"]["swings"]))
                return code
            print("ultrasonic: clear now, starting the job")
        if not args.live_check:
            sensor.close()          # start gate passed; the motion itself ignores the sensor
            sensor = None
            print("ultrasonic: start gate passed, not watching during the motion")

        ramp = max(1, int(args.ramp_s * fps))
        print("arm: moving to first frame (%.1f s)" % args.ramp_s)
        play(robot, joints, lerp_path(start, frames[0], ramp), fps, sensor, args, "ramp in", rec)
        print("arm: playing recording")
        play(robot, joints, frames, fps, sensor, args, "playback", rec)
        if not args.no_return:
            print("arm: returning to start pose")
            play(robot, joints, lerp_path(frames[-1], start, ramp), fps, sensor, args, "ramp out", rec)
        if ClampCounter.n:
            print("arm: --max-step slowed %d commands (fast parts of the motion)" % ClampCounter.n)
        print("done.")
        code, verdict = 0, "the job ran: %d of %d frames played" % (rec.rec["frames_sent"], len(frames))
        return code
    except (Halt, KeyboardInterrupt) as e:
        print("STOPPED:", e if isinstance(e, Halt) else "Ctrl-C")
        code, verdict = 3, "stopped mid-motion: %s" % (e if isinstance(e, Halt) else "Ctrl-C")
        if robot is not None:
            here = read_pose(robot, joints)
            send_pose(robot, joints, here)       # hold where it is instead of finishing the move
            print("arm: holding at", [round(v, 1) for v in here])
            if sys.stdin.isatty():
                input("Move the obstacle / support the arm, then press Enter to release torque ...")
            else:                                # run by a tool: no one to press Enter; give a person time
                print("arm: no terminal, holding for 10 s before releasing torque")
                time.sleep(10)
        return code
    finally:
        if robot is not None:
            robot.disconnect()
        if sensor is not None:
            sensor.close()
        path = rec.save(code, verdict)
        if path:
            print("run record: %s" % path)


if __name__ == "__main__":
    sys.exit(main())
