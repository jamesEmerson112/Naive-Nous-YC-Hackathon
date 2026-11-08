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


SQUEEZE = 1.0          # the gripper command while grasping: 99% closed (0 = shut, 100 = open), as wave already does


def squeezed(frames):
    """Every grasp squeezes to 99% closed, from the moment the jaws start closing until they open for the drop.
    recording_2 alone stops at ~31 (69% closed) and recording_3 at ~20 (80%), loose enough for the bag to slip."""
    ev, out, grasping = GripEvents(), [], False
    for f in frames:
        event = ev.step(f[5])
        if event == "close_at":
            grasping = True
        elif event == "open_at":
            grasping = False
        out.append([*f[:5], min(f[5], SQUEEZE)] if grasping else list(f))
    return out


def candidates(n):
    out = [(r, 0.0) for r in RECORDINGS] + [(r, off) for off in OFFSETS for r in RECORDINGS]
    return out[:n]


PROBE_HOLD_S = 3.0      # how long a probe holds the open jaws at the closing point (the Mac snaps the camera then)
PROBE_EARLY = 10        # frames (0.2 s) before the jaws start to close: they are still open there


def grasp_index(frames):
    ev = GripEvents()
    for i, f in enumerate(frames):
        if ev.step(f[5]) == "close_at":
            return i
    return None


# ---- on the Pi: a probe: reach the closing point with the jaws open, hold, back out the same way -----------
def probe(rec, pan, speed=1.0):
    from .real import RealArm
    prim = json.loads((LIB / f"{rec}.json").read_text())
    frames = [[f[0] + pan, *f[1:]] for f in prim["frames"]]
    k = grasp_index(frames)
    if k is None:
        raise SystemExit(f"{rec} never closes its jaws")
    reach = frames[:max(2, k - PROBE_EARLY)]
    arm = RealArm().connect()
    rest = arm.read()
    try:
        r = arm.play({**prim, "frames": reach}, speed=speed)
        q = [round(float(v), 1) for v in arm._q]
        tip = [round(float(v), 3) for v in arm.tip()[0]]
        print("PAUSED", flush=True)                    # the Mac snaps the camera during the hold
        time.sleep(PROBE_HOLD_S)
        back = arm.play({**prim, "frames": reach[::-1]}, speed=1.0)   # out along the way it came in
    finally:
        home = arm.goto(rest, duration=1.5)
        alarms = arm.status()
        arm.disconnect()
    return {"recording": rec, "pan_offset": pan, "speed": speed, "probe": True, "arrived": r.get("arrived"),
            "open_jaws_at": {"tip_m": tip, "joints_deg": q}, "backed_out": back.get("arrived"),
            "back_at_rest": home.get("arrived"), "alarms": alarms}


# ---- on the Pi: one candidate on the real arm -----------------------------------------------------------
def trial(rec, pan, speed=1.0):
    from .real import RealArm
    prim = json.loads((LIB / f"{rec}.json").read_text())
    prim = {**prim, "frames": squeezed([[f[0] + pan, *f[1:]] for f in prim["frames"]])}
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
                if event == "close_at":
                    print("CLOSING", flush=True)           # the Mac snaps the camera: the jaws on the bag
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
           "points": "measured", "squeeze_pct": round(100 - SQUEEZE)}
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


def plan(history, before, after, close=None):
    """Hermes debugs the last try and designs the next test. The code clamps whatever it asks for."""
    tries = []
    for h in history[-12:]:
        row = {k: h.get(k) for k in ("try", "probe", "go", "probe_check", "recording", "pan_offset", "speed",
                                     "squeeze_pct", "grasped", "holding_s",
                                     "widest_hold", "peak_grip_load_nm", "arrived", "eye", "debug", "bag_estimate")}
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
         f"Use your vision tool on the camera images of the last try: BEFORE {before} and AFTER {after}"
         + (f", and CLOSE {close}, taken the moment the jaws closed on the grasp: it shows the jaws and the bag in the same picture, so read the miss from it directly (both jaws on top of the bag, short of its edge, past it, or to its left or right as seen in the image, and roughly how far) and correct the next test by that" if close else "")
         + ". The camera "
         "faces the arm from the front at about table height, so it may not see inside the box. "
         "The grip: tries with squeeze_pct 99 squeezed the gripper to 99% closed while grasping. Tries without it "
         "squeezed as recorded: wave to 99%, recording_2 only to ~69% and recording_3 to ~80%, so a short hold with "
         "those two may have been too loose a grip rather than a wrong grasp point. "
         "Tries with probe true did not grasp: the arm stopped with the jaws open at the closing point "
         "(gripper_closed_at is where the open jaws were), you checked the camera, and probe_check says where the "
         "jaws were relative to the bag's edge (go false = not straddling it, so the full motion was skipped). "
         "Use those to aim the next test. "
         "What the person at the bench sees on every try: the jaws do not straddle the bag. Both jaws land on the "
         "same side, on top of the bag, and just pinch its slippery face. A grip that holds needs one jaw under the "
         "bag's edge and one over it. So the closing point has to be AT the edge, at the edge's height, with the open "
         "jaws reaching just past it, not on top of the bag body. You cannot change the wrist or the height directly, "
         "only pan, speed and which recording, and the recordings close at different heights (gripper_closed_at z: "
         "wave about 0.06 m, recording_2 and recording_3 about 0.02 m, near the table). Choose the one whose closing "
         "height matches the edge, and aim its closing point onto the edge line. "
         "The red bag has some white labels on it: they are part of the same bag, not a separate object, so measure "
         "the whole bag including them, and don't take a white label for a gap or for the bag's edge. "
         f"Measure {GOAL} in the AFTER image before you design the test: its length, width and thickness in cm "
         "(for scale, the arm's upper arm and forearm are each about 12-13 cm long, and the box is in view), and where "
         "its centre and the edge nearest the arm are, in the arm's coordinate frame above. Compare with your earlier "
         "bag_estimate in the tries (if any) and correct it. Advice from the person at the bench: grabbing the bag by "
         "its edge (the flat sealed rim, or the side nearest the arm) is usually easier than grabbing its full body, so "
         "don't steer the grasp toward the middle of the bag; a grasp on the edge that held briefly is close, so make "
         "it bite deeper into that edge. Then do the math: the jaws must close on the bag's edge, so aim the grasp "
         "point there, and pick pan so the measured gripper_closed_at moves onto it. "
         "Then reply with ONE "
         'JSON object and nothing else: {"verdict": "INSIDE" or "NOT" or "UNSURE" (is ' + GOAL + ' inside the box '
         'in the AFTER image: it counts as INSIDE when it sits within the box walls, even if part of it sticks out '
         'above the rim), "bag": {"size_cm": [length, width, thickness], "centre_m": [x, y], '
         '"near_edge_m": [x, y], "confidence": "high" or "medium" or "low"}, '
         '"debug": "one or two sentences: what happened on the last try and why", '
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
            "bag": reply.get("bag") if isinstance(reply.get("bag"), dict) else None,
            "next": None if rec is None or pan is None else {"recording": rec, "pan": round(pan, 1),
                                                              "speed": round(speed, 2), "why": str(nxt.get("why", ""))[:200]},
            "plan_s": round(time.time() - t0, 1)}


def trial_streamed(args, mark, tag, timeout=240):
    """Run `gentest trial <args>` on the Pi, streaming its output; when it prints `mark` (PAUSED for a probe,
    CLOSING when the jaws close on a full try), snap the camera 1 s later (the capture is 1 fps, so that frame
    is from after the event). Returns (the trial's JSON, the snapped image or None)."""
    cmd = f"cd {PI_REPO}/prototypes/arm-sim && PYTHONPATH=. .venv/bin/python -m armsim.gentest trial {args}"
    p = subprocess.Popen(["ssh", "-n", "-o", "BatchMode=yes", PI, cmd], stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, text=True)
    t0, image, last = time.time(), None, ""
    for line in p.stdout:
        line = line.strip()
        if line == mark and image is None:
            time.sleep(1.0)
            image = snap(tag)
        elif line:
            last = line
        if time.time() - t0 > timeout:
            p.kill()
            break
    p.wait(timeout=30)
    try:
        return json.loads(last), image
    except json.JSONDecodeError:
        return {"error": last[-300:]}, image


def run_probe(n, tag):
    """Probe on the Pi; snap the camera while it holds the open jaws at the closing point."""
    return trial_streamed(f"--probe --rec {n['recording']} --pan {n['pan']} --speed {n['speed']}", "PAUSED",
                          f"{tag}-probe", timeout=180)


def probe_check(image, bag):
    """Astra on the probe frame: do the open jaws straddle the bag's edge?"""
    q = (f"Use your vision tool on the image file {image}. The robot arm has stopped with its gripper jaws OPEN at "
         f"the exact point where it will close them to grab {GOAL} (the red bag has white labels on it; they are "
         "part of it). The camera faces the arm from the front at about table height. Your last estimate of the "
         f"bag: {json.dumps(bag)} (arm frame, metres). Bags of chips are slippery: a grip only holds when the open "
         "jaws straddle the bag's edge, one jaw under it and one over it, so closing clamps the edge. Do they? "
         'Reply with ONE JSON object and nothing else: {"go": true or false, "why": "one sentence: where the open '
         'jaws are relative to the bag\'s edge (straddling it, both on top, short of it, past it, left or right '
         'of it as seen in the image) and roughly how far"}.')
    t0 = time.time()
    try:
        r = subprocess.run(["hermes", "chat", "--oneshot", *HERMES_MODEL, "-t", "vision", "-q", q], capture_output=True,
                           text=True, timeout=120)
        reply = last_json(r.stdout) or {}
    except subprocess.TimeoutExpired:
        reply = {}
    return bool(reply.get("go") is True), str(reply.get("why") or "no reply")[:300], round(time.time() - t0, 1)


def guided(max_trials, probe_first=False):
    """One Hermes-designed test per trial until the eye says INSIDE (or the cap, or a servo alarm). With
    probe_first, each test is first probed (jaws open at the closing point, a camera check by Astra) and only
    run in full on a go; a no-go is logged as a probe try for the next plan."""
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
            history[k]["bag_estimate"] = history[k + 1].get("hermes_bag_before")
    before = after = snap("now")
    close = None
    print(f"Hermes-guided generate and test, goal: {GOAL} into the box; up to {max_trials} tries, "
          f"{len(history)} earlier tries as data; "
          f"log {PLAN_LOG}", flush=True)
    rc, out, err = ssh(HOST_DOWN)
    print("arm host:", out.strip() or err.strip(), flush=True)
    try:
        for i in range(1, max_trials + 1):
            p = plan(history, before, after, close)
            if history:
                history[-1]["eye"] = p["verdict"]
                history[-1]["debug"] = p["debug"]
                history[-1]["bag_estimate"] = p["bag"]
            print(f"Hermes ({p['plan_s']} s): {p['debug']}", flush=True)
            if p["bag"]:
                print(f"Hermes bag: {json.dumps(p['bag'])}", flush=True)
            if p["verdict"] == "INSIDE" and history:
                print(f"SUCCESS: the eye sees the bag in the box after try {history[-1]['try']}", flush=True)
                break
            if not p["next"]:
                print("stopping: Hermes gave no usable next test", flush=True)
                break
            n = p["next"]
            print(f"test {i}: {n['recording']} pan {n['pan']:+.1f} speed {n['speed']} | why: {n['why']}", flush=True)
            before = snap(f"g{i}-before")
            if probe_first:
                pr, image = run_probe(n, f"p{i}")
                if "error" in pr or image is None:
                    print(f"probe {i} failed: {pr.get('error', 'no pause seen')}", flush=True)
                    break
                go, why, check_s = probe_check(image, p["bag"])
                prow = {"try": f"p{i}", **pr, "close_at": pr["open_jaws_at"], "grasped": None, "go": go,
                        "probe_check": why, "plan": n, "before": str(before), "after": str(image), "ts": time.time()}
                history.append(prow)
                with open(PLAN_LOG, "a") as f:
                    f.write(json.dumps({**prow, "hermes_debug_before": p["debug"], "hermes_bag_before": p["bag"]}) + "\n")
                print(f"  probe {i}: jaws open at {pr['open_jaws_at']['tip_m']} -> {'GO' if go else 'NO-GO'} "
                      f"({check_s} s): {why} | alarms {pr['alarms']}", flush=True)
                if any(pr["alarms"]):
                    print("stopping: a servo reported an alarm", flush=True)
                    break
                if not go:
                    after, close = image, None
                    continue
                before = snap(f"g{i}-before")
            res, close_img = trial_streamed(f"--rec {n['recording']} --pan {n['pan']} --speed {n['speed']}",
                                            "CLOSING", f"g{i}-close")
            if "error" in res:
                print(f"test {i} failed to run: {res['error']}", flush=True)
                break
            time.sleep(2)
            after = snap(f"g{i}-after")
            close = close_img
            row = {"try": f"g{i}", **res, "plan": n, "before": str(before), "after": str(after),
                   "close_image": str(close_img) if close_img else None, "ts": time.time()}
            history.append(row)
            with open(PLAN_LOG, "a") as f:
                f.write(json.dumps({**row, "hermes_debug_before": p["debug"], "hermes_bag_before": p["bag"]}) + "\n")
            print(f"  -> {'GRASPED' if res['grasped'] else 'no grasp'} (held {res['holding_s']} s, widest "
                  f"{res['widest_hold']}, peak grip load {res['peak_grip_load_nm']} N*m), {res['seconds']} s, "
                  f"alarms {res['alarms']}", flush=True)
            if any(res["alarms"]):
                print("stopping: a servo reported an alarm", flush=True)
                break
        else:
            p = plan(history, before, after, close)        # judge the last try
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
    t.add_argument("--probe", action="store_true", help="stop with the jaws open at the closing point, hold, back out")
    r = sub.add_parser("run")
    r.add_argument("--max-trials", type=int, default=6)
    r.add_argument("--guide", choices=["fixed", "hermes"], default="fixed")
    r.add_argument("--probe", action="store_true", help="probe each test first; run it in full only on Astra's go")
    a = ap.parse_args()
    if a.cmd == "trial":
        if abs(a.pan) > 10:
            raise SystemExit("pan shift capped at 10 degrees")
        if not SPEEDS[0] <= a.speed <= SPEEDS[1]:
            raise SystemExit(f"speed must be {SPEEDS[0]}-{SPEEDS[1]}")
        print(json.dumps((probe if a.probe else trial)(a.rec, a.pan, a.speed)), flush=True)
    else:
        guided(a.max_trials, probe_first=a.probe) if a.guide == "hermes" else run(a.max_trials)


if __name__ == "__main__":
    main()
