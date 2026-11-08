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
import time
from pathlib import Path

from . import level1, prims
from .arm import JOINTS, SimArm
from .client import socket_path
from .record import RunRecorder

ROOT = Path(__file__).resolve().parent.parent
SOCK = Path(socket_path())
RUNS = ROOT / "runs"


class Skills:
    """The commander's skill menu. Each skill moves the arm and reports how it went."""

    def __init__(self, arm):
        self.arm, self.p, self._wave = arm, prims.poses(), prims.load("wave")

    def _keep(self, **joints):
        cur = self.arm.read()
        cur.update({f"{k}.pos": v for k, v in joints.items()})
        return cur

    def go_home(self):
        return self.arm.goto(self.p["home"])

    def reach(self):
        return self.arm.goto(self.p["above_block"])

    def grip(self):
        r = self.arm.goto(self._keep(gripper=prims.CLOSED), duration=0.8)
        self.arm.hold(0.3)
        return {**r, "grasp": self.arm.grasp_check()}

    def release(self):
        return self.arm.goto(self._keep(gripper=prims.OPEN), duration=0.6)

    def wave(self):
        return self.arm.play(self._wave)

    def park(self):
        return self.arm.park(self.p["rest"])

    MENU = ("go_home", "reach", "grip", "release", "wave", "park")


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


class Watchdog:
    """Plain code, no LLM: level-1 findings over the recent rows open incidents."""

    def __init__(self, arm, rec):
        self.arm, self.rec, self.incidents = arm, rec, []
        arm.watchers.append(self)

    def open(self):
        return [i for i in self.incidents if i["state"] == "open"]

    def __call__(self, arm):
        if self.open():
            return
        crit = [f for f in level1.run({"now": arm.t, "D": list(arm.d_rows)}) if f["severity"] == "critical"]
        if not crit:
            return
        f = crit[0]
        inc = {"id": f"inc-{len(self.incidents) + 1}", "state": "open", "opened_at": round(arm.t, 2), **f}
        inc["spoken"] = speak(f["summary"], out=RUNS / f"{self.rec.run_id}-{inc['id']}.aiff")
        self.incidents.append(inc)
        arm.halt_reason = f["kind"]
        print(f"WATCHDOG {inc['id']} {f['kind']}: {f['summary']}", flush=True)

    def repair(self, action):
        """Fix the cause (sim: the planted fault), then verify on fresh rows before closing."""
        if action != "restart_sensor":
            return {"ok": False, "error": f"unknown repair {action!r}; known: ['restart_sensor']"}
        self.arm.clear("us_frozen")
        self.arm.d_rows.clear()
        self.arm.hold(3.5)                             # fresh readings, arm still parked
        still = level1.run({"now": self.arm.t, "D": list(self.arm.d_rows)})
        fixed = not any(f["kind"] in ("us_frozen", "us_stuck") for f in still)
        if fixed:
            for inc in self.open():
                inc.update(state="resolved", resolved_at=round(self.arm.t, 2), fix=action)
            self.arm.halt_reason = None
        return {"ok": True, "action": action, "verified": fixed,
                "still": [f["summary"] for f in still], "open": [i["id"] for i in self.open()]}


def serve(*, gif=False, view=False, realtime=False):
    rec = RunRecorder(RUNS, task="arm host session", joints=JOINTS)
    arm = SimArm(recorder=rec, start=prims.REST)
    skills = Skills(arm)
    dog = Watchdog(arm, rec)
    if gif:
        from .render import Gif
        gif = Gif(arm, RUNS / f"{rec.run_id}.gif")
    viewer = None
    if view:
        import mujoco.viewer
        viewer = mujoco.viewer.launch_passive(arm.m, arm.d)
        arm.hooks.append(lambda a: viewer.sync())
    if view or realtime:
        clock = {"wall": time.monotonic(), "sim": arm.t}
        def pace(a):
            ahead = (a.t - clock["sim"]) - (time.monotonic() - clock["wall"])
            if ahead > 0:
                time.sleep(ahead)
        arm.hooks.append(pace)

    SOCK.parent.mkdir(parents=True, exist_ok=True)
    if SOCK.exists():
        SOCK.unlink()
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(str(SOCK))
    srv.listen(4)
    print(f"arm host listening on {SOCK} (run record {rec.path.name})", flush=True)

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
            result = getattr(skills, name)()
            reply = {"ok": True, "skill": name, "result": result, "t": round(arm.t, 2)}
            if result.get("halted"):
                reply.update(parked=skills.park(), incident=dog.open()[0] if dog.open() else None)
            return reply
        if op == "read":
            return {"ok": True, "pos": arm.read()}
        if op == "distance":
            return {"ok": True, "row": arm.distance()}
        if op == "status":
            return {"ok": True, "status": arm.status(), "grasp": arm.grasp_check(),
                    "incidents": [{k: i[k] for k in ("id", "kind", "summary")} for i in dog.open()]}
        if op == "incidents":
            return {"ok": True, "incidents": dog.incidents}
        if op == "repair":
            return dog.repair(req.get("action"))
        if op == "inject":
            return {"ok": True, "fault": arm.inject(req["fault"], **req.get("params", {}))}
        if op == "clear":
            arm.clear(req.get("fault"))
            return {"ok": True}
        if op == "shutdown":
            return {"ok": True, "bye": True}
        return {"ok": False, "error": f"unknown op {op!r}"}

    try:
        while True:
            conn, _ = srv.accept()
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
        rec.close("host stopped")
        if gif:
            print("gif:", gif.save(), flush=True)
        if viewer:
            viewer.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Run the simulated arm host.")
    ap.add_argument("--gif", action="store_true", help="save a GIF of the session in runs/")
    ap.add_argument("--view", action="store_true", help="open MuJoCo's viewer (on macOS, run with mjpython)")
    ap.add_argument("--realtime", action="store_true", help="pace the sim to wall-clock time")
    a = ap.parse_args()
    serve(gif=a.gif, view=a.view, realtime=a.realtime)
