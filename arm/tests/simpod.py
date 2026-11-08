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

import safe_replay  # noqa: E402
from healer import detect  # noqa: E402

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

    def __init__(self, firmware="v1.7", distance_mm=900, seed=7):
        self.firmware, self.distance_mm = firmware, distance_mm
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
        """Only the arm's job is expected here: run safe_replay.main() on this pod's samples."""
        assert "arm/safe_replay.py" in cmd, cmd
        argv = shlex.split(cmd.split("arm/safe_replay.py", 1)[1])
        out, saved = io.StringIO(), (safe_replay.Ultrasonic, safe_replay.time, sys.argv)
        safe_replay.Ultrasonic = lambda port=None: FakeSensor(self.d_rows(16))
        safe_replay.time = types.SimpleNamespace(sleep=lambda s: None, time=time.time, monotonic=time.monotonic)
        sys.argv = ["safe_replay.py"] + argv
        try:
            with contextlib.redirect_stdout(out):
                code = safe_replay.main()
        finally:
            safe_replay.Ultrasonic, safe_replay.time, sys.argv = saved
        return code, out.getvalue(), "", 0.1

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
