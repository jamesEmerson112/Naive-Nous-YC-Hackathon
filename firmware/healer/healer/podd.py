"""podd: the pod daemon. It is the only process that opens the pod's serial port.

It
  - reads telemetry lines, parses them, keeps the recent ones in memory,
  - writes logs under var/logs/ (JSONL; one record per line, "ts" = host time):
        telemetry.jsonl  A, D, F lines         (rotated at 20 MB, 5 kept)
        health.jsonl     H lines               (rotated at 20 MB, 5 kept)
        events.jsonl     E, R and # lines, link changes, host notes, incidents
        serial.log       every raw line as received, including garbage
        incidents.jsonl  incident open/resolve records
  - runs the triage rules (detect.py) every few seconds and records incidents,
  - serves requests on a Unix socket (var/podd.sock), one JSON object per line:
        {"op": "status"} -> {"ok": true, ...}
    Ops: ping status cmd recent stats triage incidents raw baud_scan set_baud note.
"""
import argparse
import json
import os
import select
import signal
import socketserver
import threading
import time
from collections import deque

from . import config, detect
from .proto import decode_frame, parse_line
from .serialport import open_serial, resolve_port, set_baud

RING = {"A": 3000, "D": 600, "F": 300, "H": 900, "E": 2000, "R": 500, "#": 400}
LOG_OF = {"A": "telemetry", "D": "telemetry", "F": "telemetry", "H": "health",
          "E": "events", "R": "events", "#": "events"}
TRIAGE_PERIOD_S = 3.0
RESOLVE_AFTER_S = 10.0


def now():
    return time.time()


def iso(ts):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(ts)) + ".%03d" % int((ts % 1) * 1000)


class RotatingLog:
    def __init__(self, path, max_bytes=20_000_000, keep=5):
        self.path, self.max_bytes, self.keep = path, max_bytes, keep
        self.f = open(path, "a", buffering=1 << 16)

    def write(self, text):
        self.f.write(text)
        if self.f.tell() > self.max_bytes:
            self.f.close()
            for i in range(self.keep - 1, 0, -1):
                src = "%s.%d" % (self.path, i)
                if os.path.exists(src):
                    os.replace(src, "%s.%d" % (self.path, i + 1))
            os.replace(self.path, self.path + ".1")
            self.f = open(self.path, "a", buffering=1 << 16)

    def flush(self):
        self.f.flush()


class Pod:
    def __init__(self, port, baud):
        config.ensure_dirs()
        self.port_arg, self.baud = port, baud
        self.port = None
        self.fd = None
        self.port_error = None
        self.port_lock = threading.Lock()      # held while touching the fd
        self.data_lock = threading.RLock()     # rings, counters
        self.cmd_lock = threading.Lock()
        self.rings = {k: deque(maxlen=n) for k, n in RING.items()}
        self.raw = deque(maxlen=300)           # (ts, text) of every line, valid or not
        self.windows = deque()                 # (ts, nbytes, valid_lines, invalid_lines) per read
        self.last_byte_ts = 0.0
        self.last_line_ts = 0.0
        self.started = now()
        self.partial = b""
        self.next_id = 1000
        self.pending = {}                      # id -> {"lines": [], "done": Event}
        self.incidents = {}                    # kind -> incident dict (open ones)
        self.last_findings = []
        self.link_state = None
        self.logs = {name: RotatingLog(str(config.LOGS / (name + ".jsonl")))
                     for name in ("telemetry", "health", "events", "incidents")}
        self.serial_log = RotatingLog(str(config.LOGS / "serial.log"), 10_000_000, 3)
        self.running = True

    # ------------------------------------------------------------------ logging
    def log(self, name, rec):
        self.logs[name].write(json.dumps(rec, separators=(",", ":")) + "\n")

    def event(self, ev, **fields):
        rec = {"ts": now(), "type": "host", "ev": ev} | fields
        with self.data_lock:
            self.rings["E"].append(rec)
        self.log("events", rec)
        return rec

    # ------------------------------------------------------------------ serial
    def _open(self):
        port = resolve_port(self.port_arg)
        if not port:
            self.port_error = "no serial port found"
            return False
        try:
            self.fd = open_serial(port, self.baud)
            self.port, self.port_error = port, None
            self.partial = b""
            self.event("port_open", port=port, baud=self.baud)
            time.sleep(0.3)   # the probe's VCOM drops bytes written right after opening
            return True
        except OSError as e:
            self.port_error = str(e)
            return False

    def _close(self, why):
        if self.fd is not None:
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = None
            self.event("port_closed", reason=why)

    def reader_loop(self):
        last_flush = now()
        while self.running:
            with self.port_lock:
                if self.fd is None and not self._open():
                    data = None
                else:
                    data = b""
                    try:
                        r, _, _ = select.select([self.fd], [], [], 0.1)
                        if r:
                            data = os.read(self.fd, 4096)
                            if not data:
                                self._close("eof")
                    except OSError as e:
                        self._close("read error: %s" % e)
            if data is None:
                time.sleep(1.0)
                continue
            if data:
                self._ingest(data)
            if now() - last_flush > 0.5:
                for lg in self.logs.values():
                    lg.flush()
                self.serial_log.flush()
                last_flush = now()

    def _ingest(self, data):
        ts = now()
        self.last_byte_ts = ts
        buf = self.partial + data
        lines = buf.split(b"\n")
        self.partial = lines.pop()
        if len(self.partial) > 400:           # no newline for a long time: garbage
            lines.append(self.partial)
            self.partial = b""
        valid = invalid = 0
        for raw in lines:
            raw = raw.rstrip(b"\r")
            if not raw:
                continue
            text = raw.decode("ascii", "backslashreplace")
            self.serial_log.write("%s %s\n" % (iso(ts), text))
            clean = len(raw) <= 400 and all(32 <= c < 127 for c in raw)   # check bytes, not the escaped text
            rec = parse_line(text) if clean else None
            with self.data_lock:
                self.raw.append((ts, text))
            if rec is None:
                invalid += 1
                continue
            valid += 1
            self.last_line_ts = ts
            rec["ts"] = ts
            if rec["type"] == "A":
                rec["_len"] = len(text)
            self._route(rec)
        with self.data_lock:
            self.windows.append((ts, len(data), valid, invalid))
            while self.windows and self.windows[0][0] < ts - 30:
                self.windows.popleft()

    def _route(self, rec):
        t = rec["type"]
        with self.data_lock:
            self.rings[t].append(rec)
        out = {k: v for k, v in rec.items() if k != "_len"}
        self.log(LOG_OF[t], out)
        if t == "R":
            p = self.pending.get(rec.get("id"))
            if p is not None:
                p["lines"].append(out)
                if "end" in rec:
                    p["done"].set()

    # ------------------------------------------------------------------ requests
    def write(self, data):
        with self.port_lock:
            if self.fd is None:
                raise RuntimeError("serial port not open: %s" % (self.port_error or "unknown"))
            os.write(self.fd, data)

    def cmd(self, line, timeout=2.0):
        line = line.strip()
        if not line or "\n" in line or len(line) > 78:
            return {"ok": False, "error": "command must be one line, 1-78 characters"}
        with self.cmd_lock:
            self.next_id += 1
            cid = self.next_id
            p = {"lines": [], "done": threading.Event()}
            self.pending[cid] = p
            try:
                self.write(("\n@%d %s\n" % (cid, line)).encode())
                done = p["done"].wait(timeout)
            finally:
                self.pending.pop(cid, None)
        lines = [{k: v for k, v in r.items() if k not in ("type", "id")} for r in p["lines"]]
        end = lines.pop() if done and lines else None
        res = {"command": line, "responses": lines}
        if not done:
            res |= {"ok": False, "error": "no complete response within %.1f s (firmware busy, hung, resetting, "
                    "or the link is garbled)" % timeout}
        else:
            res |= {"ok": bool(end.get("ok")), "error": end.get("err")}
        return res

    def link_status(self):
        t = now()
        with self.data_lock:
            win = [w for w in self.windows if w[0] >= t - 3]
            win5 = [w for w in self.windows if w[0] >= t - 5]
            sample = [txt for ts, txt in list(self.raw)[-5:]]
        nbytes = sum(w[1] for w in win)
        valid = sum(w[2] for w in win)
        invalid = sum(w[3] for w in win)
        silent = t - (self.last_byte_ts or self.started)
        if self.fd is None:
            state = "down"
        elif silent > 3:
            state = "silent"
        elif valid + invalid and valid / (valid + invalid) < 0.5:
            state = "garbled"
        elif valid == 0 and nbytes:
            state = "garbled"
        else:
            state = "up"
        st = {"state": state, "port": self.port, "baud": self.baud, "error": self.port_error,
              "bytes_per_s": round(sum(w[1] for w in win5) / 5.0), "valid_ratio": (valid / (valid + invalid)) if valid + invalid else 0.0,
              "silent_s": round(silent, 1),
              "last_line_age_s": round(t - self.last_line_ts, 1) if self.last_line_ts else None,
              "uart_capacity_bytes_per_s": config.BAUD // 10}
        if state == "garbled":
            st["sample"] = sample
        return st

    def snapshot(self):
        t = now()
        with self.data_lock:
            snap = {"now": t,
                    "A": [r for r in self.rings["A"] if r["ts"] >= t - 6],
                    "D": [r for r in self.rings["D"] if r["ts"] >= t - 6],
                    "H": [r for r in self.rings["H"] if r["ts"] >= t - 30],
                    "E": [r for r in self.rings["E"] if r["ts"] >= t - 180 and r.get("type") == "E"]}
        snap["link"] = self.link_status()
        return snap

    def triage(self):
        findings = detect.run(self.snapshot())
        self.last_findings = findings
        return findings

    def triage_loop(self):
        while self.running:
            time.sleep(TRIAGE_PERIOD_S)
            if now() - self.started < 5:
                continue
            try:
                findings = self.triage()
            except Exception as e:
                self.event("triage_error", error=repr(e))
                continue
            t = now()
            seen = set()
            for f in findings:
                k = f["kind"]
                seen.add(k)
                inc = self.incidents.get(k)
                if inc is None:
                    inc = {"id": "%s-%d" % (k, int(t)), "kind": k, "opened": t, "last_seen": t,
                           "severity": f["severity"], "summary": f["summary"], "first_evidence": f["evidence"]}
                    self.incidents[k] = inc
                    rec = {"ts": t, "status": "open"} | inc
                    self.log("incidents", rec)
                    self.event("incident_open", id=inc["id"], kind=k, severity=f["severity"], summary=f["summary"])
                inc["last_seen"] = t
                inc["summary"] = f["summary"]
            for k in list(self.incidents):
                inc = self.incidents[k]
                if k not in seen and t - inc["last_seen"] > RESOLVE_AFTER_S:
                    del self.incidents[k]
                    self.log("incidents", {"ts": t, "status": "resolved", "id": inc["id"], "kind": k,
                                           "duration_s": round(t - inc["opened"], 1)})
                    self.event("incident_resolved", id=inc["id"], kind=k)
            ls = self.link_status()["state"]
            if ls != self.link_state:
                self.event("link_state", state=ls, previous=self.link_state)
                self.link_state = ls

    def stats(self, seconds=10):
        import math
        import statistics as stt
        t = now()
        with self.data_lock:
            A = [r for r in self.rings["A"] if r["ts"] >= t - seconds]
            D = [r for r in self.rings["D"] if r["ts"] >= t - seconds]
            H = [r for r in self.rings["H"] if r["ts"] >= t - seconds]
            F = [r for r in self.rings["F"] if r["ts"] >= t - seconds]
            E = [r for r in self.rings["E"] if r["ts"] >= t - seconds]
        out = {"window_s": seconds, "link": self.link_status()}

        def desc(xs):
            if not xs:
                return None
            return {"mean": round(stt.fmean(xs), 2), "std": round(stt.pstdev(xs), 2) if len(xs) > 1 else 0.0,
                    "min": min(xs), "max": max(xs)}

        if A:
            gaps = [b["t"] - a["t"] for a, b in zip(A, A[1:])]
            mags = [math.sqrt(a["x"] ** 2 + a["y"] ** 2 + a["z"] ** 2) for a in A]
            out["accel"] = {"n": len(A), "rate_hz": round(len(A) / seconds, 1),
                            "x": desc([a["x"] for a in A]), "y": desc([a["y"] for a in A]), "z": desc([a["z"] for a in A]),
                            "magnitude_mg": desc([round(m, 1) for m in mags]),
                            "status": {s: sum(1 for a in A if a.get("st") == s) for s in {a.get("st") for a in A}},
                            "mcu_dt_ms": desc(gaps) if gaps else None}
        else:
            out["accel"] = {"n": 0}
        if D:
            ok = [d["mm"] for d in D if d.get("st") == "ok"]
            out["distance"] = {"n": len(D), "rate_hz": round(len(D) / seconds, 1), "mm_ok": desc(ok),
                               "status": {s: sum(1 for d in D if d.get("st") == s) for s in {d.get("st") for d in D}}}
        else:
            out["distance"] = {"n": 0}
        out["health_latest"] = {k: v for k, v in H[-1].items()} if H else None
        out["health_n"] = len(H)
        if F:
            out["radio_frame_latest"] = decode_frame(F[-1]["hex"]) if "hex" in F[-1] else None
        out["frames_n"] = len(F)
        out["events"] = [{k: v for k, v in e.items()} for e in E][-20:]
        return out

    def recent(self, type_, seconds=10, limit=50):
        t = now()
        with self.data_lock:
            if type_ == "raw":
                rows = [{"ts": ts, "text": txt} for ts, txt in self.raw if ts >= t - seconds]
            else:
                rows = [{k: v for k, v in r.items() if k != "_len"} for r in self.rings.get(type_, []) if r["ts"] >= t - seconds]
        return rows[-limit:]

    def raw_capture(self, seconds=2.0, baud=None):
        """Read the port for a while at `baud` (default: current), bypassing the parser."""
        seconds = min(float(seconds), 10.0)
        with self.port_lock:
            if self.fd is None:
                return {"ok": False, "error": "serial port not open"}
            if baud and baud != self.baud:
                set_baud(self.fd, baud)
            buf = b""
            end = now() + seconds
            try:
                while now() < end:
                    r, _, _ = select.select([self.fd], [], [], 0.05)
                    if r:
                        buf += os.read(self.fd, 4096)
            finally:
                if baud and baud != self.baud:
                    set_baud(self.fd, self.baud)
        text = buf.decode("ascii", "backslashreplace")
        lines = [l.rstrip(b"\r").decode("ascii", "backslashreplace") for l in buf.split(b"\n") if l.strip()]
        valid = sum(1 for l in lines if l.isprintable() and parse_line(l))
        printable = sum(1 for c in buf if 32 <= c < 127 or c in (10, 13)) / len(buf) if buf else 0.0
        return {"ok": True, "baud": baud or self.baud, "seconds": seconds, "bytes": len(buf),
                "printable_ratio": round(printable, 3), "lines": len(lines), "valid_lines": valid,
                "valid_ratio": round(valid / len(lines), 3) if lines else 0.0, "text_head": text[:600]}

    def baud_scan(self):
        results = []
        for b in (115200, 57600, 230400, 38400, 19200, 9600):    # 460800 is beyond the probe's VCOM
            r = self.raw_capture(1.2, b)
            if not r.get("ok"):
                return r
            r.pop("text_head", None)
            results.append(r)
        best = max(results, key=lambda r: (r["valid_ratio"], r["printable_ratio"]))
        good = best["valid_ratio"] > 0.5
        return {"ok": True, "results": results, "best_baud": best["baud"] if good else None,
                "note": ("telemetry parses cleanly at %d baud (host expects %d): the MCU's UART runs at %.3fx "
                         "the intended rate" % (best["baud"], config.BAUD, best["baud"] / config.BAUD)) if good and best["baud"] != config.BAUD
                else ("telemetry parses at the expected baud" if good else "no baud rate produced valid telemetry")}

    def set_baud(self, baud):
        with self.port_lock:
            if self.fd is None:
                return {"ok": False, "error": "serial port not open"}
            set_baud(self.fd, baud)
            old, self.baud = self.baud, baud
        self.event("baud_changed", baud=baud, previous=old)
        return {"ok": True, "baud": baud, "previous": old}

    def status(self):
        with self.data_lock:
            boots = [e for e in self.rings["E"] if e.get("ev") == "boot"]
            H = list(self.rings["H"])
        return {"link": self.link_status(), "last_boot": boots[-1] if boots else None,
                "last_health": H[-1] if H else None,
                "health_age_s": round(now() - H[-1]["ts"], 1) if H else None,
                "open_incidents": list(self.incidents.values()), "uptime_s": round(now() - self.started)}

    def handle(self, req):
        op = req.get("op")
        if op == "ping":
            return {"ok": True}
        if op == "status":
            return {"ok": True} | self.status()
        if op == "cmd":
            return self.cmd(req["line"], float(req.get("timeout", 2.0)))
        if op == "recent":
            return {"ok": True, "rows": self.recent(req.get("kind", "E"), float(req.get("seconds", 10)), int(req.get("limit", 50)))}
        if op == "stats":
            return {"ok": True} | self.stats(float(req.get("seconds", 10)))
        if op == "triage":
            return {"ok": True, "findings": self.triage(), "link": self.link_status()}
        if op == "incidents":
            return {"ok": True, "open": list(self.incidents.values())}
        if op == "raw":
            return self.raw_capture(float(req.get("seconds", 2)), req.get("baud"))
        if op == "baud_scan":
            return self.baud_scan()
        if op == "set_baud":
            return self.set_baud(int(req["baud"]))
        if op == "note":
            return {"ok": True, "event": self.event(req.get("ev", "note"), **req.get("fields", {}))}
        return {"ok": False, "error": "unknown op %r" % op}


def serve(pod):
    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            line = self.rfile.readline()
            try:
                req = json.loads(line)
                resp = pod.handle(req)
            except Exception as e:
                resp = {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
            self.wfile.write((json.dumps(resp, default=str) + "\n").encode())

    if config.SOCK.exists():
        config.SOCK.unlink()
    srv = socketserver.ThreadingUnixStreamServer(str(config.SOCK), Handler)
    srv.daemon_threads = True
    return srv


def main(argv=None):
    ap = argparse.ArgumentParser(description="pod daemon: serial owner, logger, triage, request server")
    ap.add_argument("--port", default=config.PORT)
    ap.add_argument("--baud", type=int, default=config.BAUD)
    args = ap.parse_args(argv)

    pod = Pod(args.port, args.baud)
    try:
        from .client import call
        call("ping", timeout=1)
        raise SystemExit("podd is already running (%s)" % config.SOCK)
    except (ConnectionError, FileNotFoundError, OSError):
        pass
    srv = serve(pod)
    pod.event("podd_start", pid=os.getpid())
    threading.Thread(target=pod.reader_loop, daemon=True, name="reader").start()
    threading.Thread(target=pod.triage_loop, daemon=True, name="triage").start()

    def stop(*_):
        pod.running = False
        threading.Thread(target=srv.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    print("podd: serving %s, logs in %s" % (config.SOCK, config.LOGS), flush=True)
    srv.serve_forever()
    pod.event("podd_stop")
    for lg in pod.logs.values():
        lg.flush()
    if config.SOCK.exists():
        config.SOCK.unlink()


if __name__ == "__main__":
    main()
