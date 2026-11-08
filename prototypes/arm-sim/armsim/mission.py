"""Romulus Mission Control: the one screen for the live demo (a projector, judges across the room).

    PYTHONPATH=. .venv/bin/python -m armsim.mission                     # http://localhost:8765
    python -m armsim.mission --port 8765 --board file|naive --board-id brd_... --romulus http://localhost:8000
    python -m armsim.mission --host 0.0.0.0                              # open it from another laptop

The page (armsim/mission/index.html, one file, no network) polls two endpoints:
  GET /api/arm     the arm host's state snapshot (about 10 Hz, for the 3D twin)
  GET /api/state   everything else (about 1 Hz): {arm, board, events, romulus, derived, presenter}

Where the data comes from. Each source fails on its own and reads {error: "..."}, never a 500:
  arm      the snapshot file the arm host writes (host.StateWriter): $ARMSIM_STATE, else next to the
           socket ($ARMSIM_SOCK or <temp dir>/armsim.sock -> armsim-state.json). Never the socket
           itself for display: a skill holds the socket for seconds.
  board    the ARM JOB cards (armsim.orders): the file board on every call; the Naïve board through
           the `naive` CLI, refreshed in the background every 3 s.
  events   the last 200 lines of the shared event log (armsim.events, $ARMSIM_EVENTS).
  romulus  /v1/devices, /v1/incidents (latest 20), /v1/work-orders and the kill switch, polled every
           2 s in the background. The kill switch has no JSON read: it comes from the page partial
           /partials/killswitch (class "killswitch frozen"), else from the arm's last check-in.
  derived  the heal timeline, the counters and the commander feed, from the snapshot's incident
           history (the watchdog's own record) and the event log.

What the presenter bar does (POST, answered 202 at once; worker threads run them in order, so a
skill holding the arm's socket never stalls the page). Each POST writes a `presenter` event:
  /api/order {job}            orders.place(board, job, source="mission"): a new ARM JOB card
  /api/fault {fault}          the host's inject (joint_stuck: the elbow, params {joint: 2})
  /api/clear                  "a person fixed it": the host's clear, then repair verify
  /api/freeze, /api/unfreeze  Romulus's kill switch, as {"by": "mission-control", "why": "presenter"}
"""
import argparse
import json
import os
import queue
import re
import statistics
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import events, orders
from .board import open_board
from .client import call, socket_path

PAGE = Path(__file__).with_suffix("") / "index.html"
EVENTS_TAIL = 200
NAIVE_TTL_S = 3.0
ROMULUS_EVERY_S = 2.0
STALE_S = 5.0
FAULTS = {   # what the presenter can plant, and how the screen names it
    "us_frozen": "a stuck distance sensor",
    "us_no_echo": "a sensor with no echo",
    "pod_image07": "bad pod firmware (image 07: distance math cut to 16 bits)",
    "pod_image02": "bad pod firmware (image 02: half-speed clock)",
    "joint_stuck": "a dead elbow servo",
}


def state_path():
    """The same rule as host.state_path() (not imported: that pulls in MuJoCo)."""
    if os.environ.get("ARMSIM_STATE"):
        return Path(os.environ["ARMSIM_STATE"])
    sock = Path(socket_path())
    return sock.with_name(sock.stem + "-state.json")


def _age(iso, now):
    try:
        return round(now - datetime.fromisoformat(str(iso).replace("Z", "+00:00")).timestamp(), 1)
    except (TypeError, ValueError):
        return None


# ---- the arm ----------------------------------------------------------------------------------
def read_arm(path=None):
    p = Path(path or state_path())
    try:
        snap = json.loads(p.read_text())
    except FileNotFoundError:
        return {"error": f"no arm snapshot yet at {p}: is the arm host running?"}
    except (OSError, json.JSONDecodeError) as exc:
        return {"error": f"can't read the arm snapshot {p}: {exc}"}
    snap["age_s"] = round(time.time() - float(snap.get("ts") or 0), 2)
    snap["stale"] = snap["age_s"] > STALE_S
    return snap


# ---- the event log ------------------------------------------------------------------------------
def tail_events(path=None, n=EVENTS_TAIL, max_bytes=512 * 1024):
    """The last n events, reading only the end of the file (an overnight soak makes it long)."""
    p = Path(path or events.PATH)
    try:
        with open(p, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - max_bytes))
            data = f.read()
    except FileNotFoundError:
        return []
    lines = data.decode("utf-8", "replace").splitlines()
    if size > max_bytes:
        lines = lines[1:]                            # the first line is cut
    out = []
    for line in lines[-n:]:
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


# ---- the job board ------------------------------------------------------------------------------
def last_line(comments, prefixes=("RESULT:", "BLOCKED:")):
    texts = [(c.get("body") or c.get("text") or c.get("content") or "") if isinstance(c, dict) else str(c)
             for c in comments or []]
    for t in reversed(texts):
        if t.strip().upper().startswith(prefixes):
            return t.strip()
    return texts[-1].strip() if texts else None


class BoardView:
    """ARM JOB cards for the kanban. The file board is read on every call; the Naïve board is slow
    (a CLI call per verb), so a background thread refreshes it and the page gets the cached copy."""

    KEEP = 40

    def __init__(self, kind, board_id=None):
        self.kind, self.board_id = kind, board_id
        self.board = open_board(kind, board_id)
        self.cache, self.comments = None, {}          # comments: card id -> (updated_at, last line)
        self.lock, self.refreshing = threading.Lock(), False

    def _cards(self):
        now = time.time()
        cards = [c for c in self.board.show() if orders.is_arm_job(c)]
        cards.sort(key=lambda c: str(c.get("created_at") or ""), reverse=True)
        out = []
        for i, c in enumerate(cards[:self.KEEP]):
            if "comments" in c:
                last = last_line(c["comments"])
            elif i < 12 and c.get("status") != "todo":   # the real board: only the newest, and only on change
                seen = self.comments.get(c.get("id"))
                if seen and seen[0] == c.get("updated_at"):
                    last = seen[1]
                else:
                    try:
                        last = last_line(self.board.comments(c["id"]))
                    except Exception as exc:          # one card's comments failing is not the board failing
                        last = f"(comments unavailable: {exc})"
                    self.comments[c.get("id")] = (c.get("updated_at"), last)
            else:
                last = None
            job = orders.parse_card(c)
            out.append({"id": c.get("id"), "title": c.get("title"), "order": job["order"], "job": job["job"],
                        "source": job["source"], "status": c.get("status") or "todo", "note": c.get("note"),
                        "created_at": c.get("created_at"), "updated_at": c.get("updated_at"),
                        "age_s": _age(c.get("created_at"), now), "updated_s": _age(c.get("updated_at"), now),
                        "last": last})
        return out

    def _refresh(self):
        try:
            got = {"kind": self.kind, "id": self.board.id, "cards": self._cards(), "at": time.time()}
        except Exception as exc:
            got = {"kind": self.kind, "id": self.board.id, "error": f"{type(exc).__name__}: {exc}", "at": time.time()}
        with self.lock:
            self.cache, self.refreshing = got, False

    def get(self):
        if self.kind == "file":
            try:
                return {"kind": "file", "id": self.board.id, "cards": self._cards()}
            except Exception as exc:
                return {"kind": "file", "id": self.board.id, "error": f"{type(exc).__name__}: {exc}"}
        with self.lock:
            cached, stale = self.cache, not self.cache or time.time() - self.cache["at"] > NAIVE_TTL_S
            if stale and not self.refreshing:
                self.refreshing = True
                threading.Thread(target=self._refresh, name="board", daemon=True).start()
        if cached is None:
            return {"kind": self.kind, "id": self.board.id, "cards": [], "loading": True}
        return {**cached, "age_s": round(time.time() - cached["at"], 1)}


# ---- Romulus ------------------------------------------------------------------------------------
def http_json(url, *, data=None, timeout=1.5):
    body = None if data is None else json.dumps(data).encode()
    req = urllib.request.Request(url, data=body, method="POST" if data is not None else "GET",
                                 headers={"Content-Type": "application/json", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    return raw if url.endswith("/partials/killswitch") else json.loads(raw or b"null")


def _err(exc):
    if isinstance(exc, urllib.error.HTTPError):
        return f"HTTP {exc.code}"
    reason = getattr(exc, "reason", None) or exc
    return f"{type(reason).__name__}: {reason}" if not isinstance(reason, str) else reason


class RomulusView:
    """Polls Romulus in the background so a dead or slow cloud never stalls the page."""

    def __init__(self, url):
        self.url = url.rstrip("/")
        self.cache = {"url": self.url, "error": "not polled yet", "at": 0.0}
        self.wake = threading.Event()

    def start(self):
        threading.Thread(target=self._loop, name="romulus", daemon=True).start()
        return self

    def _loop(self):
        while True:
            self.poll()
            self.wake.wait(ROMULUS_EVERY_S)
            self.wake.clear()

    def poll(self):
        out, errors = {"url": self.url}, {}
        for key, path in (("devices", "/v1/devices"), ("incidents", "/v1/incidents?limit=20"),
                          ("work_orders", "/v1/work-orders?limit=20")):
            try:
                got = http_json(self.url + path)
                if isinstance(got, dict):                  # tolerate {"items": [...]} wrappers
                    got = next((v for v in got.values() if isinstance(v, list)), [])
                out[key] = got if isinstance(got, list) else []
            except Exception as exc:
                errors[key] = _err(exc)
        try:
            html = http_json(self.url + "/partials/killswitch").decode("utf-8", "replace")
            out["frozen"] = bool(re.search(r'class="killswitch[^"]*\bfrozen\b', html))
            why = re.search(r'class="frozen-banner"\s+title="([^"]*)"', html)
            out["frozen_why"] = why.group(1) if why and out["frozen"] else None
        except Exception as exc:
            errors["frozen"] = _err(exc)
        if len(errors) == 4:
            out = {"url": self.url, "error": f"Romulus offline at {self.url} ({errors['devices']})"}
        elif errors:
            out["errors"] = errors
        out["at"] = time.time()
        self.cache = out
        return out

    def get(self):
        return {**self.cache, "age_s": round(time.time() - self.cache.get("at", 0), 1)}

    def switch(self, on):
        got = http_json(f"{self.url}/v1/fleet/{'freeze' if on else 'unfreeze'}",
                        data={"by": "mission-control", "why": "presenter"}, timeout=5.0)
        self.wake.set()                                    # show the new state at once
        return got


# ---- the presenter's buttons ----------------------------------------------------------------------
class Presenter:
    """Two workers: one for the arm host (its calls may wait seconds behind a moving skill), one for
    the board and Romulus. A ticket per POST; the page shows the pending count and the last result."""

    def __init__(self):
        self.queues = {"host": queue.Queue(), "net": queue.Queue()}
        self.pending, self.last, self.n = 0, None, 0
        self.lock = threading.Lock()
        for name, q in self.queues.items():
            threading.Thread(target=self._work, args=(q,), name=f"presenter-{name}", daemon=True).start()

    def submit(self, lane, action, label, fn, **fields):
        with self.lock:
            self.n += 1
            self.pending += 1
            ticket = {"id": self.n, "action": action, "label": label, "at": time.time(), "state": "queued"}
        events.emit("mission", "presenter", f"Presenter: {label}", action=action, echo=False, **fields)
        self.queues[lane].put((ticket, fn))
        return ticket

    def _work(self, q):
        while True:
            ticket, fn = q.get()
            try:
                ticket.update(result=fn(), ok=True, state="done")
            except Exception as exc:
                ticket.update(ok=False, state="failed", error=f"{type(exc).__name__}: {exc}")
                events.emit("mission", "error", f"{ticket['label']} failed: {ticket['error']}", echo=False,
                            action=ticket["action"])
            ticket["done_at"] = time.time()
            with self.lock:
                self.pending -= 1
                self.last = ticket

    def get(self):
        with self.lock:
            return {"pending": self.pending, "last": self.last}


def host_call(op, **fields):
    try:
        r = call(op, timeout=90.0, **fields)
    except (FileNotFoundError, ConnectionRefusedError) as exc:
        raise RuntimeError(f"the arm host isn't running (no socket at {socket_path()})") from exc
    if not r.get("ok", True):
        raise RuntimeError(r.get("error") or f"{op} refused")
    return r


# ---- what the screen derives -----------------------------------------------------------------------
SHORT = {"us_frozen": "stuck sensor", "us_stuck": "stuck sensor", "us_scale": "sensor scale wrong",
         "us_offset": "sensor offset", "us_residual": "sensor disagrees with pose", "us_noisy": "noisy sensor",
         "us_missing_echo": "no echo", "joint_stall": "joint stalled", "pod_us_mismatch": "bad pod firmware",
         "pod_clock_skew": "pod clock skew", "pod_link_garbled": "pod link garbled", "pod_us_no_echo": "pod: no echo"}


def fix_label(fix):
    """The watchdog's `fix` ("repair: restart_sensor", "pod healed: ...") as a few words."""
    fix = fix or ""
    if fix.startswith("pod healed"):
        return "the pod healer rolled its firmware back"
    if "restart_sensor" in fix:
        return "sensor restarted through podd"
    if "verify" in fix:
        return "a checked retry passed"
    return fix.removeprefix("repair: ") or "verified"


def short_kind(kind):
    kind = kind or "fault"
    return SHORT.get(kind) or ("sensor pod: " + kind[4:].replace("_", " ") if kind.startswith("pod_")
                               else kind.replace("_", " "))


class Derived:
    """Remembers every incident it has seen in a snapshot (keyed by host pid and id), so the heal
    timeline survives an arm host restart while Mission Control runs."""

    def __init__(self):
        self.seen = {}
        self.lock = threading.Lock()                  # /api/arm and /api/state run on different threads

    def note(self, arm):
        if arm.get("error"):
            return
        with self.lock:
            for inc in arm.get("history") or []:
                if inc.get("opened_at") is None:
                    continue
                self.seen[(arm.get("pid"), inc.get("id"))] = inc
            if len(self.seen) > 300:
                for k in sorted(self.seen, key=lambda k: self.seen[k]["opened_at"])[:-300]:
                    del self.seen[k]

    def incidents(self, now):
        out = []
        with self.lock:
            seen = list(self.seen.items())
        for (pid, _), inc in seen:
            end = inc.get("resolved_at") or inc.get("handed_off")
            outcome = ("person" if inc.get("handed_off") else "healed" if inc.get("state") == "resolved"
                       else "open")
            out.append({"key": f"{pid}/{inc['id']}", "id": inc["id"], "kind": inc.get("kind"),
                        "short": short_kind(inc.get("kind")), "summary": inc.get("summary"),
                        "opened": inc["opened_at"], "closed": end, "outcome": outcome, "fix": inc.get("fix"),
                        "heal_s": round(inc["resolved_at"] - inc["opened_at"], 1)
                        if outcome == "healed" and inc.get("resolved_at") else None,
                        "open_s": round(now - inc["opened_at"], 1) if outcome == "open" else None})
        return sorted(out, key=lambda i: i["opened"])

    def jobs(self, evs):
        """Job bars for the timeline: job_started -> job_result / card_done / card_blocked."""
        runs = {}
        for e in evs:
            key = e.get("card") or e.get("order")
            if not key:
                continue
            if e.get("kind") == "job_started":
                runs[key] = {"key": key, "job": e.get("job"), "order": e.get("order"), "start": e["ts"], "end": None,
                             "ok": None}
            elif e.get("kind") in ("job_result", "card_done", "card_blocked") and key in runs and not runs[key]["end"]:
                runs[key].update(end=e["ts"], ok=e.get("kind") == "card_done" or bool(e.get("ok")))
        return sorted(runs.values(), key=lambda r: r["start"])[-40:]

    def feed(self, evs, incs, pod):
        """Events newest first, plus the watchdog's own lines when no event says the same thing."""
        items = [{"ts": e.get("ts"), "src": e.get("src"), "kind": e.get("kind"), "text": e.get("text"),
                  "card": e.get("card"), "order": e.get("order"), "ok": e.get("ok")} for e in evs]
        said = [(e.get("ts") or 0, e.get("kind")) for e in evs if e.get("kind") in ("incident", "heal")]

        def near(kind, ts):
            return any(k == kind and abs(t - ts) < 20 for t, k in said)

        for i in incs:
            if not near("incident", i["opened"]):
                what = (i.get("summary") or short_kind(i["kind"])).rstrip(". ")
                items.append({"ts": i["opened"], "src": "watchdog", "kind": "incident",
                              "text": f"{what}. The arm parks.", "synth": True})
            if i["outcome"] == "healed" and not near("heal", i["closed"]):
                items.append({"ts": i["closed"], "src": "watchdog", "kind": "heal",
                              "text": f"Healed in {i['heal_s']:.1f} s: {fix_label(i.get('fix'))}, checked at home.",
                              "synth": True})
            if i["outcome"] == "person":
                items.append({"ts": i["closed"], "src": "watchdog", "kind": "handoff",
                              "text": f"Needs a person: {short_kind(i['kind'])} still failing after a checked retry.",
                              "synth": True})
        last = (pod or {}).get("healer_last")
        if last and last.get("at"):
            items.append({"ts": last["at"], "src": "pod healer", "kind": "heal",
                          "text": f"Pod healer rolled the firmware back: {last.get('from')} -> {last.get('to')}.",
                          "synth": True})
        items = [i for i in items if i.get("ts")]
        return sorted(items, key=lambda i: i["ts"], reverse=True)[:80]

    def summary(self, incs, board, evs):
        heals = [i["heal_s"] for i in incs if i["heal_s"] is not None]
        cards = board.get("cards") or []
        done = sum(1 for c in cards if c["status"] == "done")
        if not cards:
            done = sum(1 for e in evs if e.get("kind") == "card_done")
        return {"jobs_done": done, "faults_healed": len(heals),
                "median_heal_s": round(statistics.median(heals), 1) if heals else None,
                "needs_person": sum(1 for i in incs if i["outcome"] == "person"),
                "open": sum(1 for i in incs if i["outcome"] == "open")}


class Mission:
    def __init__(self, *, board_kind, board_id, romulus):
        self.board = BoardView(board_kind, board_id)
        self.romulus = RomulusView(romulus).start()
        self.derived = Derived()
        self.presenter = Presenter()

    def arm(self):
        arm = read_arm()
        self.derived.note(arm)
        return arm

    def state(self):
        now = time.time()
        out = {"now": now, "arm": self.arm()}
        try:
            out["board"] = self.board.get()
        except Exception as exc:
            out["board"] = {"error": f"{type(exc).__name__}: {exc}"}
        try:
            evs = tail_events()
            out["events"] = evs
        except Exception as exc:
            evs, out["events"] = [], []
            out["events_error"] = f"{type(exc).__name__}: {exc}"
        out["romulus"] = self.romulus.get()
        try:
            incs = self.derived.incidents(now)
            out["derived"] = {"incidents": incs, "jobs": self.derived.jobs(evs),
                              "feed": self.derived.feed(evs, incs, out["arm"].get("pod")),
                              "summary": self.derived.summary(incs, out["board"], evs)}
        except Exception as exc:
            out["derived"] = {"error": f"{type(exc).__name__}: {exc}"}
        out["presenter"] = self.presenter.get()
        out["sources"] = {"state": str(state_path()), "events": str(events.PATH), "sock": socket_path()}
        return out

    # ---- POST actions ----
    def order(self, body):
        job = body.get("job") or "pick"
        if job not in orders.JOBS:
            raise ValueError(f"unknown job {job!r}; jobs: {sorted(orders.JOBS)}")

        def run():
            order, card = orders.place(self.board.board, job, source="mission")
            return {"order": order["id"], "card": card.get("id")}
        return self.presenter.submit("net", "order", f"new order: {orders.JOBS[job]['title']}", run, job=job)

    def fault(self, body):
        fault = body.get("fault")
        if fault not in FAULTS:
            raise ValueError(f"unknown fault {fault!r}; faults: {sorted(FAULTS)}")
        params = {"joint": 2} if fault == "joint_stuck" else {}
        return self.presenter.submit("host", "fault", f"plant {FAULTS[fault]} ({fault})",
                                     lambda: host_call("inject", fault=fault, **({"params": params} if params else {})),
                                     fault=fault)

    def clear(self, body):
        def run():
            host_call("clear")
            r = host_call("repair", action="verify")
            return {"verified": r.get("verified"), "still": r.get("still"), "open": r.get("open")}
        return self.presenter.submit("host", "clear", "a person fixed it (clear the faults, then check at home)", run)

    def freeze(self, on):
        return self.presenter.submit("net", "freeze" if on else "unfreeze",
                                     "freeze the fleet" if on else "unfreeze the fleet", lambda: self.romulus.switch(on))


def make_handler(mission):
    posts = {"/api/order": mission.order, "/api/fault": mission.fault, "/api/clear": mission.clear,
             "/api/freeze": lambda body: mission.freeze(True), "/api/unfreeze": lambda body: mission.freeze(False)}

    class Handler(BaseHTTPRequestHandler):
        server_version = "MissionControl/1"

        def log_message(self, fmt, *args):            # the page polls 10 times a second: stay quiet
            pass

        def _send(self, code, body, ctype="application/json"):
            data = body if isinstance(body, bytes) else json.dumps(body, default=str).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            try:
                if path in ("/", "/index.html"):
                    return self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
                if path == "/api/arm":
                    return self._send(200, mission.arm())
                if path == "/api/state":
                    return self._send(200, mission.state())
                return self._send(404, {"error": f"no such page {path}"})
            except Exception as exc:                  # a broken source must not take the screen down
                return self._send(200, {"error": f"{type(exc).__name__}: {exc}"})

        def do_POST(self):
            path = self.path.split("?", 1)[0]
            fn = posts.get(path)
            if not fn:
                return self._send(404, {"error": f"no such action {path}"})
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}") if n else {}
                return self._send(202, {"ok": True, "ticket": fn(body if isinstance(body, dict) else {})})
            except ValueError as exc:
                return self._send(400, {"ok": False, "error": str(exc)})
            except Exception as exc:
                return self._send(500, {"ok": False, "error": f"{type(exc).__name__}: {exc}"})

    return Handler


def main():
    ap = argparse.ArgumentParser(description="Romulus Mission Control: the demo screen.")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1", help="0.0.0.0 to open it from another machine")
    ap.add_argument("--board", choices=["file", "naive"], default=os.environ.get("ARMSIM_BOARD_KIND", "file"))
    ap.add_argument("--board-id", default=None)
    ap.add_argument("--romulus", default=os.environ.get("ARMSIM_ROMULUS", "http://localhost:8000"))
    a = ap.parse_args()
    mission = Mission(board_kind=a.board, board_id=a.board_id, romulus=a.romulus)
    srv = ThreadingHTTPServer((a.host, a.port), make_handler(mission))
    srv.daemon_threads = True
    print(f"Mission Control on http://{'localhost' if a.host in ('127.0.0.1', '0.0.0.0') else a.host}:{a.port} "
          f"(arm snapshot {state_path()}, board {a.board}, events {events.PATH}, Romulus {a.romulus})", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
