"""A fake Hermes API server for the worker/manager loop: POST /v1/runs, GET /v1/runs/{id}, GET /health,
the shapes of docs/raw/hermes-llms-full.txt H:L39524-39575. Standard library only, no Hermes, no model.

    from fake_manager import start
    srv, url = start("good")            # http://127.0.0.1:<free port>; srv.requests records every call
    ...
    srv.shutdown()

    python tests/fake_manager.py --mode good --port 8642     # as a process (Ctrl-C stops it)

`python -m armsim.dispatch --manager fake --fake-mode <mode>` loads this file by path and runs it in a
thread, so the dispatcher's ManagerClient talks to it over real HTTP.

Modes (what the "manager" answers):
  good      after `delay` s (default 0.3), one arm-fixplan/v1 JSON built from the packet: the first
            entry of its allowed_actions, like a manager that follows the allowlist; a redo_step goes
            from the approach with the packet's first correction (relocate), else from the failed step
  bad_json  prose with a broken JSON object
  off_list  a schema-valid plan whose action is NOT in the packet's allowed_actions
  unsafe    a valid plan with touches_safety: true
  slow      the run never finishes (status "running" forever)
  429       the first `n429` POSTs (default 2) get HTTP 429 with Retry-After: 1, then like good
  500       every POST gets HTTP 500
  failed    the run ends with status "failed"
  refused   no server at all: start() returns the URL of a port nothing listens on
  redo_offset  like good, but a redo_step carries the bounded offset correction dx_mm=30, dy_mm=0 (a
            manager's own guess at where the block went; category 2, armsim/redo.py)
Like the real server: a Bearer key when `key` is set (else 401), and Idempotency-Key replay (the same
key and body answer the same run_id with 202 and Idempotency-Replayed: true; a different body 409).
"""
import argparse
import json
import socket
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODES = ("good", "bad_json", "off_list", "unsafe", "slow", "429", "500", "failed", "refused", "redo_offset")
ACTIONS = ("restart_sensor", "verify", "pod_rollback", "redo_step", "park", "handoff_human", "no_action")


def good_plan(esc):
    """What a manager that follows the allowlist answers: the first allowed action."""
    allowed = esc.get("allowed_actions") or ["park"]
    action = allowed[0]
    args = {}
    if action == "redo_step":
        state = esc.get("state") or {}
        steps, failed = (state.get("job") or {}).get("steps") or [], (state.get("job") or {}).get("failed_step")
        corrections = [c for c in state.get("corrections") or [] if c != "none"]
        if corrections and "approach" in steps and failed in steps and steps.index("approach") <= steps.index(failed):
            args = {"from_step": "approach", "correction": corrections[0]}      # relocate, as plain code offers it
        else:
            args = {"from_step": failed or "reach"}
    kind = (esc.get("findings") or [{}])[0].get("kind") or "other"
    return {"schema": "arm-fixplan/v1", "incident_id": esc.get("incident_id"), "action": action, "args": args,
            "kind": kind[:40], "confidence": 0.8, "touches_safety": False,
            "why": f"fake manager: {kind} maps to {action}, the first action plain code allows",
            "expect": "the redone grip holds the block" if action == "redo_step" else "the check at home passes"}


def answer(mode, esc):
    if mode == "bad_json":
        return 'Sure! Here is the fix plan: {"schema": "arm-fixplan/v1", "action": "restart_sensor", "args": '
    plan = good_plan(esc)
    if mode == "off_list":
        plan["action"] = next(a for a in ACTIONS if a not in (esc.get("allowed_actions") or []) and a != "redo_step")
        plan["why"] = "fake manager (off_list): an action this incident does not allow"
    if mode == "redo_offset" and plan["action"] == "redo_step":
        plan["args"] = {"from_step": "approach", "correction": "offset", "dx_mm": 30, "dy_mm": 0}
        plan["why"] = "fake manager (redo_offset): the block likely slid 30 mm away from the base; shift the approach"
    if mode == "unsafe":
        plan["touches_safety"] = True
        plan["why"] = "fake manager (unsafe): a plan that says it touches the safety layer"
    return json.dumps(plan)


class FakeHermes(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, addr, *, mode="good", delay=0.3, key=None, n429=2):
        super().__init__(addr, Handler)
        self.mode, self.delay, self.key, self.n429 = mode, delay, key, n429
        self.requests, self.runs, self.idem, self.lock = [], {}, {}, threading.Lock()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server_address[1]}"


class Handler(BaseHTTPRequestHandler):
    server: FakeHermes

    def log_message(self, fmt, *args):          # quiet
        pass

    def _send(self, code, obj, headers=None):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _authed(self):
        if self.server.key and self.headers.get("Authorization") != f"Bearer {self.server.key}":
            self._send(401, {"error": {"message": "invalid API key", "code": "unauthorized"}})
            return False
        return True

    def do_GET(self):
        s = self.server
        s.requests.append({"method": "GET", "path": self.path, "at": time.time()})
        if self.path in ("/health", "/v1/health"):
            return self._send(200, {"status": "ok"})
        if not self._authed():
            return None
        if self.path == "/v1/capabilities":        # what hermes-manager/check.sh reads (H:L39406, H:L39796)
            return self._send(200, {"object": "hermes.api_server.capabilities", "platform": "hermes-agent (fake)",
                                    "features": {"run_submission": True, "run_status": True}})
        if self.path == "/v1/toolsets":            # a manager with its tools off: delegation and memory only
            return self._send(200, [{"name": "delegation", "enabled": True, "tools": ["delegate_task"]},
                                    {"name": "memory", "enabled": True, "tools": ["memory"]}])
        if self.path == "/v1/skills":
            return self._send(200, [{"name": "arm-incident-manager", "description": "fake", "category": "ops"}])
        if self.path.startswith("/v1/runs/"):
            run = s.runs.get(self.path.rsplit("/", 1)[-1])
            if not run:
                return self._send(404, {"error": {"message": "run not found", "code": "not_found"}})
            out = {"object": "hermes.run", "run_id": run["id"], "session_id": run.get("session_id"),
                   "model": "hermes-agent", "status": "running"}
            if s.mode != "slow" and time.time() - run["created"] >= s.delay:
                if s.mode == "failed":
                    out.update(status="failed", error="fake manager: the run failed")
                else:
                    out.update(status="completed", output=answer(s.mode, run["esc"]),
                               usage={"input_tokens": 900, "output_tokens": 120, "total_tokens": 1020},
                               runtime={"provider": "fake", "model": "openai/gpt-6-sol", "route_source": "global"})
            return self._send(200, out)
        return self._send(404, {"error": {"message": f"no route {self.path}"}})

    def do_POST(self):
        s = self.server
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        rec = {"method": "POST", "path": self.path, "at": time.time(), "headers": dict(self.headers), "body": raw}
        s.requests.append(rec)
        if self.path != "/v1/runs":
            return self._send(404, {"error": {"message": f"no route {self.path}"}})
        if not self._authed():
            return None
        if s.mode == "500":
            return self._send(500, {"error": {"message": "fake manager: internal error"}})
        with s.lock:
            if s.mode == "429" and s.n429 > 0:
                s.n429 -= 1
                return self._send(429, {"error": {"message": "Too many concurrent runs (max 2)"}},
                                  {"Retry-After": "1"})
            key = self.headers.get("Idempotency-Key")
            if key and key in s.idem:
                run_id, body = s.idem[key]
                if body != raw:
                    return self._send(409, {"error": {"message": "key reused with another body",
                                                      "code": "idempotency_key_conflict"}})
                return self._send(202, {"run_id": run_id, "status": "started"}, {"Idempotency-Replayed": "true"})
            try:
                req = json.loads(raw)
                esc = json.loads(req["input"])
            except (ValueError, KeyError, TypeError):
                return self._send(400, {"error": {"message": "input must be the escalation as a JSON string"}})
            run_id = "run_" + uuid.uuid4().hex[:12]
            s.runs[run_id] = {"id": run_id, "created": time.time(), "esc": esc, "session_id": req.get("session_id")}
            if key:
                s.idem[key] = (run_id, raw)
        return self._send(202, {"run_id": run_id, "status": "started"})


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def start(mode="good", *, port=0, delay=0.3, key=None, n429=2):
    """Serve in a daemon thread; returns (server, base URL). For "refused", (None, a URL nobody serves)."""
    if mode not in MODES:
        raise ValueError(f"unknown fake manager mode {mode!r}; modes: {', '.join(MODES)}")
    if mode == "refused":
        return None, f"http://127.0.0.1:{free_port()}"
    srv = FakeHermes(("127.0.0.1", port), mode=mode, delay=delay, key=key, n429=n429)
    threading.Thread(target=srv.serve_forever, name=f"fake-manager-{mode}", daemon=True).start()
    return srv, srv.url


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="A fake Hermes API server (/v1/runs) for the worker/manager loop.")
    ap.add_argument("--mode", choices=MODES, default="good")
    ap.add_argument("--port", type=int, default=8642)
    ap.add_argument("--delay", type=float, default=0.3, help="seconds before a run completes")
    ap.add_argument("--key", default=None, help="require this Bearer key")
    a = ap.parse_args()
    srv, url = start(a.mode, port=a.port, delay=a.delay, key=a.key)
    print(f"fake manager ({a.mode}) on {url}", flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        if srv:
            srv.shutdown()
