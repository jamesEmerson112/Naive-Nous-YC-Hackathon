"""Generate and test: try the chips pick-and-place on the real arm until the eye says the bag is in the box.

    python -m armsim.gentest run [--max-trials 6]        # on the Mac: the loop (stops the Pi's arm host, restarts it)
    python -m armsim.gentest trial --rec wave --pan 5    # on the Pi: one candidate (the arm host must be stopped)

Generate: Andre's three taught chips motions (wave.json, recording_2, recording_3), first as recorded, then
rotated at the base by small steps (pan +-5, +-10 degrees), so every candidate stays inside the space his
recordings already used. No lift or elbow changes: those could drive the gripper into the table.
Test, two signals:
  - the gripper: while the recording commands it closed, the jaws stop open under load = it is holding
    something (the bag). Reported as `grasped` with how long it held.
  - the eye: after the try, Hermes's vision looks at the camera frame and answers INSIDE / NOT / UNSURE.
Stop at the first INSIDE, at the cap, or at any servo alarm. Every try ends with the arm folded at rest.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

LIB = Path(__file__).resolve().parents[3] / "arm" / "prims" / "library"
RECORDINGS = ("wave", "recording_2", "recording_3")
OFFSETS = (5.0, -5.0, 10.0, -10.0)                 # pan shifts in degrees, after the as-recorded tries
PI = os.environ.get("ARMSIM_PI", "pi@192.168.50.2")
PI_REPO = "~/sage_hardware_healer/Naive-Nous-YC-Hackathon"
CAMERA = os.environ.get("ARMSIM_CAMERA", "/tmp/armcam/latest.jpg")
OUT = Path(__file__).resolve().parents[1] / "runs" / "gentest"


def candidates(n):
    out = [(r, 0.0) for r in RECORDINGS] + [(r, off) for off in OFFSETS for r in RECORDINGS]
    return out[:n]


# ---- on the Pi: one candidate on the real arm -----------------------------------------------------------
def trial(rec, pan):
    from .real import RealArm
    prim = json.loads((LIB / f"{rec}.json").read_text())
    prim = {**prim, "frames": [[f[0] + pan, *f[1:]] for f in prim["frames"]]}
    arm = RealArm().connect()
    rest = arm.read()
    seen = {"holding_s": 0.0, "widest_hold": 0.0, "peak_grip_load_nm": 0.0, "last": time.monotonic()}

    def watch(a):
        now = time.monotonic()
        dt, seen["last"] = now - seen["last"], now
        q, load = a._q, a._load
        cmd = a._goal if a._goal is not None else q
        g, gc, gl = float(q[5]), float(cmd[5]), float(load[5])
        seen["peak_grip_load_nm"] = max(seen["peak_grip_load_nm"], gl)
        if gc < 10 and g - gc > 4 and gl > 0.25:      # told to close, stopped open, under load
            seen["holding_s"] += dt
            seen["widest_hold"] = max(seen["widest_hold"], g)

    arm.hooks.append(watch)
    t0 = time.time()
    try:
        r = arm.play(prim)
    finally:
        arm.hooks.remove(watch)
    back = arm.goto(rest, duration=1.5)
    out = {"recording": rec, "pan_offset": pan, "arrived": r.get("arrived"), "max_err_deg": r.get("max_err_deg"),
           "seconds": round(time.time() - t0, 1), "grasped": seen["holding_s"] >= 0.5,
           "holding_s": round(seen["holding_s"], 2), "widest_hold": round(seen["widest_hold"], 1),
           "peak_grip_load_nm": round(seen["peak_grip_load_nm"], 2), "alarms": arm.status(),
           "back_at_rest": back.get("arrived")}
    arm.disconnect()                                   # torque off, folded on its stops
    return out


# ---- on the Mac: the loop ---------------------------------------------------------------------------------
def ssh(cmd, timeout=180):
    try:
        r = subprocess.run(["ssh", "-n", "-o", "BatchMode=yes", PI, cmd], capture_output=True, text=True,
                           timeout=timeout, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired as exc:            # a backgrounded process can hold the session open
        return 124, (exc.stdout or b"").decode() if isinstance(exc.stdout, bytes) else (exc.stdout or ""), "timed out"
    return r.returncode, r.stdout, r.stderr


HOST_UP = (f"cd {PI_REPO}/prototypes/arm-sim && PYTHONPATH=. setsid nohup .venv/bin/python -u -m armsim.host "
           f"--arm real --pod real --var {PI_REPO}/firmware/healer/var --romulus http://192.168.50.1:8000 "
           "> runs/logs/host.log 2>&1 < /dev/null & disown; echo $! > runs/logs/host.pid; sleep 6; "
           "PYTHONPATH=. .venv/bin/python -c \"from armsim.client import call; s=call('status'); "
           "print('host up', s['ok'], s['status'], s['incidents'])\"")
HOST_DOWN = (f"cd {PI_REPO}/prototypes/arm-sim && PYTHONPATH=. .venv/bin/python -c \"from armsim.client import "
             "call; call('shutdown')\" >/dev/null 2>&1; for i in $(seq 1 20); do "
             "ps -eo comm=,args= | awk '$1 ~ /^python/ && /armsim.host/' | grep -q . || break; sleep 0.5; done; echo down")


def snap(tag):
    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / f"{time.strftime('%H%M%S')}-{tag}.jpg"
    shutil.copy(CAMERA, p)
    return p


def judge(image):
    """Hermes's vision on one camera frame: INSIDE / NOT / UNSURE."""
    q = (f"Use your vision tool on the image file {image}. Is a bag of chips inside the open cardboard box? "
         "Reply with exactly one word: INSIDE, NOT, or UNSURE.")
    try:
        r = subprocess.run(["hermes", "chat", "--oneshot", "-t", "vision", "-q", q], capture_output=True,
                           text=True, timeout=120)
    except subprocess.TimeoutExpired:
        return "UNSURE", "vision timed out"
    words = [w.strip(".,*!") for w in r.stdout.upper().split()]
    for w in reversed(words):
        if w in ("INSIDE", "NOT", "UNSURE"):
            return w, ""
    return "UNSURE", "no verdict in the reply"


def run(max_trials):
    OUT.mkdir(parents=True, exist_ok=True)
    log = OUT / "trials.jsonl"
    print(f"generate and test: up to {max_trials} tries; log {log}", flush=True)
    rc, out, err = ssh(HOST_DOWN)
    print("arm host:", out.strip() or err.strip(), flush=True)
    try:
        for i, (rec, pan) in enumerate(candidates(max_trials), 1):
            before = snap(f"t{i}-before")
            rc, out, err = ssh(f"cd {PI_REPO}/prototypes/arm-sim && PYTHONPATH=. .venv/bin/python -m armsim.gentest "
                               f"trial --rec {rec} --pan {pan}")
            line = (out.strip().splitlines() or [""])[-1]
            try:
                res = json.loads(line)
            except json.JSONDecodeError:
                print(f"try {i} ({rec}, pan {pan:+.0f}): failed to run: {(err or out)[-300:]}", flush=True)
                break
            time.sleep(2)
            after = snap(f"t{i}-after")
            verdict, note = judge(after)
            row = {"try": i, **res, "eye": verdict, "eye_note": note, "before": str(before), "after": str(after),
                   "ts": time.time()}
            with open(log, "a") as f:
                f.write(json.dumps(row) + "\n")
            print(f"try {i}: {rec} pan {pan:+.0f} -> {'GRASPED' if res['grasped'] else 'no grasp'} "
                  f"(held {res['holding_s']} s, peak grip load {res['peak_grip_load_nm']} N*m), "
                  f"eye: {verdict}, {res['seconds']} s, alarms {res['alarms']}", flush=True)
            if any(res["alarms"]):
                print("stopping: a servo reported an alarm", flush=True)
                break
            if verdict == "INSIDE":
                print(f"SUCCESS on try {i}: {rec} with pan {pan:+.0f}", flush=True)
                break
        else:
            print("no success within the cap", flush=True)
    finally:
        rc, out, err = ssh(HOST_UP, timeout=45)
        print((out.strip().splitlines() or [err.strip()])[-1], flush=True)


def main():
    ap = argparse.ArgumentParser(prog="python -m armsim.gentest")
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("trial")
    t.add_argument("--rec", choices=RECORDINGS, required=True)
    t.add_argument("--pan", type=float, default=0.0)
    r = sub.add_parser("run")
    r.add_argument("--max-trials", type=int, default=6)
    a = ap.parse_args()
    if a.cmd == "trial":
        if abs(a.pan) > 10:
            raise SystemExit("pan shift capped at 10 degrees")
        print(json.dumps(trial(a.rec, a.pan)), flush=True)
    else:
        run(a.max_trials)


if __name__ == "__main__":
    main()
