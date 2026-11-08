"""Mission Control (armsim/mission.py): the HTTP server on a free port, in this process, with its own
temp snapshot, file board and event log (Romulus is a dead port). The last test starts a real sim
arm host and drives it through the API. Run from prototypes/arm-sim:

    PYTHONPATH=. .venv/bin/python tests/test_mission.py      (or: python -m pytest tests/)

The sim host writes its run record to runs/ (host.RUNS is fixed); everything else stays in a temp dir.
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from armsim import client, events, mission, orders  # noqa: E402
from armsim.board import FileBoard                  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="armsim-mission-test-"))
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # never through a proxy


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


DEAD_ROMULUS = f"http://127.0.0.1:{free_port()}"


class Server:
    """Mission Control on a free port with its own snapshot, board, event log and host socket path.
    Restores events.PATH and the env on exit, so other test files keep theirs."""

    def __init__(self, name, host="127.0.0.1", token=None):
        self.dir = TMP / f"{name}-{time.time_ns()}"
        self.dir.mkdir(parents=True)
        self.host, self.token = host, token
        self.state, self.events = self.dir / "state.json", self.dir / "events.jsonl"

    def __enter__(self):
        self.saved = events.PATH, {k: os.environ.get(k) for k in ("ARMSIM_STATE", "ARMSIM_SOCK")}
        events.PATH = self.events
        os.environ.update(ARMSIM_STATE=str(self.state), ARMSIM_SOCK=str(self.dir / "a.sock"))
        self.m = mission.Mission(board_kind="file", board_id="brd_test", romulus=DEAD_ROMULUS)
        self.board = self.m.board.board = FileBoard(self.dir / "board.json", board_id="brd_test")
        self.srv, self.token = mission.make_server(self.m, self.host, 0, self.token)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *exc):
        self.srv.shutdown()
        self.srv.server_close()
        events.PATH, env = self.saved
        for k, v in env.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)

    def url(self, path, at="127.0.0.1"):
        return f"http://{at}:{self.port}{path}"

    def presenter_idle(self, timeout=30):
        t0 = time.time()
        while self.m.presenter.get()["pending"] and time.time() - t0 < timeout:
            time.sleep(0.05)
        return self.m.presenter.get()


def http(url, body=None, *, raw=None, headers=None, method=None):
    """(status, decoded JSON or text). A body is sent as application/json unless headers say otherwise."""
    data = raw if raw is not None else None if body is None else json.dumps(body).encode()
    h = {"Content-Type": "application/json"} if data is not None else {}
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=h, method=method or ("POST" if data is not None else "GET"))
    try:
        with OPENER.open(req, timeout=15) as r:
            code, out = r.status, r.read()
    except urllib.error.HTTPError as e:
        code, out = e.code, e.read()
    try:
        return code, json.loads(out)
    except ValueError:
        return code, out.decode("utf-8", "replace")


def raw_http(port, text, timeout=8):
    """Send raw request bytes; return (whole reply, seconds until the server closed)."""
    t0 = time.time()
    with socket.create_connection(("127.0.0.1", port), timeout=timeout) as s:
        s.sendall(text.encode())
        buf = b""
        while chunk := s.recv(65536):
            buf += chunk
    return buf.decode("utf-8", "replace"), time.time() - t0


def presenter_events(path, action=None):
    return [e for e in events.read(path, kinds={"presenter"}) if action is None or e.get("action") == action]


# ---- GET /api/state with nothing running ---------------------------------------------------------
def test_state_with_every_source_missing_still_answers():
    with Server("empty") as s:
        code, st = http(s.url("/api/state"))
        assert code == 200, (code, st)
        assert set(st) >= {"now", "arm", "board", "events", "romulus", "derived", "presenter", "sources"}, set(st)
        assert "no arm snapshot yet" in st["arm"]["error"], st["arm"]
        assert st["board"] == {"kind": "file", "id": "brd_test", "cards": []}, st["board"]
        assert st["events"] == []
        assert st["romulus"]["url"] == DEAD_ROMULUS and ("error" in st["romulus"] or "errors" in st["romulus"])
        assert st["derived"]["incidents"] == [] and st["derived"]["jobs"] == [] and st["derived"]["feed"] == []
        assert st["derived"]["summary"]["open"] == 0, st["derived"]["summary"]
        assert st["presenter"] == {"pending": 0, "last": None}
        assert st["sources"]["state"] == str(s.state)

        s.state.write_text("[1, 2, 3]")                     # a snapshot that isn't an object
        code, st = http(s.url("/api/state"))
        assert code == 200 and "isn't a JSON object" in st["arm"]["error"], st["arm"]
        assert "summary" in st["derived"], st["derived"]
        s.state.write_text(json.dumps({"ts": "soon", "pid": 1}))
        code, arm = http(s.url("/api/arm"))
        assert code == 200 and "no usable ts" in arm["error"], arm
        s.state.write_text("{not json")
        code, arm = http(s.url("/api/arm"))
        assert code == 200 and "can't read" in arm["error"], arm


# ---- the POST guards -----------------------------------------------------------------------------
def test_post_needs_json_content_type():
    with Server("ctype") as s:
        for ctype in ("text/plain", "application/x-www-form-urlencoded", "multipart/form-data; boundary=x"):
            code, r = http(s.url("/api/order"), raw=b'{"job": "wave"}', headers={"Content-Type": ctype})
            assert code == 415 and not r["ok"], (ctype, code, r)
        reply, _ = raw_http(s.port, f"POST /api/clear HTTP/1.1\r\nHost: 127.0.0.1:{s.port}\r\n"
                                    "Content-Length: 0\r\n\r\n")   # no body and no type at all
        assert reply.startswith("HTTP/1.0 415"), reply[:80]
        code, r = http(s.url("/api/order"), {"job": "wave"}, headers={"Content-Type": "application/json; charset=utf-8"})
        assert code == 202 and r["ok"], (code, r)
        s.presenter_idle()
        assert len(s.board.show()) == 1


def test_bad_origin_and_bad_host_are_refused():
    with Server("origin") as s:
        for headers in ({"Origin": "http://evil.example"}, {"Origin": "null"},
                        {"Origin": f"http://localhost:{s.port + 1}"}, {"Origin": f"https://localhost:{s.port}"},
                        {"Host": f"evil.example:{s.port}"}, {"Host": f"localhost:{s.port + 1}"},
                        {"Host": "localhost:notaport"}):
            code, r = http(s.url("/api/freeze"), {}, headers=headers)
            assert code == 403 and not r["ok"], (headers, code, r)
        code, r = http(s.url("/api/state"), headers={"Host": f"rebound.example:{s.port}"})   # DNS rebinding, reading
        assert code == 403, (code, r)
        assert presenter_events(s.events) == []             # nothing refused reached the presenter
        for origin in (f"http://localhost:{s.port}", f"http://127.0.0.1:{s.port}"):   # the page itself
            code, r = http(s.url("/api/order"), {"job": "wave"}, headers={"Origin": origin})
            assert code == 202, (origin, code, r)
            s.presenter_idle()
        code, page = http(s.url("/"), headers={"Host": f"localhost:{s.port}"})
        assert code == 200 and "<html" in page.lower() and "mission-token" not in page


def test_order_needs_an_explicit_job():
    with Server("job") as s:
        for body in ({}, {"job": ""}, {"job": "dance"}, {"job": ["pick"]}):
            code, r = http(s.url("/api/order"), body)
            assert code == 400 and not r["ok"], (body, code, r)
        code, r = http(s.url("/api/order"), raw=b"")
        assert code == 400 and "say which job" in r["error"], (code, r)
        code, r = http(s.url("/api/fault"), {"fault": ["us_frozen"]})
        assert code == 400, (code, r)
        assert s.board.show() == [] and presenter_events(s.events) == []


def test_wildcard_bind_needs_the_token():
    with Server("token", host="0.0.0.0") as s:
        assert s.token, "a 0.0.0.0 bind must make up a token"
        code, r = http(s.url("/api/order"), {"job": "wave"})
        assert code == 403 and "X-Mission-Token" in r["error"], (code, r)
        code, r = http(s.url("/api/order"), {"job": "wave"}, headers={"X-Mission-Token": s.token + "x"})
        assert code == 403, (code, r)
        code, r = http(s.url("/api/order"), {"job": "wave"}, headers={"X-Mission-Token": s.token})
        assert code == 202, (code, r)
        s.presenter_idle()
        assert len(s.board.show()) == 1
        code, page = http(s.url("/"))                        # a browser on this machine gets the token
        assert code == 200 and f'<meta name="mission-token" content="{s.token}">' in page

        ip = mission.lan_ip()                                # another machine: the IP, and no token in the page
        if not ip.startswith("<") and not mission.is_loopback(ip):
            code, page = http(s.url("/", at=ip))
            assert code == 200 and "mission-token" not in page, (ip, code)
            code, r = http(s.url("/api/freeze", at=ip), {})
            assert code == 403 and "X-Mission-Token" in r["error"], (code, r)
            code, r = http(s.url("/api/order", at=ip), {"job": "pick"}, headers={"X-Mission-Token": s.token})
            assert code == 202, (code, r)
            s.presenter_idle()
            assert len(s.board.show()) == 2
        else:
            print(f"   (no LAN address to test the remote path: {ip})")
        code, r = http(s.url("/api/order"), {"job": "wave"},
                       headers={"X-Mission-Token": s.token, "Host": f"evil.example:{s.port}"})
        assert code == 403, (code, r)


def test_loopback_with_a_token_given_needs_it_too():
    with Server("loop-token", token="s3cret") as s:
        code, r = http(s.url("/api/clear"), {})
        assert code == 403, (code, r)
        code, page = http(s.url("/"))
        assert '<meta name="mission-token" content="s3cret">' in page
        code, r = http(s.url("/api/order"), {"job": "wave"}, headers={"X-Mission-Token": "s3cret"})
        assert code == 202, (code, r)
        s.presenter_idle()


# ---- Content-Length --------------------------------------------------------------------------------
def test_content_length_out_of_range_is_400_at_once_and_a_stalled_body_times_out():
    with Server("length") as s:
        assert s.srv.RequestHandlerClass.timeout == 10
        head = f"POST /api/order HTTP/1.1\r\nHost: 127.0.0.1:{s.port}\r\nContent-Type: application/json\r\n"
        for n in (-1, 999999, 65537):
            reply, took = raw_http(s.port, head + f"Content-Length: {n}\r\n\r\n")
            assert reply.startswith("HTTP/1.0 400") and took < 2, (n, reply[:80], took)
        reply, took = raw_http(s.port, head + "Content-Length: twelve\r\n\r\n")
        assert reply.startswith("HTTP/1.0 400"), reply[:80]

        s.srv.RequestHandlerClass.timeout = 1                # not 10 s in a test
        reply, took = raw_http(s.port, head + 'Content-Length: 50\r\n\r\n{"job"')
        assert took < 4 and reply.split(" ")[1] in ("400", "500"), (reply[:80], took)
        assert http(s.url("/api/state"))[0] == 200          # and the server still answers
        assert s.board.show() == []


# ---- a double click --------------------------------------------------------------------------------
def test_same_post_while_pending_is_dropped():
    gate, real = threading.Event(), orders.place

    def slow_place(board, job, **kw):                        # the Naïve board's CLI is this slow, or slower
        gate.wait(10)
        return real(board, job, **kw)

    orders.place = slow_place
    try:
        with Server("dup") as s:
            c1, r1 = http(s.url("/api/order"), {"job": "wave"})
            c2, r2 = http(s.url("/api/order"), {"job": "wave"})
            c3, r3 = http(s.url("/api/order"), {"job": "pick"})   # another button is another order
            assert c1 == 202 and c2 == 200 and c3 == 202, (c1, c2, c3)
            assert r2["ticket"]["id"] == r1["ticket"]["id"] and r2["ticket"]["duplicate"], (r1, r2)
            assert r3["ticket"]["id"] != r1["ticket"]["id"] and "duplicate" not in r3["ticket"]
            assert [e["job"] for e in presenter_events(s.events, "order")] == ["wave", "pick"]
            gate.set()
            p = s.presenter_idle()
            assert p["pending"] == 0 and p["last"]["state"] == "done", p
            assert sorted(orders.parse_card(c)["job"] for c in s.board.show()) == ["pick", "wave"]
            c4, r4 = http(s.url("/api/order"), {"job": "wave"})    # once it is done, a new click is a new order
            assert c4 == 202 and r4["ticket"]["id"] not in (r1["ticket"]["id"], r3["ticket"]["id"])
            s.presenter_idle()
            assert len(s.board.show()) == 3
    finally:
        orders.place = real


# ---- an arm host restart -----------------------------------------------------------------------------
def test_old_hosts_open_incident_is_lost_not_open():
    d = mission.Derived()
    inc = {"id": "inc-1", "kind": "us_frozen", "summary": "Distance stuck", "state": "open", "opened_at": 990.0}
    d.note({"pid": 111, "ts": 1000.0, "incidents": [inc], "history": [inc]})
    (i,) = d.incidents(1001.0)
    assert (i["key"], i["outcome"], i["open_s"], i["closed"]) == ("111/inc-1", "open", 11.0, None), i

    d.note({"pid": 222, "ts": 1030.0, "incidents": [], "history": []})      # the host restarted
    (i,) = d.incidents(1031.0)
    assert (i["outcome"], i["open_s"], i["closed"]) == ("lost", None, 1000.0), i
    assert d.summary([i], {"cards": []}, [])["open"] == 0

    d.note({"pid": 111, "ts": 1000.0, "incidents": [inc], "history": [inc]})   # read late by another thread
    assert d.incidents(1032.0)[0]["outcome"] == "lost"

    new = {**inc, "kind": "pod_clock_skew", "opened_at": 1033.0}            # same id, the new host
    d.note({"pid": 222, "ts": 1033.2, "incidents": [], "history": [new]})  # open list a frame behind history
    by = {i["key"]: i for i in d.incidents(1034.0)}
    assert by["222/inc-1"]["outcome"] == "open" and by["111/inc-1"]["outcome"] == "lost", by
    d.note({"pid": 222, "ts": 1040.0, "incidents": [],
            "history": [{**new, "state": "resolved", "resolved_at": 1039.0, "fix": "pod healed: x"}]})
    by = {i["key"]: i for i in d.incidents(1041.0)}
    assert by["222/inc-1"]["outcome"] == "healed" and by["222/inc-1"]["heal_s"] == 6.0, by
    assert d.summary(list(by.values()), {"cards": []}, [])["open"] == 0


# ---- a real sim host ---------------------------------------------------------------------------------
def test_order_and_fault_through_the_api_on_a_sim_host():
    try:
        import mujoco  # noqa: F401
    except ImportError:
        print("   (skipped: no MuJoCo in this Python)")
        return
    with Server("host") as s:
        env = {**os.environ, "PYTHONPATH": str(ROOT), "ARMSIM_SPEAK": "off", "ARMSIM_EVENTS": str(s.events),
               "ARMSIM_FILE_BOARD": str(s.dir / "board.json"), "ARMSIM_POD_VAR": str(s.dir / "pod")}
        env.pop("ARMSIM_ROMULUS", None)
        log = open(s.dir / "host.log", "w")
        host = subprocess.Popen([sys.executable, "-u", "-m", "armsim.host"], cwd=ROOT, env=env,
                                stdout=log, stderr=subprocess.STDOUT)
        try:
            t0 = time.time()
            while True:
                assert host.poll() is None, (s.dir / "host.log").read_text()[-2000:]
                code, arm = http(s.url("/api/arm"))
                if "error" not in arm and not arm["stale"]:
                    break
                assert time.time() - t0 < 90, arm
                time.sleep(0.3)
            assert arm["pid"] == host.pid and arm["incidents"] == [], arm

            code, r = http(s.url("/api/order"), {"job": "wave"})
            assert code == 202, (code, r)
            p = s.presenter_idle()
            assert p["last"]["action"] == "order" and p["last"]["ok"], p
            code, r = http(s.url("/api/fault"), {"fault": "us_frozen"})
            assert code == 202, (code, r)
            p = s.presenter_idle(60)
            assert p["last"]["action"] == "fault" and p["last"]["ok"] and p["last"]["result"]["where"] == "arm", p
            got = client.call("skill", name="reach", timeout=60)       # a commander moves the arm: the fault trips
            assert got.get("incident"), got

            t0 = time.time()
            while True:
                code, st = http(s.url("/api/state"))
                incs = [i for i in st["derived"]["incidents"] if i["key"].startswith(f"{host.pid}/")]
                if incs and st["arm"].get("incidents"):
                    break
                assert time.time() - t0 < 10, st["arm"]
                time.sleep(0.2)
            (card,) = st["board"]["cards"]
            assert (card["job"], card["source"], card["status"]) == ("wave", "mission", "todo"), card
            # frozen at home's "no echo", the reach reads as us_missing_echo (or us_frozen/us_stuck)
            assert incs[0]["outcome"] == "open" and incs[0]["kind"].startswith("us_"), incs
            assert st["derived"]["summary"]["open"] == 1, st["derived"]["summary"]
            assert {e["action"] for e in st["events"] if e["kind"] == "presenter"} == {"order", "fault"}
            assert any(f["kind"] == "incident" for f in st["derived"]["feed"])

            code, r = http(s.url("/api/clear"), {})                     # a person fixed it
            assert code == 202, (code, r)
            p = s.presenter_idle(90)
            assert p["last"]["action"] == "clear" and p["last"]["ok"] and p["last"]["result"]["verified"], p
            code, st = http(s.url("/api/state"))
            t0 = time.time()
            while st["derived"]["summary"]["open"] and time.time() - t0 < 5:
                time.sleep(0.2)
                code, st = http(s.url("/api/state"))
            (inc,) = [i for i in st["derived"]["incidents"] if i["key"].startswith(f"{host.pid}/")]
            assert inc["outcome"] == "healed" and st["derived"]["summary"]["open"] == 0, inc
        finally:
            try:
                client.call("shutdown", timeout=5)
            except OSError:
                pass
            try:
                host.wait(15)
            except subprocess.TimeoutExpired:
                host.kill()
                host.wait(5)
            log.close()


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            t0 = time.time()
            fn()
            print(f"ok  {name}  ({time.time() - t0:.1f} s)")
