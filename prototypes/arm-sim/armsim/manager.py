"""The worker's line to the manager: one escalation in, the run's answer out, inside a deadline
(docs/worker-manager-healer.md §1, "Timeouts"). The manager is a Hermes profile behind its API server.

    from armsim.manager import ManagerClient
    m = ManagerClient("http://<mac>:8642/p/manager", key=os.environ["ARMSIM_MANAGER_KEY"])
    text, why = m.ask(packet)       # text: the run's output, UNCHECKED (escalation.check_plan checks it)

    PYTHONPATH=. .venv/bin/python tests/test_manager.py       # against tests/fake_manager.py, no key

How one ask goes (the Runs API, docs/raw/hermes-llms-full.txt H:L39524-39575):
1. POST {url}/v1/runs with {"input": <packet as JSON text>, "session_id": <incident id>, "instructions",
   "conversation_history": []}. The Idempotency-Key header is the packet's incident id, so a retried POST
   gets the same run_id back (H:L39538). One session per incident, not per device, so no earlier incident
   rides along (H:L39545-39549). The empty history is caller-supplied history: "Runs requests with ...
   caller-supplied history instead execute delegation synchronously, returning the result in the original
   turn" (H:L39622-39628), so the run's output is the plan, not a "working on it" status line (inferred:
   that an empty list counts as caller-supplied; the fields may be combined, H:L39536, H:L39546-39548).
2. Poll GET {url}/v1/runs/{run_id} every poll_s (2 s) until status is completed: return its output.
3. All of it inside the packet's deadline_s (90 s by default), on the wall clock: each HTTP call runs in
   a thread joined for what is left, so a server that trickles bytes can't stretch it (urllib's timeout
   is per socket operation). 429 and 503 are retried after Retry-After (H:L39907, H:L39538-39542); any
   other HTTP error, a connection refused, or a failed / cancelled / interrupted / unknown run gives up at
   once, so the arm isn't held parked for nothing.
Giving up returns (None, why): the caller then runs today's scripted heal. A run that may still be going
(the deadline passed, or polling failed) is stopped first, best effort: POST /v1/runs/{run_id}/stop
(H:L39698-39703) with a 2 s budget, so an orphaned run doesn't hold one of the profile's
max_concurrent_runs slots (2 in config.manager.yaml) and 429 the next incident.

`ManagerClient.check_tools()` is the startup check for `--manager hermes` (armsim/worker.py make): GET
/v1/toolsets (H:L39794-39809) and refuse to start when the manager can do more than think: any enabled
toolset other than delegation and memory, or any arm_*, terminal, write_file or patch tool. A
/v1/toolsets that can't be read refuses too (fail closed).

`url` is the API server's base, with the profile prefix when the gateway multiplexes profiles
(`/p/manager`, H:L39846-39860). The key comes from the caller (the dispatcher reads $ARMSIM_MANAGER_KEY),
never from the command line.
"""
import importlib.util
import json
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INSTRUCTIONS = ("Use skill arm-incident-manager. The input is one arm-escalation/v1 packet. Reply with ONLY one "
                "arm-fixplan/v1 JSON object whose action is in the packet's allowed_actions.")
TERMINAL_BAD = ("failed", "cancelled", "interrupted", "unknown")    # unknown is not terminal, but won't finish
STOP_S = 2.0                # the best-effort stop of a run we gave up on (H:L39698-39703)
# The manager's tools: it only thinks. Its analysts are delegate_task children (delegation), memory is
# allowed; anything else that is enabled (terminal, file, browser, web, so101_arm, mcp-naive_board, ...)
# could act, and the so101_arm plugin's socket reaches the arm host in sim (hermes-plugin/so101-arm).
MANAGER_TOOLSETS = ("delegation", "memory")
BAD_TOOLS = ("terminal", "write_file", "patch")
ARM_TOOLSETS = ("so101_arm", "mcp-naive_board")
# The manager and the laptop's Ollama sit on the bench LAN: never send those calls through a proxy (inferred).
DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class _Deadline(TimeoutError):
    """An HTTP call ran past the wall-clock budget it was given."""


def toolset_problems(data):
    """What makes /v1/toolsets' answer (H:L39803-39807: a list of {name, enabled, tools}; a {"data": [...]}
    wrapper is tolerated) unsafe for the manager: a list of strings, [] when only delegation and memory
    are on and no arm_*, terminal, write_file or patch tool is exposed."""
    sets = data.get("data", data) if isinstance(data, dict) else data
    if not isinstance(sets, list) or not all(isinstance(t, dict) and t.get("name") for t in sets):
        return [f"/v1/toolsets didn't answer a list of toolsets ({str(data)[:120]})"]
    out = []
    for t in sets:
        if not t.get("enabled"):
            continue
        name, tools = str(t["name"]), [str(x) for x in t.get("tools") or []]
        if name not in MANAGER_TOOLSETS:
            out.append(f"toolset {name} is enabled" + (" (it reaches the arm or the board)" if name in ARM_TOOLSETS else ""))
        bad = [x for x in tools if x.startswith("arm_") or x in BAD_TOOLS]
        if bad:
            out.append(f"toolset {name} exposes {', '.join(sorted(bad))}")
    return out


class ManagerClient:
    def __init__(self, url, *, key=None, label="hermes", poll_s=2.0, request_timeout_s=10.0,
                 sleep=time.sleep, clock=time.time):
        self.url, self.key, self.label = url.rstrip("/"), key, label
        self.poll_s, self.request_timeout_s = poll_s, request_timeout_s
        self.sleep, self.clock = sleep, clock
        self.last = {}                    # the last ask: run_id, status, seconds, http codes, usage

    def _http(self, method, path, body=None, headers=None, timeout=10.0):
        """(status, headers, json-or-None) within `timeout` seconds on the wall clock. Raises OSError
        (refused, a socket timeout) like urllib does, and _Deadline (a TimeoutError) when the whole call
        ran past `timeout`: the call runs in a thread joined for that long, so a server that trickles
        bytes can't hold it (urllib's own timeout is per socket operation)."""
        req = urllib.request.Request(self.url + path, method=method, data=body,
                                     headers={"Accept": "application/json", **(headers or {})})
        if self.key:
            req.add_header("Authorization", f"Bearer {self.key}")
        budget = max(0.5, timeout)
        got = {}

        def call():
            try:
                try:
                    with DIRECT.open(req, timeout=budget) as resp:
                        raw, code, hdrs = resp.read(), resp.status, resp.headers
                except urllib.error.HTTPError as exc:
                    raw, code, hdrs = exc.read(), exc.code, exc.headers
                got["out"] = (raw, code, hdrs)
            except Exception as exc:  # noqa: BLE001 (handed to the caller's thread below)
                got["exc"] = exc

        th = threading.Thread(target=call, name=f"manager-{method.lower()}", daemon=True)
        th.start()
        th.join(budget)
        if th.is_alive():
            raise _Deadline(f"{method} {path} ran past {budget:.1f} s")
        if "exc" in got:
            if isinstance(got["exc"], OSError):
                raise got["exc"]
            raise OSError(f"{type(got['exc']).__name__}: {got['exc']}")
        raw, code, hdrs = got["out"]
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

    @staticmethod
    def _error_text(data):
        """The error message of a non-2xx JSON body: {"error": {"message": ...}} or {"error": "text"}."""
        e = data.get("error") if isinstance(data, dict) else None
        msg = e.get("message") if isinstance(e, dict) else e
        return str(msg) if msg else None

    def stop(self, run_id, *, timeout=STOP_S):
        """Best effort: POST /v1/runs/{run_id}/stop (H:L39698-39703; it answers {"status": "stopping"}).
        Returns the HTTP code, or why it couldn't be sent; never raises."""
        try:
            code, _, _ = self._http("POST", f"/v1/runs/{run_id}/stop", b"{}", {"Content-Type": "application/json"},
                                    timeout=timeout)
            return code
        except OSError as exc:
            return f"{type(exc).__name__}: {exc}"

    def check_tools(self, *, timeout=10.0):
        """(problems, why) from the manager's /v1/toolsets (see toolset_problems): [] when only delegation
        and memory are on; a list of what can act otherwise; None when /v1/toolsets can't be read (the
        caller fails closed: armsim/worker.py preflight)."""
        try:
            code, _, data = self._http("GET", "/v1/toolsets", timeout=timeout)
        except OSError as exc:
            return None, f"/v1/toolsets at {self.url} can't be read ({type(exc).__name__}: {exc})"
        if code != 200:
            msg = self._error_text(data)
            return None, f"/v1/toolsets at {self.url} answered HTTP {code}" + (f" ({msg})" if msg else "")
        bad = toolset_problems(data)
        if bad and bad[0].startswith("/v1/toolsets didn't answer"):
            return None, bad[0]
        return bad, ("; ".join(bad) if bad else "only " + " and ".join(MANAGER_TOOLSETS) + " are on")

    def ask(self, esc, *, deadline_s=None):
        """(output text, why) when the run completed; (None, why) when it didn't in time or failed."""
        t0 = self.clock()
        deadline = t0 + float(deadline_s or esc.get("deadline_s") or 90)
        iid = esc.get("incident_id")
        body = json.dumps({"input": json.dumps(esc), "session_id": iid, "instructions": INSTRUCTIONS,
                           "conversation_history": []}).encode()
        headers = {"Content-Type": "application/json", "Idempotency-Key": iid}
        self.last = {"label": self.label, "url": self.url, "incident_id": iid, "http": []}
        run_id = None

        def left():
            return deadline - self.clock()

        def gave_up(why, *, stop=True):
            if run_id and stop:                   # it may still be running: free its slot (best effort)
                self.last["stop"] = self.stop(run_id)
            self.last.update(seconds=round(self.clock() - t0, 2), why=why)
            return None, why

        def late():
            return f"no answer from the manager within its {deadline - t0:.0f} s deadline" + (
                f" (run {run_id})" if run_id else "")

        while run_id is None:
            if left() <= 0:
                return gave_up(f"the manager didn't take the run within {deadline - t0:.0f} s")
            try:
                code, hdrs, data = self._http("POST", "/v1/runs", body, headers,
                                              timeout=min(self.request_timeout_s, left()))
            except _Deadline:
                return gave_up(late())
            except OSError as exc:
                return gave_up(f"the manager at {self.url} isn't reachable ({type(exc).__name__}: {exc})")
            self.last["http"].append(code)
            if code in (429, 503):
                wait = min(self._retry_after(hdrs), max(0.0, left()))
                self.sleep(wait)
                continue
            if code not in (200, 201, 202) or not isinstance(data, dict) or not data.get("run_id"):
                msg = self._error_text(data)
                return gave_up(f"the manager refused the run: HTTP {code}" + (f" ({msg})" if msg else ""))
            run_id = data["run_id"]
        self.last["run_id"] = run_id
        while True:
            if left() <= 0:
                self.last["status"] = "running"
                return gave_up(late())
            try:
                code, hdrs, data = self._http("GET", f"/v1/runs/{run_id}", timeout=min(self.request_timeout_s, left()))
            except _Deadline:
                self.last["status"] = "running"
                return gave_up(late())
            except OSError as exc:
                return gave_up(f"lost the manager while polling run {run_id} ({type(exc).__name__}: {exc})")
            if code in (429, 503):
                self.sleep(min(self._retry_after(hdrs), max(0.0, left())))
                continue
            if code != 200 or not isinstance(data, dict):
                msg = self._error_text(data)
                return gave_up(f"polling run {run_id} failed: HTTP {code}" + (f" ({msg})" if msg else ""))
            status = data.get("status")
            self.last["status"] = status
            if status == "completed":
                self.last.update(seconds=round(self.clock() - t0, 2), usage=data.get("usage"),
                                 runtime=data.get("runtime"))
                return data.get("output") or "", f"run {run_id} completed in {self.clock() - t0:.1f} s"
            if status in TERMINAL_BAD:                # over (or unknown: stop answers 409 there, H:L39683-39684)
                return gave_up(f"run {run_id} ended {status}: {self._error_text(data) or 'no reason given'}", stop=False)
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
