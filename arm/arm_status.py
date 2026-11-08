#!/usr/bin/env python3
"""The arm's state and what its last jobs did, as JSON. The pod healer's arm_state and arm_report tools run this.

  ~/lerobot-venv/bin/python arm/arm_status.py state --arm-port /dev/serial/by-id/<adapter>
  python3 arm/arm_status.py report                 # the newest run record in arm/runs/
  python3 arm/arm_status.py report --run 20261107-235012-123
  python3 arm/arm_status.py list                   # recent runs

state reads the joint positions (degrees; gripper 0-100), torque, load and temperature straight from the
servo bus. It never changes torque, so a limp arm stays limp and a holding arm keeps holding.
report summarises a run record written by safe_replay.py: the start gate's verdict and samples, the
obstacle reflex (swings, base range, direction changes), and the playback: frames played, and for each joint
the range commanded against the range the arm actually reached (range_coverage: the share of the commanded
range the arm reached, 1.0 = all of it) and the median tracking error. Fast moves lag the command on purpose
(--max-step), so per-instant errors there are large and normal.
"""
import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def runs_dir(arg=None):
    return Path(arg or os.environ.get("ARM_RUNS_DIR") or HERE / "runs")


def load_run(d, run=None):
    files = sorted(d.glob("*.json"))
    if not files:
        raise SystemExit(json.dumps({"ok": False, "error": "no run records in %s yet" % d}))
    if run in (None, "", "last", "latest"):
        return json.loads(files[-1].read_text())
    hits = [f for f in files if f.stem == run or f.stem.startswith(run)]
    if not hits:
        raise SystemExit(json.dumps({"ok": False, "error": "no run %r; recent runs: %s"
                                     % (run, ", ".join(f.stem for f in files[-5:]))}))
    return json.loads(hits[-1].read_text())


def _rng(xs):
    return [round(min(xs), 1), round(max(xs), 1)] if xs else None


def summarize(rec):
    joints = rec["joints"]
    out = {"run_id": rec["run_id"], "started": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(rec["started"])),
           "seconds": round(rec.get("ended", rec["started"]) - rec["started"], 1), "exit": rec.get("exit"),
           "verdict": rec.get("verdict"), "mode": rec.get("mode"), "motion": rec.get("motion"),
           "phases": [{"name": p["name"], "seconds": round((p["t1"] or p["t0"]) - p["t0"], 1), "frames": p["frames"]}
                      for p in rec.get("phases", [])]}
    g = rec.get("gate") or {}
    out["start_gate"] = {"passed": g.get("ok"), "message": g.get("message"), "min_mm": rec.get("min_mm"),
                         "last_samples": g.get("samples", [])[-5:]}
    track = [t for t in rec.get("track", []) if isinstance(t[3], list)]
    pan = joints.index("shoulder_pan") if "shoulder_pan" in joints else 0

    r = rec.get("reflex")
    if r:
        targets = [s["pan_target"] for s in r.get("swings", [])]
        sides = [1 if t > r["home_pan"] else -1 for t in targets]
        meas = [t[3][pan] for t in track if t[1] == "reflex"]
        out["obstacle_reflex"] = {
            "ran": True, "cleared": r.get("cleared"), "swings": len(targets),
            "direction_changes": sum(1 for a, b in zip(sides, sides[1:]) if a != b),
            "base_home_deg": r["home_pan"], "base_targets_deg": targets[:20],
            "base_measured_range_deg": _rng(meas), "nearest_mm_seen": [s["nearest_mm"] for s in r.get("swings", [])][:20]}
    else:
        out["obstacle_reflex"] = {"ran": False}

    play = [t for t in track if t[1] == "playback"]
    planned, sent = rec.get("frames_planned"), rec.get("frames_sent", 0)
    pb = {"frames_planned": planned, "frames_played": sent, "complete": bool(planned) and sent == planned}
    if play:
        exp, got, err, cover = {}, {}, {}, {}
        for i, j in enumerate(joints):
            exp[j] = _rng([t[2][i] for t in play])
            got[j] = _rng([t[3][i] for t in play])
            err[j] = round(statistics.median(abs(t[3][i] - t[2][i]) for t in play), 1)
            span = exp[j][1] - exp[j][0]
            overlap = max(0.0, min(exp[j][1], got[j][1]) - max(exp[j][0], got[j][0]))
            cover[j] = round(overlap / span, 2) if span > 1 else None      # None: the joint barely moves here
        pb |= {"commanded_range": exp, "measured_range": got, "range_coverage": cover,
               "median_tracking_error_deg": err, "measured_samples": len(play),
               "commands_slowed_by_max_step": rec.get("clamped_commands", 0)}
    expected = _expected(rec)
    if expected:
        pb["motion_file"] = expected
    out["playback"] = pb
    return out


def _expected(rec):
    """What the motion file asks for: frames, duration and each joint's range."""
    path = HERE / rec.get("motion", "")
    if not path.is_file():
        path = HERE / "motions" / rec.get("motion", "")
    try:
        m = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    joints = [j.removesuffix(".pos") for j in m["joints"]]
    return {"name": rec.get("motion"), "frames": len(m["frames"]),
            "seconds": round(len(m["frames"]) / m["fps"], 1),
            "joint_range": {j: _rng([f[i] for f in m["frames"]]) for i, j in enumerate(joints)}}


def state(port, robot_id):
    from lerobot.robots.so_follower import SO101Follower, SO101FollowerConfig
    import safe_replay
    rid = robot_id or safe_replay.default_robot_id()
    robot = SO101Follower(SO101FollowerConfig(port=port, id=rid, use_degrees=True))
    if not robot.calibration:
        return {"ok": False, "error": "no calibration saved as %r" % rid}
    robot.bus.connect()
    try:
        pos = robot.bus.sync_read("Present_Position")
        extra = {}
        for name in ("Torque_Enable", "Present_Load", "Present_Temperature"):
            try:
                extra[name] = robot.bus.sync_read(name, normalize=False)
            except Exception as e:           # older firmware may not answer every register
                extra[name] = {"error": str(e)}
    finally:
        robot.bus.disconnect(disable_torque=False)
    joints = {j: {"position": round(v, 1), "torque_on": bool(extra["Torque_Enable"].get(j)) if
                  isinstance(extra["Torque_Enable"], dict) else None,
                  "load": extra["Present_Load"].get(j) if "error" not in extra["Present_Load"] else None,
                  "temperature_c": extra["Present_Temperature"].get(j) if "error" not in extra["Present_Temperature"]
                  else None} for j, v in pos.items()}
    return {"ok": True, "robot_id": rid, "port": port, "units": "degrees (gripper: 0-100 % open)", "joints": joints}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["state", "report", "list"])
    ap.add_argument("--arm-port")
    ap.add_argument("--robot-id")
    ap.add_argument("--run", help="run id (or a prefix); default: the newest")
    ap.add_argument("--runs-dir")
    a = ap.parse_args()
    if a.command == "state":
        if not a.arm_port:
            print(json.dumps({"ok": False, "error": "no arm attached (no --arm-port)"}))
            return 1
        try:
            out = state(a.arm_port, a.robot_id)
        except Exception as e:
            out = {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
    elif a.command == "report":
        out = summarize(load_run(runs_dir(a.runs_dir), a.run))
    else:
        files = sorted(runs_dir(a.runs_dir).glob("*.json"))[-10:]
        out = {"runs": [{k: r.get(k) for k in ("run_id", "exit", "verdict")}
                        for r in (json.loads(f.read_text()) for f in files)]}
    print(json.dumps(out, indent=1))
    return 0 if out.get("ok", True) else 1


if __name__ == "__main__":
    sys.exit(main())
