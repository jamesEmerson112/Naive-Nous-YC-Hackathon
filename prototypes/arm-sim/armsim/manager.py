"""The worker's line to the manager: one escalation in, the run's answer out, inside a deadline
(docs/worker-manager-healer.md §1, "Timeouts"). The manager is a Hermes profile behind its API server.

    from armsim.manager import ManagerClient
    m = ManagerClient("http://<mac>:8642/p/manager", key=os.environ["ARMSIM_MANAGER_KEY"])
    text, why = m.ask(packet)       # text: the run's output, UNCHECKED (escalation.check_plan checks it)

    PYTHONPATH=. .venv/bin/python tests/test_manager.py       # against tests/fake_manager.py, no key

How one ask goes (the Runs API, docs/raw/hermes-llms-full.txt H:L39524-39575):
1. POST {url}/v1/runs with {"input": <packet as JSON text>, "session_id": <incident id>, "instructions"}.
   The Idempotency-Key header is the packet's incident id, so a retried POST gets the same run_id back
   (H:L39538). One session per incident, not per device, so no earlier incident rides along (H:L39545-39549).
2. Poll GET {url}/v1/runs/{run_id} every poll_s (2 s) until status is completed: return its output.
3. All of it inside the packet's deadline_s (90 s by default). 429 and 503 are retried after Retry-After
   (H:L39907, H:L39538-39542); any other HTTP error, a connection refused, or a failed / cancelled /
   interrupted / unknown run gives up at once, so the arm isn't held parked for nothing.
Giving up returns (None, why): the caller then runs today's scripted heal.

`url` is the API server's base, with the profile prefix when the gateway multiplexes profiles
(`/p/manager`, H:L39846-39860). The key comes from the caller (the dispatcher reads $ARMSIM_MANAGER_KEY),
never from the command line.
"""
import importlib.util
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INSTRUCTIONS = ("Use skill arm-incident-manager. The input is one arm-escalation/v1 packet. Reply with ONLY one "
                "arm-fixplan/v1 JSON object whose action is in the packet's allowed_actions.")
TERMINAL_BAD = ("failed", "cancelled", "interrupted", "unknown")    # unknown is not terminal, but won't finish
# The manager and the laptop's Ollama sit on the bench LAN: never send those calls through a proxy (inferred).
DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class ManagerClient:
    def __init__(self, url, *, key=None, label="hermes", poll_s=2.0, request_timeout_s=10.0,
                 sleep=time.sleep, clock=time.time):
        self.url, self.key, self.label = url.rstrip("/"), key, label
        self.poll_s, self.request_timeout_s = poll_s, request_timeout_s
        self.sleep, self.clock = sleep, clock
        self.last = {}                    # the last ask: run_id, status, seconds, http codes, usage

    def _http(self, method, path, body=None, headers=None, timeout=10.0):
        """(status, headers, json-or-None). Raises OSError (refused, timeout) like urllib does."""
        req = urllib.request.Request(self.url + path, method=method, data=body,
                                     headers={"Accept": "application/json", **(headers or {})})
        if self.key:
            req.add_header("Authorization", f"Bearer {self.key}")
        try:
            with DIRECT.open(req, timeout=max(0.5, timeout)) as resp:
                raw, code, hdrs = resp.read(), resp.status, resp.headers
        except urllib.error.HTTPError as exc:
            raw, code, hdrs = exc.read(), exc.code, exc.headers
        try:
            data = json.loads(raw) if raw else None
        except ValueError:
            data = None
        return code, hdrs, data

    @staticmethod
    def _retry_after(hdrs, default=2.0):
        try:
            return max(0.2, float((hdrs or {}).get("Retry-After") or default))
        except (TypeError, ValueError):
            return default

    def ask(self, esc, *, deadline_s=None):
        """(output text, why) when the run completed; (None, why) when it didn't in time or failed."""
        t0 = self.clock()
        deadline = t0 + float(deadline_s or esc.get("deadline_s") or 90)
        iid = esc.get("incident_id")
        body = json.dumps({"input": json.dumps(esc), "session_id": iid, "instructions": INSTRUCTIONS}).encode()
        headers = {"Content-Type": "application/json", "Idempotency-Key": iid}
        self.last = {"label": self.label, "url": self.url, "incident_id": iid, "http": []}

        def left():
            return deadline - self.clock()

        def gave_up(why):
            self.last.update(seconds=round(self.clock() - t0, 2), why=why)
            return None, why

        run_id = None
        while run_id is None:
            if left() <= 0:
                return gave_up(f"the manager didn't take the run within {deadline - t0:.0f} s")
            try:
                code, hdrs, data = self._http("POST", "/v1/runs", body, headers,
                                              timeout=min(self.request_timeout_s, left()))
            except OSError as exc:
                return gave_up(f"the manager at {self.url} isn't reachable ({type(exc).__name__}: {exc})")
            self.last["http"].append(code)
            if code in (429, 503):
                wait = min(self._retry_after(hdrs), max(0.0, left()))
                self.sleep(wait)
                continue
            if code not in (200, 201, 202) or not isinstance(data, dict) or not data.get("run_id"):
                msg = ((data or {}).get("error") or {}).get("message") if isinstance(data, dict) else None
                return gave_up(f"the manager refused the run: HTTP {code}" + (f" ({msg})" if msg else ""))
            run_id = data["run_id"]
        self.last["run_id"] = run_id
        while True:
            if left() <= 0:
                self.last["status"] = "running"
                return gave_up(f"no answer from the manager within its {deadline - t0:.0f} s deadline (run {run_id})")
            try:
                code, hdrs, data = self._http("GET", f"/v1/runs/{run_id}", timeout=min(self.request_timeout_s, left()))
            except OSError as exc:
                return gave_up(f"lost the manager while polling run {run_id} ({type(exc).__name__}: {exc})")
            if code in (429, 503):
                self.sleep(min(self._retry_after(hdrs), max(0.0, left())))
                continue
            if code != 200 or not isinstance(data, dict):
                return gave_up(f"polling run {run_id} failed: HTTP {code}")
            status = data.get("status")
            self.last["status"] = status
            if status == "completed":
                self.last.update(seconds=round(self.clock() - t0, 2), usage=data.get("usage"),
                                 runtime=data.get("runtime"))
                return data.get("output") or "", f"run {run_id} completed in {self.clock() - t0:.1f} s"
            if status in TERMINAL_BAD:
                return gave_up(f"run {run_id} ended {status}: {data.get('error') or 'no reason given'}")
            self.sleep(min(self.poll_s, max(0.0, left())))


def load_fake():
    """tests/fake_manager.py, loaded by path (tests/ is not a package)."""
    path = ROOT / "tests" / "fake_manager.py"
    if not path.exists():
        raise SystemExit(f"--manager fake needs {path} (the fake Hermes API server)")
    spec = importlib.util.spec_from_file_location("armsim_fake_manager", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def fake_client(mode="good", **opts):
    """A ManagerClient wired to a fake Hermes API server in this process: (client, server or None)."""
    srv, url = load_fake().start(mode, **opts)
    return ManagerClient(url, label=f"fake-{mode}" if mode != "good" else "fake", poll_s=0.5), srv


def main():
    """python -m armsim.manager ask PACKET.json: one real run on a saved packet, checked and timed.
    The packet gets a fresh incident id (an Idempotency-Key is kept 24 h, and the same key with another
    body answers 409, H:L39538), so the same sample can be asked again."""
    import argparse
    import os
    import uuid
    from .escalation import check_plan, parse_plan
    ap = argparse.ArgumentParser(description="Ask the manager about one saved arm-escalation/v1 packet.")
    ap.add_argument("cmd", choices=["ask"])
    ap.add_argument("packet")
    ap.add_argument("--url", default=os.environ.get("ARMSIM_MANAGER_URL", "http://127.0.0.1:8642"))
    ap.add_argument("--deadline", type=float, default=None, help="seconds (default: the packet's deadline_s)")
    ap.add_argument("--fake", default=None, help="ask tests/fake_manager.py in this mode instead (no Hermes)")
    a = ap.parse_args()
    esc = json.loads(Path(a.packet).read_text())
    esc["incident_id"] = f"inc-manual-{uuid.uuid4().hex[:12]}"
    if a.fake:
        client, _srv = fake_client(a.fake)
    else:
        client = ManagerClient(a.url, key=os.environ.get("ARMSIM_MANAGER_KEY"), label="hermes")
    t0 = time.time()
    text, why = client.ask(esc, deadline_s=a.deadline)
    print(f"{why} ({time.time() - t0:.1f} s; run {client.last.get('run_id')}, http {client.last.get('http')}, "
          f"usage {client.last.get('usage')}, runtime {client.last.get('runtime')})")
    if text is None:
        raise SystemExit(1)
    print("output:", text)
    obj, _ = parse_plan(text)
    ok, reason = check_plan(text, esc, {"incident_open": True, "halted": True, "now": time.time(), "asked_at": t0})
    print(f"check_plan: {'ok' if ok else 'REFUSED: ' + reason}" + (f" -> {obj['action']}" if ok else ""))
    raise SystemExit(0 if ok else 2)


if __name__ == "__main__":
    main()
