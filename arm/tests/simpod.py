"""A simulated bench for the offline tests: the pod runs v1.0 or v1.7, and everything else follows from that.

Distance samples go through the real firmware arithmetic (the expressions from hcsr04.c and the v1.7
bug.patch, evaluated with C's 32-bit unsigned semantics), triage through the real detect.py rules, and
arm_test through the real arm/safe_replay.py main() with only its sensor swapped for these samples.
"""
import contextlib
import io
import json
import random
import shlex
import sys
import time
import types
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for p in (REPO / "arm", REPO / "firmware" / "harness", REPO / "firmware" / "healer"):
    sys.path.insert(0, str(p))

import arm_status  # noqa: E402
import safe_replay  # noqa: E402
from healer import detect  # noqa: E402

REST = [18.0, -108.0, 95.3, -101.7, 12.9, 1.3]       # the real arm's folded pose (arm_status.py state, Nov 7)
JOINTS = ["shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper"]


class FakeClock:
    """Simulated time for safe_replay: sleep() advances it, so an 8 s reflex or a 13 s wave takes no wall time."""

    def __init__(self):
        self.t = time.time()

    def time(self):
        return self.t

    def monotonic(self):
        return self.t

    def sleep(self, s):
        self.t += max(0.0, s)


class FakeArm:
    """Follows every command exactly. history keeps every commanded pose, in order."""

    def __init__(self, pose=None):
        self.pose = list(pose or REST)
        self.moves = 0
        self.history = []

    def get_observation(self):
        return {j + ".pos": v for j, v in zip(JOINTS, self.pose)}

    def send_action(self, action):
        self.pose = [float(action[j + ".pos"]) for j in JOINTS]
        self.moves += 1
        self.history.append(list(self.pose))
        return action

    def disconnect(self):
        pass

BAD_DIR = REPO / "firmware" / "demo" / "bad-images" / "07-us-dist-16bit"
GOOD, BAD = {"crc": "BAADB704", "version": "v1.0", "status": "known-good"}, {"crc": "A12774C3", "version": "v1.7",
                                                                               "status": "candidate"}
US_PER_MM = 2 / 0.343


def mm_v10(us):
    return (us * 343 + 1000) // 2000                       # out->dist_mm = (us * 343u + 1000u) / 2000u;


def mm_v17(us):
    return ((us * 343 + 1000) & 0xFFFF) // 2000            # (uint16_t)(us * 343u + 1000u) / 2000u;


class FakeSensor:
    def __init__(self, rows):
        now = time.time()
        self.rows = [(now - 0.1 * (len(rows) - i), r) for i, r in enumerate(rows)]
        self.source, self.error = "simulated pod", None

    def samples(self, seconds):
        return list(self.rows)

    def close(self):
        pass


class SimPod:
    target, timeout = "sim", 180

    def __init__(self, firmware="v1.7", distance_mm=900, seed=7, runs_dir=None):
        self.firmware, self.distance_mm = firmware, distance_mm
        self.arm = FakeArm()                               # used when a command names an --arm-port
        self.runs_dir = runs_dir
        self.workspace = firmware                          # a rollback flashes; it doesn't touch the source
        self.rng = random.Random(seed)
        self.deployments, self.notes, self.notified, self.calls, self.ops = [], [], [], [], []

    # ---------------------------------------------------------------- the pod's physics
    def d_rows(self, n=20):
        conv = mm_v17 if self.firmware == "v1.7" else mm_v10
        now = time.time()
        rows = []
        for i in range(n):
            us = round(self.distance_mm * US_PER_MM) + self.rng.randint(-12, 12)
            st = "ok" if 116 <= us <= 23300 else "out_of_range"
            rows.append({"type": "D", "t": 1000 + 100 * i, "mm": conv(us), "us": us, "st": st,
                         "ts": now - 0.1 * (n - i)})
        return rows

    def findings(self):
        return detect.run({"link": {"state": "up"}, "E": [], "H": [], "A": [], "D": self.d_rows(), "now": time.time()})

    def flash(self, image, reason):
        img = GOOD if image in ("known-good", GOOD["crc"]) else BAD
        self.firmware = img["version"]
        self.deployments.append({"ts": time.time(), "crc": img["crc"], "result": "ok", "reason": reason})
        return {"ok": True, "result": "ok", "image": dict(img),
                "checks": [{"check": c, "ok": True} for c in ("build_id_in_flash", "boot_event", "no_reboots",
                                                               "health_flowing", "normal_mode")],
                "triage_after": self.findings()}

    # ---------------------------------------------------------------- the workspace (the real source)
    def files(self):
        f = {"src/" + q.name: q.read_text() for q in sorted((REPO / "firmware" / "pod" / "src").iterdir())}
        if self.workspace == "v1.7":
            patch = (BAD_DIR / "bug.patch").read_text()
            old = [ln[1:] for ln in patch.splitlines() if ln.startswith("-") and not ln.startswith("---")]
            new = [ln[1:] for ln in patch.splitlines() if ln.startswith("+") and not ln.startswith("+++")]
            f["src/hcsr04.c"] = f["src/hcsr04.c"].replace(old[0] + "\n", "\n".join(new) + "\n")
        return f

    def src_read(self, path, start=1, end=None):
        text = self.files().get(path)
        if text is None:
            return {"ok": False, "error": "FileNotFoundError: %s" % path}
        lines = text.splitlines()
        start, end = max(1, int(start or 1)), min(len(lines), int(end or start + 199))
        return {"path": path, "total_lines": len(lines), "start": start, "end": end,
                "text": "\n".join("%5d  %s" % (i, lines[i - 1]) for i in range(start, end + 1))}

    def src_search(self, pattern, path=None):
        import re
        rx = re.compile(pattern)
        return {"hits": [{"path": p, "line": i, "text": ln} for p, t in self.files().items() if not path or p == path
                         for i, ln in enumerate(t.splitlines(), 1) if rx.search(ln)][:50]}

    def stats(self, seconds=10):
        rows = self.d_rows(max(10, int(float(seconds) * 10)))
        ok = [r["mm"] for r in rows if r["st"] == "ok"]
        return {"distance": {"n": len(rows), "rate_hz": 10.0, "status": {"ok": len(ok)},
                             "mm_ok": {"n": len(ok), "mean": round(sum(ok) / len(ok), 1), "min": min(ok), "max": max(ok)}
                             if ok else None},
                "accel": {"magnitude_mg": {"mean": 1076.0}}, "health_latest": self.health()}

    def health(self):
        return {"type": "H", "mode": "normal", "boots": 3, "streak": 0, "crashes": 0, "wdg": 0, "vdda": 3308,
                "acc": "ok", "us": "ok", "clk": 48000000, "tx_drop": 0}

    # ---------------------------------------------------------------- the Bench interface
    def heal(self, name, args):
        self.calls.append((name, args))
        img = GOOD if self.firmware == "v1.0" else BAD
        r = {
            "system_info": lambda: {"text": "BRIEFING"},
            "triage": lambda: {"findings": self.findings()},
            "incidents": lambda: {"open": [dict(f, id=f["kind"], first_evidence=f["evidence"]) for f in self.findings()
                                           if f["severity"] in ("warn", "critical")]},
            "status": lambda: {"link": {"state": "up", "baud": 115200, "bytes_per_s": 2500}, "last_health": self.health(),
                               "open_incidents": self.heal_json("incidents")["open"]},
            "telemetry_recent": lambda: {"rows": self.d_rows(min(int(args.get("limit", 10)), 40))
                                         if args.get("kind", "D") == "D" else []},
            "telemetry_stats": lambda: self.stats(args.get("seconds", 10)),
            "events": lambda: {"rows": [], "matched": 0},
            "pod_cmd": lambda: {"command": args.get("command"), "ok": True, "responses": (
                [{"result": "ok", "echo_mv": 3195, "vdda_mv": 3308, "saturated": 0}]
                if str(args.get("command", "")).startswith("us check") else [])},
            "src_read": lambda: self.src_read(args.get("path", ""), args.get("start", 1), args.get("end")),
            "src_search": lambda: self.src_search(args.get("pattern", ""), args.get("path")),
            "src_log": lambda: {"log": "6804c8f v1.7 us: tidy the distance conversion\n43566ce v1.0"},
            "src_diff": lambda: {"diff": (BAD_DIR / "bug.patch").read_text() if self.workspace == "v1.7" else ""},
            "fw_images": lambda: {"images": [dict(GOOD), dict(BAD)], "deployments": self.deployments},
            "chip_status": lambda: {"crc": img["crc"], "build_id": "pod " + img["version"], "running": True},
            "fw_rollback": lambda: self.flash(args.get("to", "known-good"), args.get("reason") or "rollback"),
            "fw_flash": lambda: self.flash(args["image"], args.get("reason")),
            "note": lambda: self.notes.append(args) or {"ok": True},
            "notify_human": lambda: self.notified.append(args) or {"ok": True, "recorded": args},
        }.get(name, lambda: {"ok": True})()
        return (1 if r.get("ok") is False else 0), json.dumps(r), "", 0.01

    def heal_json(self, name, args=None):
        return json.loads(self.heal(name, args or {})[1])

    def read(self, rel):
        if rel == "healer/tools.json":
            return (REPO / "firmware" / "healer" / "tools.json").read_text()
        if rel == "healer/var/deployments.jsonl":
            return "".join(json.dumps(d) + "\n" for d in self.deployments)
        return ""

    def sh(self, cmd, timeout=None):
        """The arm's commands: safe_replay.py (the job) and arm_status.py (state, report, list), run in-process
        on this pod's samples and the simulated arm."""
        if "arm/arm_status.py" in cmd:
            return self._status(shlex.split(cmd.split("arm/arm_status.py", 1)[1]))
        assert "arm/safe_replay.py" in cmd, cmd
        argv = shlex.split(cmd.split("arm/safe_replay.py", 1)[1])
        if self.runs_dir and "--runs-dir" not in argv:
            argv += ["--runs-dir", str(self.runs_dir)]
        out = io.StringIO()
        saved = (safe_replay.Ultrasonic, safe_replay.time, safe_replay.connect_arm, sys.argv)
        clock = FakeClock()
        safe_replay.Ultrasonic = lambda port=None: FakeSensor(self.d_rows(16))
        safe_replay.time = types.SimpleNamespace(sleep=clock.sleep, time=clock.time, monotonic=clock.monotonic,
                                                 localtime=time.localtime, strftime=time.strftime)
        safe_replay.connect_arm = lambda port, robot_id, max_step, units: self.arm
        sys.argv = ["safe_replay.py"] + argv
        try:
            with contextlib.redirect_stdout(out):
                code = safe_replay.main()
        finally:
            safe_replay.Ultrasonic, safe_replay.time, safe_replay.connect_arm, sys.argv = saved
        return code, out.getvalue(), "", 0.1

    def _status(self, argv):
        a = dict(zip(argv[1::2], argv[2::2])) if len(argv) > 1 else {}
        runs = Path(a.get("--runs-dir") or self.runs_dir or arm_status.runs_dir())
        if argv[0] == "state":
            out = {"ok": True, "robot_id": "simulated", "port": a.get("--arm-port"),
                   "joints": {j: {"position": round(v, 1), "torque_on": False} for j, v in zip(JOINTS, self.arm.pose)}}
        elif argv[0] == "report":
            try:
                out = arm_status.summarize(arm_status.load_run(runs, a.get("--run")))
            except SystemExit as e:
                return 1, str(e), "", 0.01
        else:
            out = {"runs": [json.loads(f.read_text()).get("run_id") for f in sorted(runs.glob("*.json"))]}
        return 0, json.dumps(out), "", 0.01

    def scenario(self, *words):
        self.ops.append(words)
        if words[0] == "reset":
            self.firmware = self.workspace = "v1.0"
            self.deployments.append({"ts": time.time(), "crc": GOOD["crc"], "result": "unverified",
                                     "reason": "operator reset to known-good"})
        elif words[0] == "start":
            self.firmware = self.workspace = "v1.7"
            self.deployments.append({"ts": time.time(), "crc": BAD["crc"], "result": "unverified",
                                     "reason": "operator deploy v1.7"})
        return 0, "", "", 0.0

    def clock(self):
        t = time.time()
        return t, time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(t))
