"""The arm host: one long-lived process that owns the arm and runs skills on request.

It mirrors Olir's podd: newline-delimited JSON over a Unix socket, one request per line, one
reply per line. LeRobot's SO101Follower takes seconds and ~0.9 GB to start, so the real arm
also wants exactly one long-lived owner of the servo bus (note, "Traps").

    {"op": "skill", "name": "wave"}           -> {"ok": true, "skill": "wave", "result": {...}}
    {"op": "read"} / {"op": "distance"} / {"op": "status"} / {"op": "ping"}
    {"op": "inject", "fault": "us_frozen"} / {"op": "clear"}
"""
import argparse
import json
import socket
import time
from pathlib import Path

from . import prims
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
        r = self.arm.goto(self.p["rest"])
        self.arm.relax()
        return {**r, "torque": "off"}

    MENU = ("go_home", "reach", "grip", "release", "wave", "park")


def serve(*, gif=False, view=False, realtime=False):
    rec = RunRecorder(RUNS, task="arm host session", joints=JOINTS)
    arm = SimArm(recorder=rec, start=prims.REST)
    skills = Skills(arm)
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
            return {"ok": True, "skill": name, "result": getattr(skills, name)(), "t": round(arm.t, 2)}
        if op == "read":
            return {"ok": True, "pos": arm.read()}
        if op == "distance":
            return {"ok": True, "row": arm.distance()}
        if op == "status":
            return {"ok": True, "status": arm.status(), "grasp": arm.grasp_check(), "faults": arm.faults}
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
