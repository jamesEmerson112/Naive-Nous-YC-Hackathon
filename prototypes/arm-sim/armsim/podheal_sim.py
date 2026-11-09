"""Story A's stand-in for Olir's pod healer, for the sim only (it can't flash: no probe, no board).

Olir's real healer (`firmware/harness/agent.py watch`) wakes on a critical podd incident, diagnoses
it with the heal tools and, for bad image 02, rolls back with `fw_rollback`. Here the "rollback" is
the virtual pod rebooting into the good image, and this module writes the same records the real
tools write (firmware/healer/healer/tools.py, images.py), so podd, the edge bridge and Romulus run
for real downstream of it. Every record it writes carries `"sim": true`.

  podd events:    agent_note (the diagnosis), flash_start, flash_done      (through podd's note op)
  var/logs/audit.jsonl:   {ts, tool: fw_rollback, args, ok, duration_s}
  var/deployments.jsonl:  {ts, crc, result: ok, build_id, reason, backup, checks}
  var/images/<CRC>/meta.json for the known-good image (origin "golden", so the bridge reports
                  fw_rollback / ops rather than a code fix)
"""
import json
import threading
import time
from pathlib import Path

from .vpod import BUILDS, crc_of

TRIGGERS = ("clock_skew", "link_garbled", "us_mismatch")
WAKE_AFTER_S = 3.0          # the real harness debounces ~10 s (HARNESS.md); a short wait for the demo


class PodHealSim:
    def __init__(self, vpod, pod, var):
        self.vpod, self.pod, self.var = vpod, pod, Path(var)
        self.seen = {}
        self.busy = False
        self.log = []

    def start(self):
        self.pod.on_incidents.append(self._on_incidents)
        return self

    def _on_incidents(self, open_list):
        bad = [i for i in open_list if i.get("kind") in TRIGGERS and i.get("severity") == "critical"]
        if not bad or self.busy or self.vpod.image == "good":
            return
        first = self.seen.setdefault(bad[0]["id"], time.time())
        if time.time() - first < WAKE_AFTER_S:
            return
        self.busy = True
        threading.Thread(target=self._rollback, args=(bad[0],), name="podheal-sim", daemon=True).start()

    def _append(self, path, rec):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as f:
            f.write(json.dumps(rec, separators=(",", ":")) + "\n")

    def _rollback(self, inc):
        try:
            bad_build, good = self.vpod.build, BUILDS["good"]
            good_crc = crc_of(good)
            self.pod.note("agent_note", sim=True, text=(
                f"{inc['kind']}: {inc.get('summary', '')}. The running image ({bad_build}) is the newest; "
                f"the known-good image {good_crc} ran clean. Rolling back to known-good (sim stand-in for fw_rollback)."))
            meta = self.var / "images" / good_crc / "meta.json"
            if not meta.exists():
                meta.parent.mkdir(parents=True, exist_ok=True)
                meta.write_text(json.dumps({"crc": good_crc, "build_id": good, "version": good.split()[1],
                                            "status": "known-good", "origin": "golden", "created": time.time(),
                                            "notes": [], "sim": True}, indent=1))
            t0 = time.time()
            self.pod.note("flash_start", sim=True, image=good_crc, reason=f"rollback after {inc['kind']}")
            time.sleep(2.5)                         # SWD program + verify takes a few seconds
            self.vpod.flash("good")
            time.sleep(1.5)                         # the boot line with the expected CRC arrives
            self.pod.note("flash_done", sim=True, ok=True, crc=good_crc)
            checks = [{"check": "build_id_in_flash", "ok": True, "detail": good},
                      {"check": "boot_line_crc", "ok": True, "detail": "0x" + good_crc},
                      {"check": "watch_seconds", "ok": True, "detail": "health flowing, mode=normal (sim)"}]
            self._append(self.var / "deployments.jsonl",
                         {"ts": time.time(), "crc": good_crc, "result": "ok", "build_id": good,
                          "reason": f"rollback after {inc['kind']} (sim stand-in)", "backup": None,
                          "checks": checks, "sim": True})
            self._append(self.var / "logs" / "audit.jsonl",
                         {"ts": t0, "tool": "fw_rollback", "args": {"to": "known-good"}, "ok": True,
                          "duration_s": round(time.time() - t0, 2), "sim": True})
            self.log.append({"at": time.time(), "incident": inc["id"], "from": bad_build, "to": good})
            print(f"PODHEAL (sim) rolled back {bad_build} -> {good} after {inc['kind']}", flush=True)
        finally:
            self.busy = False
