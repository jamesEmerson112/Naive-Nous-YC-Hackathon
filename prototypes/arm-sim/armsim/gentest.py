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
# The debugger and the eye run on Astra (the default profile stays as it is); ARMSIM_HERMES_MODEL overrides.
HERMES_MODEL = ["-m", os.environ.get("ARMSIM_HERMES_MODEL", "gpt-6-astra"), "--provider", "openai-codex"]
FRAME = "metres from the centre of the arm's base: x forward (where the arm faces at pan 0), y to the arm's left, z up (the twin's estimated calibration)"


class GripEvents:
    """Grasp and release from the commanded gripper (0-100): a grasp is a drop of 15 or more from an open
    peak of 40 or more (the recordings squeeze to 1, ~15-20 or ~31); the release is a rise of 10 or more
    after it."""
    def __init__(self):
        self.peak, self.low, self.state = 0.0, None, "idle"      # idle -> open -> grasping -> released

    def step(self, g):
        if self.state in ("idle", "open"):
            self.peak = max(self.peak, g)
            if g >= 40:
                self.state = "open"
            if self.state == "open" and g <= self.peak - 15:
                self.state, self.low = "grasping", g
                return "close_at"
        elif self.state == "grasping":
            self.low = min(self.low, g)
            if g >= self.low + 10:
                self.state = "released"
                return "open_at"
        return None


def planned_points(rec, pan):
    """Where the recording puts the gripper tip when it closes the jaws and when it opens them again (the
    twin's forward kinematics on the recorded frames, rotated by pan): for tries with no measurement."""
    from .arm import SimArm
    import mujoco
    frames = json.loads((LIB / f"{rec}.json").read_text())["frames"]
    arm, out, ev = SimArm(), {}, GripEvents()
    for i in range(len(frames)):
        event = ev.step(frames[i][5])
        if event:
            f = [frames[i][0] + pan, *frames[i][1:]]
            arm.d.qpos[arm._qadr] = arm._raw(f)
            mujoco.mj_forward(arm.m, arm.d)
            out[event] = {"tip_m": [round(float(v), 3) for v in arm.d.site_xpos[arm._tip]],
                          "joints_deg": [round(float(v), 1) for v in f], "t_s": round(i / 50, 2)}
    return out


def candidates(n):
    out = [(r, 0.0) for r in RECORDINGS] + [(r, off) for off in OFFSETS for r in RECORDINGS]
    return out[:n]


# ---- on the Pi: one candidate on the real arm -----------------------------------------------------------
def trial(rec, pan, speed=1.0):
    from .real import RealArm
    prim = json.loads((LIB / f"{rec}.json").read_text())
    prim = {**prim, "frames": [[f[0] + pan, *f[1:]] for f in prim["frames"]]}
    arm = RealArm().connect()
    rest = arm.read()
    seen = {"holding_s": 0.0, "widest_hold": 0.0, "peak_grip_load_nm": 0.0, "last": time.monotonic(),
            "ev": GripEvents(), "close_at": None, "open_at": None}

    def watch(a):
        now = time.monotonic()
        dt, seen["last"] = now - seen["last"], now
        q, load = a._q, a._load
        cmd = a._goal if a._goal is not None else q
        g, gc, gl = float(q[5]), float(cmd[5]), float(load[5])
        seen["peak_grip_load_nm"] = max(seen["peak_grip_load_nm"], gl)
        event = seen["ev"].step(gc)
        if event:
            if seen[event] is None:
                tip = a.tip()[0]
                seen[event] = {"tip_m": [round(float(v), 3) for v in tip],
                               "joints_deg": [round(float(v), 1) for v in q], "t_s": round(a.t, 2)}
        if seen["ev"].state == "grasping" and g - gc > 4 and gl > 0.25:   # squeezing, stopped open, under load
            seen["holding_s"] += dt
            seen["widest_hold"] = max(seen["widest_hold"], g)

    arm.hooks.append(watch)
    t0 = time.time()
    try:
        r = arm.play(prim, speed=speed)
    finally:
        arm.hooks.remove(watch)
    back = arm.goto(rest, duration=1.5)
    out = {"recording": rec, "pan_offset": pan, "speed": speed, "arrived": r.get("arrived"), "max_err_deg": r.get("max_err_deg"),
           "seconds": round(time.time() - t0, 1), "grasped": seen["holding_s"] >= 0.5,
           "holding_s": round(seen["holding_s"], 2), "widest_hold": round(seen["widest_hold"], 1),
           "peak_grip_load_nm": round(seen["peak_grip_load_nm"], 2), "alarms": arm.status(),
           "back_at_rest": back.get("arrived"), "close_at": seen["close_at"], "open_at": seen["open_at"],
           "points": "measured"}
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
        r = subprocess.run(["hermes", "chat", "--oneshot", *HERMES_MODEL, "-t", "vision", "-q", q], capture_output=True,
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


SPEEDS = (0.5, 1.0)                                  # the slowest and fastest replay Hermes may ask for
PLAN_LOG = OUT / "hermes-trials.jsonl"
GOAL = os.environ.get("ARMSIM_GOAL", "the red bag of chips")      # the bag the demo uses


def last_json(text):
    """The last JSON object in Hermes's reply (it may be pretty-printed over several lines)."""
    best, depth, start = None, 0, None
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth:
            depth -= 1
            if depth == 0:
                try:
                    obj = json.loads(text[start:i + 1])
                    if isinstance(obj, dict):
                        best = obj
                except json.JSONDecodeError:
                    pass
    return best


def plan(history, before, after):
    """Hermes debugs the last try and designs the next test. The code clamps whatever it asks for."""
    tries = []
    for h in history[-12:]:
        row = {k: h.get(k) for k in ("try", "recording", "pan_offset", "speed", "grasped", "holding_s",
                                     "widest_hold", "peak_grip_load_nm", "arrived", "eye", "debug")}
        if h.get("close_at") or h.get("open_at"):
            row.update(gripper_closed_at=h.get("close_at"), gripper_opened_at=h.get("open_at"), points="measured")
        else:
            pts = planned_points(h["recording"], float(h.get("pan_offset") or 0.0))
            row.update(gripper_closed_at=pts.get("close_at"), gripper_opened_at=pts.get("open_at"),
                       points="planned (from the recording; no measurement for this try)")
        tries.append(row)
    context = {"coordinate_frame": FRAME, "joint_order": ["shoulder_pan", "shoulder_lift", "elbow_flex",
               "wrist_flex", "wrist_roll", "gripper"], "tries": tries}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "context.json").write_text(json.dumps(context, indent=1) + "\n")
    rows = json.dumps(context, indent=1)
    q = (f"You are debugging a robot arm on a bench. The task: pick up {GOAL} and put it in the open cardboard "
         "box. Other bags on the bench don't count. The arm can only replay one of three motions taught by hand (recordings: wave, recording_2, "
         "recording_3), each a pick-and-place, optionally rotated at the base by pan degrees (-10 to +10; which way "
         "positive turns as seen from the camera is unknown, so infer it from the history) and slowed by speed "
         "(0.5 to 1.0, lower is slower). The gripper data per try: holding_s is how long the jaws were stopped open "
         "under load while told to close (0.5 s or more counts as a grasp), widest_hold is how far open they stopped "
         "(0-100), peak_grip_load_nm is the gripper's peak load, gripper_closed_at / gripper_opened_at are where the "
         "gripper tip was (and the joint angles) when the jaws closed for the grasp and opened for the drop. "
         "The structured context, all tries oldest first (also saved as context.json):\n" + rows + "\n"
         f"Use your vision tool on both camera images of the last try: BEFORE {before} and AFTER {after}. The camera "
         "faces the arm from the front at about table height, so it may not see inside the box. Then reply with ONE "
         'JSON object and nothing else: {"verdict": "INSIDE" or "NOT" or "UNSURE" (is ' + GOAL + ' inside the box '
         'in the AFTER image: it counts as INSIDE when it sits within the box walls, even if part of it sticks out '
         'above the rim), "debug": "one or two sentences: what happened on the last try and why", '
         '"next": {"recording": "wave" or "recording_2" or "recording_3", "pan": number, "speed": number, '
         '"why": "one sentence"}}. Use the coordinates: say where the grasp point was relative to where the bag '
         'appears in the images, and move it toward the bag. Design the next test to fix what went wrong; do not repeat a try that failed '
         "the same way.")
    t0 = time.time()
    try:
        r = subprocess.run(["hermes", "chat", "--oneshot", *HERMES_MODEL, "-t", "vision", "-q", q], capture_output=True, text=True,
                           timeout=180)
        reply = last_json(r.stdout) or {}
    except subprocess.TimeoutExpired:
        reply = {}
    nxt = reply.get("next") if isinstance(reply.get("next"), dict) else {}
    rec = nxt.get("recording") if nxt.get("recording") in RECORDINGS else None
    try:
        pan = max(-10.0, min(10.0, float(nxt.get("pan"))))
        speed = max(SPEEDS[0], min(SPEEDS[1], float(nxt.get("speed"))))
    except (TypeError, ValueError):
        pan, speed = None, None
    verdict = reply.get("verdict") if reply.get("verdict") in ("INSIDE", "NOT", "UNSURE") else "UNSURE"
    return {"verdict": verdict, "debug": str(reply.get("debug") or "no reply from Hermes")[:400],
            "next": None if rec is None or pan is None else {"recording": rec, "pan": round(pan, 1),
                                                              "speed": round(speed, 2), "why": str(nxt.get("why", ""))[:200]},
            "plan_s": round(time.time() - t0, 1)}


def guided(max_trials):
    """One Hermes-designed test per trial until the eye says INSIDE (or the cap, or a servo alarm)."""
    OUT.mkdir(parents=True, exist_ok=True)
    history = []
    for old in (OUT / "trials.jsonl", PLAN_LOG):      # what the earlier runs already learned
        if not old.exists():
            continue
        for line in old.read_text().splitlines():
            try:
                h = json.loads(line)
                history.append({**h, "pan_offset": h.get("pan_offset"), "speed": h.get("speed", 1.0)})
            except json.JSONDecodeError:
                pass
    for k, h in enumerate(history):                  # each guided try's diagnosis was written with the next try
        if str(h.get("try", "")).startswith("g") and k + 1 < len(history) and history[k + 1].get("hermes_debug_before"):
            history[k]["debug"] = history[k + 1]["hermes_debug_before"]
    before = after = snap("now")
    print(f"Hermes-guided generate and test, goal: {GOAL} into the box; up to {max_trials} tries, "
          f"{len(history)} earlier tries as data; "
          f"log {PLAN_LOG}", flush=True)
    rc, out, err = ssh(HOST_DOWN)
    print("arm host:", out.strip() or err.strip(), flush=True)
    try:
        for i in range(1, max_trials + 1):
            p = plan(history, before, after)
            if history:
                history[-1]["eye"] = p["verdict"]
                history[-1]["debug"] = p["debug"]
            print(f"Hermes ({p['plan_s']} s): {p['debug']}", flush=True)
            if p["verdict"] == "INSIDE" and history:
                print(f"SUCCESS: the eye sees the bag in the box after try {history[-1]['try']}", flush=True)
                break
            if not p["next"]:
                print("stopping: Hermes gave no usable next test", flush=True)
                break
            n = p["next"]
            print(f"test {i}: {n['recording']} pan {n['pan']:+.1f} speed {n['speed']} | why: {n['why']}", flush=True)
            before = snap(f"g{i}-before")
            rc, out, err = ssh(f"cd {PI_REPO}/prototypes/arm-sim && PYTHONPATH=. .venv/bin/python -m armsim.gentest "
                               f"trial --rec {n['recording']} --pan {n['pan']} --speed {n['speed']}", timeout=240)
            try:
                res = json.loads((out.strip().splitlines() or [""])[-1])
            except json.JSONDecodeError:
                print(f"test {i} failed to run: {(err or out)[-300:]}", flush=True)
                break
            time.sleep(2)
            after = snap(f"g{i}-after")
            row = {"try": f"g{i}", **res, "plan": n, "before": str(before), "after": str(after), "ts": time.time()}
            history.append(row)
            with open(PLAN_LOG, "a") as f:
                f.write(json.dumps({**row, "hermes_debug_before": p["debug"]}) + "\n")
            print(f"  -> {'GRASPED' if res['grasped'] else 'no grasp'} (held {res['holding_s']} s, widest "
                  f"{res['widest_hold']}, peak grip load {res['peak_grip_load_nm']} N*m), {res['seconds']} s, "
                  f"alarms {res['alarms']}", flush=True)
            if any(res["alarms"]):
                print("stopping: a servo reported an alarm", flush=True)
                break
        else:
            p = plan(history, before, after)               # judge the last try
            print(f"Hermes ({p['plan_s']} s): {p['debug']} | verdict {p['verdict']}", flush=True)
            if p["verdict"] != "INSIDE":
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
    t.add_argument("--speed", type=float, default=1.0)
    r = sub.add_parser("run")
    r.add_argument("--max-trials", type=int, default=6)
    r.add_argument("--guide", choices=["fixed", "hermes"], default="fixed")
    a = ap.parse_args()
    if a.cmd == "trial":
        if abs(a.pan) > 10:
            raise SystemExit("pan shift capped at 10 degrees")
        if not SPEEDS[0] <= a.speed <= SPEEDS[1]:
            raise SystemExit(f"speed must be {SPEEDS[0]}-{SPEEDS[1]}")
        print(json.dumps(trial(a.rec, a.pan, a.speed)), flush=True)
    else:
        guided(a.max_trials) if a.guide == "hermes" else run(a.max_trials)


if __name__ == "__main__":
    main()
