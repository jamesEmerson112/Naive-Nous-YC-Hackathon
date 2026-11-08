"""The arm host: one long-lived process that owns the arm and runs skills on request.

It mirrors Olir's podd: newline-delimited JSON over a Unix socket, one request per line, one
reply per line. LeRobot's SO101Follower takes seconds and ~0.9 GB to start, so the real arm
also wants exactly one long-lived owner of the servo bus (note, "Traps").

    {"op": "skill", "name": "wave"}           -> {"ok": true, "skill": "wave", "result": {...}}
    {"op": "read"} / {"op": "distance"} / {"op": "status"} / {"op": "incidents"} / {"op": "ping"}
    {"op": "inject", "fault": "us_frozen"}    plant a fault (sim only)
    {"op": "repair", "action": "restart_sensor"}  the healer's tool: fix the cause, then verify

The watchdog is plain code (level 1, `level1.py`): on each distance row it checks the last few
seconds. The first critical finding opens an incident: the sentence is spoken, motion halts, the
arm parks, and motion stays blocked until a repair is verified.
"""
import argparse
import json
import os
import shutil
import socket
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from . import level1, prims
from .arm import JOINTS, SimArm
from .client import socket_path
from .record import RunRecorder

ROOT = Path(__file__).resolve().parent.parent
SOCK = Path(socket_path())
RUNS = ROOT / "runs"


class Skills:
    """The commander's skill menu. Each skill moves the arm and reports how it went.

    The sim uses armsim's own poses and wave. The real arm uses the poses and the wave Andre taught
    by hand on it (arm/prims/library/: `bin/prim-record.sh --pose rest home above_block pick`, and
    `bin/prim-record.sh wave`), because the sim's joint zero isn't the real arm's calibrated zero."""

    NEEDED = ("rest", "home", "above_block", "pick")

    def __init__(self, arm, *, poses=None, wave=None):
        self.arm = arm
        self.p = poses or prims.poses()
        self._wave = wave or prims.load("wave")
        missing = [n for n in self.NEEDED if n not in self.p]
        if missing:
            raise SystemExit(f"teach these poses first: {', '.join(missing)} "
                             f"(arm/bin/prim-record.sh --pose {' '.join(missing)})")

    def _keep(self, **joints):
        cur = self.arm.read()
        cur.update({f"{k}.pos": v for k, v in joints.items()})
        return cur

    def go_home(self):
        return self.arm.goto(self.p["home"])

    def reach(self):
        return self.arm.goto(self.p["above_block"])

    def grip(self):
        """Close; if the jaws stall on something, back off to just inside the stall so the servo
        isn't held at its torque limit (on an STS3215 that trips the overload alarm, note "Traps")."""
        r = self.arm.goto(self._keep(gripper=prims.CLOSED), duration=0.8, settle_timeout=0.4)
        g = self.arm.grasp_check()
        if g["holding"]:
            # Find a squeeze between 40% and 90% of the servo's limit: held at the limit for 0.5 s an
            # STS3215 sets its overload bit (32) and the guard blocks motion. Search the commanded
            # opening between fully closed (full squeeze) and where the jaws stopped (no squeeze).
            limit = self.arm.grip_limit_nm()
            lo, hi = float(prims.CLOSED), g["gripper"] + 1.0
            for _ in range(8):
                if 0.4 * limit <= g["load_nm"] < 0.9 * limit:
                    break
                mid = (lo + hi) / 2
                self.arm.send(self._keep(gripper=mid))
                self.arm.hold(0.15)
                g = self.arm.grasp_check(min_load=0.2)
                if g["load_nm"] >= 0.9 * limit:
                    lo = mid                         # still at the limit: open a little more
                else:
                    hi = mid                         # too loose (or let go): close a little
            self.arm.hold(0.6)                       # long enough for a stale overload count to clear
            g = self.arm.grasp_check(min_load=0.2)
        return {**{k: v for k, v in r.items() if k != "arrived"}, "grasp": g, "status": self.arm.status()}

    def release(self):
        return self.arm.goto(self._keep(gripper=prims.OPEN), duration=0.6)

    def wave(self):
        return self.arm.play(self._wave)

    def park(self):
        return self.arm.park(self.p["rest"])

    def approach(self):
        return approach(self.arm, self.p)

    def lift(self):
        return lift(self.arm)

    MENU = ("go_home", "reach", "approach", "grip", "lift", "release", "wave", "park")


SETBACK_MM = 50                              # the sensor sits 50 mm behind the fingertips
SWITCH_MM = max(60, SETBACK_MM + 25)         # stop stepping here, then a fixed final move (note)


def _median_reading(arm, n=3, timeout=1.0):
    """Wait for n fresh D rows ("step and settle"), then the median of the valid ones."""
    start = len(arm.d_rows) and arm.d_rows[-1]["ts"]
    t0 = arm.t
    while arm.t - t0 < timeout:
        arm.tick()
        fresh = [r for r in arm.d_rows if r["ts"] > start]
        if len(fresh) >= n:
            ok = sorted(r["mm"] for r in fresh[-n:] if r["st"] == "ok")
            return (ok[len(ok) // 2] if ok else None), fresh[-n:]
    return None, []


def approach(arm, poses, *, step_m=0.005, max_steps=20):
    """Step down 5 mm at a time over the block, settling on 3 readings each step, until the
    median distance is inside the switch distance; then a fixed final move to the grasp pose."""
    from .model import BLOCK_POS
    start = arm.goto(poses["above_block"])    # always start over the block with the jaws open
    if not start.get("arrived"):
        return {**start, "aborted": "couldn't get over the block to start the approach"}
    if not getattr(arm, "use_ik", True):
        return _approach_joint(arm, poses)
    tip, _ = arm.tip()
    readings, invalid = [], 0
    for step in range(max_steps + 1):
        d, rows = _median_reading(arm)
        if arm.halted():
            return arm._stop()
        if d is None:
            invalid += len(rows) or 3
            if invalid >= 6:
                return {"arrived": False, "aborted": "6 invalid readings in a row", "readings": readings}
        else:
            invalid = 0
            readings.append(d)
            pred = rows[-1].get("pred_mm")
            if pred is not None and pred - d > 20:
                return {"arrived": False, "aborted": f"reading {d} mm is far below the expected {pred:.0f} mm",
                        "readings": readings, "finding": level1.residual_finding(d, pred)}
            if d <= SWITCH_MM:
                r = arm.goto(poses["pick"], duration=0.8)          # the fixed final move
                return {**r, "steps": step, "readings": readings, "switched_at_mm": d}
        tip[2] -= step_m
        try:
            pose = arm.solve((BLOCK_POS[0], BLOCK_POS[1], tip[2]), seed=list(arm.read().values()))
        except ValueError as exc:
            return {"arrived": False, "aborted": str(exc), "readings": readings}
        r = arm.goto(pose, duration=0.3)
        if r.get("halted"):
            return {**r, "readings": readings}
    return {"arrived": False, "aborted": f"no switch distance after {max_steps} steps", "readings": readings}


def _lerp_pose(a, b, f):
    return [x + (y - x) * f for x, y in zip(a, b)]


def _as_list(pose):
    from .arm import JOINTS
    return [pose[j] for j in JOINTS] if isinstance(pose, dict) else list(pose)


def _approach_joint(arm, poses, *, steps=12):
    """The real arm before its kinematics are calibrated: walk the joint-space line from the taught
    above_block pose to the taught pick pose, settling on the sensor each step, and switch to the
    fixed final move once the reading is inside the switch distance (no IK, no prediction)."""
    above, pick = _as_list(poses["above_block"]), _as_list(poses["pick"])
    readings, invalid = [], 0
    for k in range(steps + 1):
        d, rows = _median_reading(arm)
        if arm.halted():
            return arm._stop()
        if d is None:
            invalid += len(rows) or 3
            if invalid >= 6:
                return {"arrived": False, "aborted": "6 invalid readings in a row", "readings": readings}
        else:
            invalid = 0
            readings.append(d)
            if d <= SWITCH_MM:
                r = arm.goto(pick, duration=0.8)
                return {**r, "steps": k, "readings": readings, "switched_at_mm": d, "mode": "joint"}
        if k == steps:
            break
        r = arm.goto(_lerp_pose(above, pick, (k + 1) / (steps + 1)), duration=0.3)
        if r.get("halted"):
            return {**r, "readings": readings}
    return {"arrived": False, "aborted": f"no switch distance after {steps} steps", "readings": readings,
            "mode": "joint"}


def lift(arm, *, up_m=0.03):
    """Lift 30 mm straight up, then check the grasp still holds. Without IK (the real arm before
    calibration), go halfway back along the taught pick -> above_block line instead."""
    if not getattr(arm, "use_ik", True):
        poses = getattr(arm, "taught_poses", None)
        if not poses:
            return {"arrived": False, "aborted": "no taught poses for a joint-space lift", "grasp": arm.grasp_check()}
        target = _lerp_pose(_as_list(poses["pick"]), _as_list(poses["above_block"]), 0.5)
        target[5] = arm.commanded()[5]          # keep the grip's commanded squeeze, not where the jaws stopped
        r = arm.goto(target, duration=0.8)
        arm.hold(0.3)
        return {**r, "grasp": arm.grasp_check(), "mode": "joint"}
    tip, _ = arm.tip()
    try:
        pose = arm.solve((tip[0], tip[1], tip[2] + up_m), seed=list(arm.read().values()))  # keeps the gripper squeezing
    except ValueError as exc:
        return {"arrived": False, "aborted": str(exc), "grasp": arm.grasp_check()}
    r = arm.goto(pose, duration=0.8)
    arm.hold(0.3)
    return {**r, "grasp": arm.grasp_check()}


def speak(text, *, out=None):
    """Say the level-1 sentence on the bench. ARMSIM_SPEAK: live (default), file, or off."""
    mode = os.environ.get("ARMSIM_SPEAK", "live")
    if mode == "off" or not shutil.which("say"):
        return None
    if mode == "file" and out:
        subprocess.run(["say", "-o", str(out), text], check=False)
        return str(out)
    subprocess.Popen(["say", text])
    return "live"


SENSOR_KINDS = ("us_frozen", "us_stuck", "us_scale", "us_offset", "us_noisy", "us_missing_echo", "us_residual")


class Watchdog:
    """Plain code, no LLM: level-1 findings over the recent rows open incidents.

    In pod mode the rows come from podd (armsim.podsource), `clock` is wall-clock time, and the pod's
    own critical incidents park the arm too. A fix is only trusted after `verify()`: a move home,
    fresh readings that pass the checks, and (pod mode) no critical incident left in podd."""

    REPAIRS = ("restart_sensor", "verify")

    def __init__(self, arm, rec, *, clock=None, pod=None):
        self.arm, self.rec, self.incidents = arm, rec, []
        self.clock = clock or (lambda: arm.t)
        self.pod = pod                        # PodSource, or None in plain sim
        self.lock = threading.RLock()
        self.on_open, self.on_resolve = [], []    # callbacks(incident): the Romulus reporter
        self.on_handoff = []                  # callbacks(incident): needs hands (hardware)
        self.verify_pending = None            # set from the pod thread, run on the main thread
        self.home = None                      # the pose verify() checks from (set by serve)
        arm.watchers.append(self)

    def open(self):
        with self.lock:
            return [i for i in self.incidents if i["state"] == "open"]

    def __call__(self, arm):
        if self.open():
            return
        snap = {"now": self.clock(), "D": list(arm.d_rows), "holding": arm.grasp_check()["holding"]}
        found = [f for f in level1.run(snap) if f["severity"] in ("critical", "warn")]
        if found:
            self._open(found[0])

    def arrival(self, result):
        """After a skill: a finding it returned, or a move that didn't arrive (joint_stall)."""
        if self.open():
            return None
        if result.get("finding"):
            return self._open(result["finding"])
        load = self.arm.load_nm(result["worst_joint"]) if result.get("worst_joint") else None
        found = level1.check_arrival(result, load_nm=load)
        return self._open(found[0]) if found else None

    def on_pod_incidents(self, open_list):
        """podd's open incidents, every second (pod thread). A critical one parks the arm; once
        every pod incident tied to our incidents has resolved, a verify is queued."""
        crit = [i for i in open_list if i.get("severity") == "critical"]
        with self.lock:
            mine = self.open()
            for inc in crit:
                if any(inc["id"] in m.get("pod_incidents", []) for m in self.incidents):
                    continue
                if mine:                                  # explain the open incident, don't re-speak
                    mine[0].setdefault("pod_incidents", []).append(inc["id"])
                    mine[0].setdefault("pod_summaries", []).append(inc.get("summary"))
                else:
                    new = self._open(level1.pod_finding(inc))
                    new["pod_incidents"] = [inc["id"]]
                    mine = [new]
            open_ids = {i["id"] for i in open_list}
            for m in mine:
                tied = m.get("pod_incidents")
                if tied and not (set(tied) & open_ids) and not m.get("pod_healed"):
                    m["pod_healed"] = round(self.clock(), 2)
                    self.verify_pending = "pod healed: " + ", ".join(m.get("pod_summaries") or tied)

    def _open(self, f):
        arm = self.arm
        with self.lock:
            inc = {"id": f"inc-{len(self.incidents) + 1}", "state": "open", "opened_at": round(self.clock(), 2),
                   "sim_t": round(arm.t, 2), **f}
            inc["spoken"] = speak(f["summary"], out=RUNS / f"{self.rec.run_id}-{inc['id']}.aiff")
            self.incidents.append(inc)
            arm.halt_reason = f["kind"]
        print(f"WATCHDOG {inc['id']} {f['kind']}: {f['summary']}", flush=True)
        if self.pod:
            self.pod.note("arm_incident", id=inc["id"], kind=f["kind"], summary=f["summary"])
        for cb in self.on_open:
            cb(inc)
        return inc

    def repair(self, action):
        """The healer's tool. restart_sensor: `us off` then `us on` through podd (sim without a pod:
        clear the planted fault). verify: no fix, just re-check. Either way, verify() decides."""
        if action not in self.REPAIRS:
            return {"ok": False, "error": f"unknown repair {action!r}; known: {list(self.REPAIRS)}"}
        did = None
        if action == "restart_sensor":
            if self.pod:
                did = [self.pod.cmd("us off"), self.pod.cmd("us on")]
                did = [{"command": r.get("command"), "ok": r.get("ok")} for r in did]
            else:
                self.arm.clear("us_frozen")
        at = datetime.now(timezone.utc).isoformat()
        for inc in self.open():
            inc.setdefault("actions", []).append({"tool": f"arm_{action}", "args": {}, "ok": True, "at": at,
                                                  **({"did": did} if did else {})})
        return {"ok": True, "action": action, "did": did, **self.verify(f"repair: {action}")}

    def verify(self, why):
        """Main thread only (it moves the arm). Go home with the jaws open even though motion is
        halted, hold still on fresh readings, then close what the checks say is fixed."""
        self.verify_pending = None
        if not self.open():
            self.arm.hold(0.6)                    # no incident: refresh the servo status bits
            return {"verified": True, "open": [], "status": self.arm.status()}
        arm = self.arm
        arm._parking = True                   # the watchdog's own move is allowed while halted
        try:
            moved = arm.goto(self.home) if self.home else {"arrived": True}
        finally:
            arm._parking = False
        arm.d_rows.clear()
        arm.hold(3.5)
        snap = {"now": self.clock(), "D": list(arm.d_rows)}
        still = [f for f in level1.run(snap) if f["severity"] in ("critical", "warn")]
        rows_ok = sum(1 for r in arm.d_rows if r.get("st") == "ok")
        pod_crit = [i for i in (self.pod.open if self.pod else []) if i.get("severity") == "critical"]
        resolved, handoffs = [], []
        with self.lock:
            for inc in self.open():
                if inc["kind"] == "joint_stall":
                    fixed = bool(moved.get("arrived"))
                elif inc["kind"] in SENSOR_KINDS or inc["kind"].startswith("pod_"):
                    fixed = not still and not pod_crit and rows_ok >= 10
                else:
                    fixed = not still
                if fixed:
                    inc.update(state="resolved", resolved_at=round(self.clock(), 2), fix=why)
                    resolved.append(inc)
                else:
                    # Needs hands: a joint that still won't move after a checked retry, or a sensor
                    # that fails two checks. The arm stays parked; Romulus gets a hardware report.
                    inc["verify_failures"] = inc.get("verify_failures", 0) + 1
                    if not inc.get("handed_off") and (inc["kind"] == "joint_stall" or inc["verify_failures"] >= 2):
                        inc.update(handed_off=round(self.clock(), 2), handoff_reason=(
                            "still failing after a checked retry: " + ("; ".join(f["summary"] for f in still)
                                                                     or f"home move arrived={moved.get('arrived')}")))
                        handoffs.append(inc)
            if not self.open():
                arm.halt_reason = None
        for inc in handoffs:
            print(f"WATCHDOG {inc['id']} handed off (hardware): {inc['handoff_reason']}", flush=True)
            if self.pod:
                self.pod.note("arm_handoff", id=inc["id"], kind=inc["kind"], reason=inc["handoff_reason"])
            for cb in self.on_handoff:
                cb(inc)
        for inc in resolved:
            print(f"WATCHDOG {inc['id']} resolved ({why})", flush=True)
            if self.pod:
                self.pod.note("arm_resolved", id=inc["id"], kind=inc["kind"], fix=why)
            for cb in self.on_resolve:
                cb(inc)
        return {"verified": not self.open(), "home_arrived": moved.get("arrived"), "ok_rows": rows_ok,
                "still": [f["summary"] for f in still], "pod_critical": [i.get("summary") for i in pod_crit],
                "resolved": [i["id"] for i in resolved], "open": [i["id"] for i in self.open()]}


POD_FAULTS = {"us_frozen": "frozen", "us_no_echo": "no_echo"}     # in pod-sim mode these live in the pod
POD_VAR = Path(os.environ.get("ARMSIM_POD_VAR") or Path(os.environ.get("TMPDIR", "/tmp")) / "armsim-pod")


def start_podd(port, var):
    """Olir's podd, unchanged, on the virtual pod's pty (tonight) with its own HEALER_VAR."""
    var.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, HEALER_VAR=str(var), POD_PORT=port)
    out = open(var / "podd.out", "a")
    return subprocess.Popen([str(ROOT.parents[1] / "firmware" / "healer" / "bin" / "podd")], env=env,
                            stdout=out, stderr=subprocess.STDOUT)


def serve(*, gif=False, view=False, realtime=False, pod=None, var=POD_VAR, romulus=None, device_id=None,
          arm_kind="sim"):
    """pod=None: plain sim (distance from the sim itself). pod="sim": a virtual pod on a pty, read by
    Olir's real podd. pod="real": podd is already running on the real pod; `var` is its HEALER_VAR.
    arm_kind="real": the real SO-101 through Andre's prims (needs pod="real").
    romulus: Romulus's URL; the arm then checks in and reports its incidents as `device_id`."""
    rec = RunRecorder(RUNS, task="arm host session", joints=JOINTS)
    if arm_kind == "real":
        if pod != "real":
            raise SystemExit("--arm real needs --pod real: the real arm reads its distance from podd")
        from .real import RealArm, andre_library
        poses, wave = andre_library()
        arm = RealArm(recorder=rec).connect()
        arm.taught_poses = poses
        skills = Skills(arm, poses=poses, wave=wave)
        print(f"real arm connected (IK {'on' if arm.use_ik else 'off: joint-space moves'}; "
              f"registers {arm.registers})", flush=True)
    else:
        arm = SimArm(recorder=rec, start=prims.REST)
        skills = Skills(arm)
    vpod = podd = source = None
    if pod:
        from .arm import SCENE_OBJECTS
        from .podsource import PodSource, healer_client
        arm.pod_mode = True                           # podd stamps rows with wall-clock time
        realtime = arm_kind == "sim"                  # the sim paces itself; the real arm runs in real time
        client = healer_client(var)
        if pod == "sim":
            from .podheal_sim import PodHealSim
            from .vpod import VirtualPod
            vpod = VirtualPod(arm)
            podd = start_podd(vpod.port, Path(var))
            for _ in range(100):                      # wait for podd's socket, then boot the pod
                try:
                    client.call("ping", timeout=0.5)
                    break
                except OSError:
                    time.sleep(0.1)
            vpod.start()
        source = PodSource(arm, client, objects=SCENE_OBJECTS).start()
        dog = Watchdog(arm, rec, clock=time.time, pod=source)
        source.on_incidents.append(dog.on_pod_incidents)
        if vpod:
            PodHealSim(vpod, source, var).start()
    else:
        dog = Watchdog(arm, rec)
    dog.home = skills.p["home"]
    reporter = None
    if romulus:
        from .romulus import ArmReporter
        kind = "real" if pod == "real" else "sim"
        device_id = device_id or ("arm-1" if kind == "real" else "arm-twin-1")
        reporter = ArmReporter(romulus, device_id, kind=kind, state_dir=ROOT.parents[1] / "data" / "armsim",
                               arm=arm, dog=dog).start()
        print(f"reporting to Romulus at {romulus} as {device_id} ({kind})", flush=True)
    if gif:
        from .render import Gif
        gif = Gif(arm, RUNS / f"{rec.run_id}.gif")
    viewer = None
    if view and arm_kind == "sim":
        import mujoco.viewer
        viewer = mujoco.viewer.launch_passive(arm.m, arm.d)
        arm.hooks.append(lambda a: viewer.sync())
    if view or realtime:
        clock = {"wall": time.monotonic(), "sim": arm.t}
        def pace(a):
            ahead = (a.t - clock["sim"]) - (time.monotonic() - clock["wall"])
            if ahead < -0.25:                         # the host sat idle: re-anchor, don't sprint
                clock.update(wall=time.monotonic(), sim=a.t)
            elif ahead > 0:
                time.sleep(ahead)
        arm.hooks.append(pace)

    SOCK.parent.mkdir(parents=True, exist_ok=True)
    if SOCK.exists():
        SOCK.unlink()
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(str(SOCK))
    srv.listen(4)
    srv.settimeout(0.5)
    state = {"parked": True}
    print(f"arm host listening on {SOCK} (run record {rec.path.name}"
          f"{', pod ' + pod + ' via podd at ' + str(var) if pod else ''})", flush=True)

    def park():
        state["parked"] = True
        return skills.park()

    def handle(req):
        op = req.get("op")
        if op == "ping":
            return {"ok": True, "t": arm.t}
        if op == "skill":
            name = req.get("name")
            if name not in Skills.MENU:
                return {"ok": False, "error": f"unknown skill {name!r}; the menu is {list(Skills.MENU)}"}
            if dog.open() and name != "park":
                return {"ok": False, "error": "incident open; the arm stays parked until a repair is verified",
                        "incidents": dog.open()}
            state["parked"] = False
            result = getattr(skills, name)()
            reply = {"ok": True, "skill": name, "result": result, "t": round(arm.t, 2)}
            if not result.get("halted") and name != "park":
                dog.arrival(result)
            if result.get("halted") or dog.open():
                reply.update(parked=park(), incident=dog.open()[0] if dog.open() else None)
            elif name == "park":
                state["parked"] = True
            return reply
        if op == "read":
            return {"ok": True, "pos": arm.read()}
        if op == "distance":
            return {"ok": True, "row": arm.d_rows[-1] if pod and arm.d_rows else arm.distance()}
        if op == "status":
            out = {"ok": True, "status": arm.status(), "grasp": arm.grasp_check(),
                   "incidents": [{k: i[k] for k in ("id", "kind", "summary")} for i in dog.open()]}
            if source:
                out["pod"] = {"podd_up": source.up, "image": vpod.image if vpod else None,
                              "incidents": [{k: i.get(k) for k in ("kind", "severity", "summary")} for i in source.open]}
            if reporter:
                out["romulus"] = {"device": reporter.device_id, "last_checkin": reporter.last.get("checkin"),
                                  "error": reporter.last.get("error"), "queued": len(reporter.outbox),
                                  "reported": reporter.last["reported"][-3:]}
            out["handed_off"] = [{k: i[k] for k in ("id", "kind", "summary", "handoff_reason")}
                                 for i in dog.open() if i.get("handed_off")]
            return out
        if op == "incidents":
            return {"ok": True, "incidents": dog.incidents}
        if op == "repair":
            return dog.repair(req.get("action"))
        if op == "inject":
            fault = req["fault"]
            if vpod and fault in POD_FAULTS:
                vpod.set_fault(POD_FAULTS[fault])
                return {"ok": True, "fault": fault, "where": "pod"}
            if vpod and fault == "pod_image02":
                vpod.flash("02")                       # a bad pod image goes out (an OTA, say)
                return {"ok": True, "fault": fault, "where": "pod", "build": vpod.build}
            return {"ok": True, "fault": arm.inject(fault, **req.get("params", {})), "where": "arm"}
        if op == "clear":
            arm.clear(req.get("fault"))
            if vpod:
                for f in list(vpod.faults):
                    vpod.set_fault(f, False)
            return {"ok": True}
        if op == "shutdown":
            return {"ok": True, "bye": True}
        return {"ok": False, "error": f"unknown op {op!r}"}

    try:
        while True:
            try:
                conn, _ = srv.accept()
            except socket.timeout:                    # idle: the main thread's own chores
                if dog.verify_pending:
                    print("VERIFY", dog.verify_pending, json.dumps(dog.verify(dog.verify_pending))[:300], flush=True)
                elif dog.open() and not state["parked"]:
                    park()                            # a pod incident while idle: park anyway
                continue
            conn.settimeout(None)
            with conn, conn.makefile("rw", encoding="utf-8") as f:
                for line in f:
                    try:
                        reply = handle(json.loads(line))
                    except Exception as exc:  # report, keep serving
                        reply = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
                    print(json.dumps({"req": line.strip()[:120], "reply": reply})[:400], flush=True)
                    f.write(json.dumps(reply) + "\n")
                    f.flush()
                    if reply.get("bye"):
                        raise SystemExit
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        srv.close()
        SOCK.unlink(missing_ok=True)
        rec.close("host stopped", findings=[{k: i[k] for k in ("kind", "severity", "summary")} for i in dog.incidents])
        if reporter:
            reporter.stop()
        if source:
            source.stop()
        if vpod:
            vpod.stop()
        if podd:
            podd.terminate()
            podd.wait(5)
        if gif:
            print("gif:", gif.save(), flush=True)
        if viewer:
            viewer.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Run the simulated arm host.")
    ap.add_argument("--gif", action="store_true", help="save a GIF of the session in runs/")
    ap.add_argument("--view", action="store_true", help="open MuJoCo's viewer (on macOS, run with mjpython)")
    ap.add_argument("--realtime", action="store_true", help="pace the sim to wall-clock time")
    ap.add_argument("--pod", choices=["sim", "real"], help="read the wrist sensor through podd: a virtual pod "
                    "(sim), or a podd already running on the real pod (real)")
    ap.add_argument("--var", default=str(POD_VAR), help="podd's HEALER_VAR (default $ARMSIM_POD_VAR or $TMPDIR/armsim-pod)")
    ap.add_argument("--romulus", default=os.environ.get("ARMSIM_ROMULUS"),
                    help="Romulus's URL, e.g. http://localhost:8000 (check-ins and incident reports)")
    ap.add_argument("--device-id", default=os.environ.get("ARMSIM_DEVICE_ID"),
                    help="the arm's id in Romulus (default arm-twin-1 in sim, arm-1 with --pod real)")
    ap.add_argument("--arm", choices=["sim", "real"], default="sim",
                    help="real: the SO-101 through Andre's prims (arm/arm.env sets ARM_PORT); needs --pod real")
    a = ap.parse_args()
    serve(gif=a.gif, view=a.view, realtime=a.realtime, pod=a.pod, var=Path(a.var), romulus=a.romulus,
          device_id=a.device_id, arm_kind=a.arm)
