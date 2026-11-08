"""The dispatcher (armsim/dispatch.py) against a file board in a temp dir and a fake arm host: no
host, no Naïve, no Hermes. Run from prototypes/arm-sim:

    PYTHONPATH=. .venv/bin/python tests/test_dispatch.py      (or: python -m pytest tests/)

The Hermes tests put a fake `hermes` and a fake `naive` CLI (backed by a FileBoard) on PATH.
"""
import functools
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from armsim import dispatch, events, orders                  # noqa: E402
from armsim.board import FileBoard, NaiveBoard, comment_text  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="armsim-dispatch-test-"))
events.PATH = TMP / "orders-events.jsonl"                     # orders.place() logs here, not in runs/


def fresh(name):
    d = TMP / f"{name}-{time.time_ns()}"
    d.mkdir(parents=True)
    log = d / "events.jsonl"
    return d, FileBoard(d / "board.json", board_id="brd_test"), log, functools.partial(events.emit, path=log, echo=False)


def kinds(log, src=None):
    return [e["kind"] for e in events.read(log, src=src)]


def texts(board, cid):
    return [comment_text(c) for c in board.comments(cid)]


class FakeHost:
    """A stand-in arm host. Every skill arrives, except where a test plants a fault:
      fail={"reach": "us_stuck"}      the first `reach` halts and opens that incident
      fixable=False                   repairs never verify; a joint_stall is handed to a person
      pod_between=("reach", n)        after `reach`, podd flags the pod (pod_us_mismatch opens while the
                                      arm stands still); its healer fixes it after n status polls
      pod_on={"approach": n}          `approach` sees the bad image first (us_offset, podd's us_mismatch
                                      tied to it); fixed after n status polls
    """

    def __init__(self, *, fail=None, fixable=True, pod_between=None, pod_on=None, handed_off=False):
        self.fail, self.fixable = dict(fail or {}), fixable
        self.pod_between, self.pod_on = pod_between, dict(pod_on or {})
        self.incidents, self.pod_crit, self.calls = [], [], []
        self.pod_polls = None                         # status polls until the pod healer finishes
        if handed_off:
            self._open("joint_stall", "Elbow flex stopped 40 deg short of its target")
            self.incidents[-1]["handed_off"] = 1.0
            self.incidents[-1]["handoff_reason"] = "still failing after a checked retry"

    def open(self):
        return [i for i in self.incidents if i["state"] == "open"]

    def _open(self, kind, summary, **extra):
        inc = {"id": f"inc-{len(self.incidents) + 1}", "state": "open", "kind": kind, "summary": summary, **extra}
        self.incidents.append(inc)
        return inc

    def _pod_fault(self, kind, n):
        self.pod_crit = [{"kind": "us_mismatch", "severity": "critical", "summary": "distance disagrees with its echo"}]
        self.pod_polls = n
        return self._open(kind, "Reads 98 mm short against the arm's kinematics (0.11x)" if kind == "us_offset"
                          else "Sensor pod: distance disagrees with its echo", pod_incidents=["us_mismatch-1"])

    def __call__(self, op, **f):
        self.calls.append((op, f.get("name") or f.get("action")))
        if op == "status":
            if self.pod_polls is not None:
                self.pod_polls -= 1
                if self.pod_polls <= 0:               # the pod healer rolled back; the host's check passed
                    self.pod_crit, self.pod_polls = [], None
                    for i in self.open():
                        i["state"] = "resolved"
            return {"ok": True, "incidents": [{k: i[k] for k in ("id", "kind", "summary")} for i in self.open()],
                    "handed_off": [{k: i.get(k) for k in ("id", "kind", "summary", "handoff_reason")}
                                   for i in self.open() if i.get("handed_off")],
                    "pod": {"podd_up": True, "incidents": list(self.pod_crit)}}
        if op == "incidents":
            return {"ok": True, "incidents": self.incidents}
        if op == "skill":
            name = f["name"]
            if self.open():
                return {"ok": False, "error": "incident open; the arm stays parked until a repair is verified",
                        "incidents": self.open()}
            if name in self.pod_on:
                inc = self._pod_fault("us_offset", self.pod_on.pop(name))
                return {"ok": True, "result": {"arrived": False, "aborted": "reading far below"}, "incident": inc}
            if name in self.fail:
                kind = self.fail.pop(name)
                inc = self._open(kind, "Distance stuck at 130 mm while the arm moved 29 mm" if kind.startswith("us_")
                                 else "Elbow flex stopped 40 deg short of its target")
                return {"ok": True, "result": {"arrived": False, "halted": kind}, "incident": inc, "parked": {}}
            result = {"arrived": True}
            if name == "approach":
                result["switched_at_mm"] = 75
            if name == "grip":
                result = {"grasp": {"holding": True}}
            if name == "lift":
                result["grasp"] = {"holding": True}
            if self.pod_between and name == self.pod_between[0]:
                self._pod_fault("pod_us_mismatch", self.pod_between[1])
                self.pod_between = None
            return {"ok": True, "skill": name, "result": result}
        if op == "repair":
            if self.fixable:
                for i in self.open():
                    i["state"] = "resolved"
                return {"ok": True, "action": f["action"], "verified": True, "still": [], "open": []}
            for i in self.open():
                if i["kind"] == "joint_stall":
                    i.update(handed_off=1.0, handoff_reason="still failing after a checked retry: home move arrived=False")
            return {"ok": True, "action": f["action"], "verified": False, "still": ["home move arrived=False"],
                    "open": [i["id"] for i in self.open()]}
        return {"ok": False, "error": f"unknown op {op!r}"}


def scripted(board, host, emit):
    ex = dispatch.ScriptedExecutor(board, call=host, emit=emit, poll_s=0.01, pod_clear_s=0.05, hold_s=0.05)
    return dispatch.Dispatcher(board, ex, call=host, emit=emit, every=0.01, sleep=lambda s: None)


def test_happy_path_closes_the_card_with_a_note():
    _, board, log, emit = fresh("happy")
    _, card = orders.place(board, "pick", source="test")
    host = FakeHost()
    jobs = scripted(board, host, emit).run(once=True)
    c = board.card(card["id"])
    assert c["status"] == "done" and c["note"], c
    said = texts(board, card["id"])
    assert said[0].startswith("CLAIMED: the dispatcher started the scripted commander"), said
    assert said[-1].startswith("RESULT: pick up the block") and "No faults" in said[-1], said
    assert [n for op, n in host.calls if op == "skill"] == orders.JOBS["pick"]["steps"], host.calls
    k = kinds(log)
    for want in ("dispatcher", "card_claimed", "job_started", "skill", "job_result", "card_done", "job_summary"):
        assert want in k, (want, k)
    assert k.count("skill") == 7 and jobs[0]["outcome"] == "done", (k, jobs)


def test_an_incident_heals_and_the_step_is_retried():
    _, board, log, emit = fresh("heal")
    _, card = orders.place(board, "pick", source="test")
    host = FakeHost(fail={"reach": "us_stuck"})
    scripted(board, host, emit).run(once=True)
    c = board.card(card["id"])
    assert c["status"] == "done" and "us_stuck" in c["note"], c
    result = texts(board, card["id"])[-1]
    assert result.startswith("RESULT:") and "us_stuck during reach" in result and "restarted the sensor" in result, result
    skills = [n for op, n in host.calls if op == "skill"]
    assert skills.count("reach") == 2, skills                # retried once, after the heal
    assert ("repair", "restart_sensor") in host.calls, host.calls
    heal = [e for e in events.read(log) if e["kind"] == "heal"]
    assert len(heal) == 1 and heal[0]["ok"] and heal[0]["action"] == "restart_sensor", heal
    assert "incident" in kinds(log, src="commander")


def test_an_unhealable_incident_ends_blocked():
    _, board, log, emit = fresh("stall")
    _, card = orders.place(board, "pick", source="test")
    host = FakeHost(fail={"reach": "joint_stall"}, fixable=False)
    jobs = scripted(board, host, emit).run(once=True)
    c = board.card(card["id"])
    assert c["status"] == "blocked", c
    said = texts(board, card["id"])
    assert said[-1].startswith("BLOCKED: joint_stall during reach") and "needs a person" in said[-1], said
    assert not any(t.startswith("BLOCKED: dispatcher") for t in said), said   # the commander said it, once
    assert ("repair", "verify") in host.calls and jobs[0]["outcome"] == "blocked", (host.calls, jobs)
    assert "card_blocked" in kinds(log) and "card_done" not in kinds(log)


def test_a_pod_fault_between_steps_waits_for_the_pod_healer():
    _, board, log, emit = fresh("pod-between")
    _, card = orders.place(board, "pick", source="test")
    host = FakeHost(pod_between=("reach", 4))
    scripted(board, host, emit).run(once=True)
    c = board.card(card["id"])
    assert c["status"] == "done" and "pod_us_mismatch" in c["note"], c
    assert not [n for op, n in host.calls if op == "repair"], host.calls   # the pod's own healer fixed it
    assert "the sensor pod's own healer fixed it" in texts(board, card["id"])[-1]


def test_a_bad_pod_image_seen_as_us_offset_is_not_a_sensor_restart():
    """Bad image 07: the approach opens us_offset (a us_* kind) with podd's us_mismatch tied to it. A
    sensor restart there would fail its check and count toward a handoff; wait for the pod instead."""
    _, board, log, emit = fresh("pod-offset")
    _, card = orders.place(board, "pick", source="test")
    host = FakeHost(pod_on={"approach": 5})
    scripted(board, host, emit).run(once=True)
    c = board.card(card["id"])
    assert c["status"] == "done" and "us_offset (the sensor pod's us_mismatch)" in c["note"], c
    assert "podd flagged us_mismatch" in texts(board, card["id"])[-1]     # the root cause is on the card
    assert ("repair", "restart_sensor") not in host.calls, host.calls
    assert [n for op, n in host.calls if op == "skill"].count("approach") == 2


def test_a_third_fault_is_left_for_a_person():
    _, board, log, emit = fresh("budget")
    _, card = orders.place(board, "pick", source="test")
    host = FakeHost(fail={"reach": "us_stuck", "approach": "us_stuck", "grip": "us_stuck"})
    scripted(board, host, emit).run(once=True)
    c = board.card(card["id"])
    assert c["status"] == "blocked", c
    assert "after 2 heals" in texts(board, card["id"])[-1], texts(board, card["id"])


def test_a_stop_mid_job_finishes_the_step_blocks_the_card_and_exits():
    _, board, log, emit = fresh("stop")
    _, first = orders.place(board, "pick", source="test")
    _, second = orders.place(board, "pick", source="test")
    host = FakeHost()
    d = scripted(board, host, emit)

    def call(op, **f):                                   # SIGTERM arrives while `reach` is running
        reply = host(op, **f)
        if op == "skill" and f["name"] == "reach":
            d.stop.set()
        return reply
    d.host = d.exec.host = dispatch.Host(call)
    d.run()                                              # no --once: it must exit on the stop by itself
    assert [n for op, n in host.calls if op == "skill"] == ["go_home", "reach"], host.calls
    said = texts(board, first["id"])
    assert board.card(first["id"])["status"] == "blocked" and "stopped in the middle of the job, before approach" \
        in said[-1], said
    assert board.card(second["id"])["status"] == "todo"     # nothing new was claimed


def test_waiting_for_a_person_and_an_unreachable_host_are_said_once():
    _, board, log, emit = fresh("waiting")
    _, card = orders.place(board, "pick", source="test")
    d = scripted(board, FakeHost(handed_off=True), emit)
    assert [d.poll() for _ in range(3)] == ["waiting"] * 3
    assert board.card(card["id"])["status"] == "todo"
    assert kinds(log).count("waiting") == 1, kinds(log)

    def down(op, **f):
        raise ConnectionRefusedError("no host")
    d = scripted(board, down, emit)
    assert [d.poll() for _ in range(3)] == ["waiting"] * 3
    assert kinds(log).count("error") == 1, kinds(log)
    d.host = dispatch.Host(FakeHost())                       # the host comes back: the card is claimed
    d.exec.host = d.host
    assert d.poll() == "job" and board.card(card["id"])["status"] == "done"


def test_a_stale_doing_card_is_blocked_at_startup():
    _, board, log, emit = fresh("stale")
    _, card = orders.place(board, "pick", source="test")
    other = board.add("not an arm job", body="leave me")
    board.move(card["id"], status="doing")
    board.move(other["id"], status="doing")
    host = FakeHost()
    scripted(board, host, emit).run(once=True)
    assert board.card(card["id"])["status"] == "blocked"
    assert board.card(other["id"])["status"] == "doing"
    assert "stopped in the middle of its job" in texts(board, card["id"])[-1]
    blocked = [e for e in events.read(log) if e["kind"] == "card_blocked"]
    assert blocked and blocked[0]["reason"] == "stale", blocked
    assert not [c for c in host.calls if c[0] == "skill"], host.calls   # nothing re-ran


def test_the_lock_refuses_a_second_dispatcher():
    d, board, log, emit = fresh("lock")
    held = dispatch.take_lock(dispatch.lock_path(board))
    assert dispatch.lock_path(board).name == "board.dispatch.lock"    # not FileBoard's own board.lock
    env = dict(os.environ, PYTHONPATH=str(ROOT), ARMSIM_BOARD_KIND="file", ARMSIM_FILE_BOARD=str(d / "board.json"),
               ARMSIM_EVENTS=str(log), ARMSIM_SOCK=str(d / "no.sock"))
    cmd = [sys.executable, "-m", "armsim.dispatch", "--once"]
    try:
        try:
            dispatch.take_lock(dispatch.lock_path(board))
            raise AssertionError("a second lock in-process was granted")
        except SystemExit as exc:
            assert "another dispatcher" in str(exc), exc
        out = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
        assert out.returncode != 0 and "another dispatcher already works this board" in out.stderr, out
        assert f"pid {os.getpid()}" in out.stderr, out.stderr
    finally:
        held.close()
    out = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out                          # free again: it runs (an empty board, so it exits)


def test_hermes_needs_the_naive_board():
    try:
        dispatch.main(["--exec", "hermes", "--board", "file"])
        raise AssertionError("--exec hermes --board file was accepted")
    except SystemExit as exc:
        assert "--exec hermes needs --board naive" in str(exc), exc


# ---- Hermes, with a fake `hermes` and a fake `naive` on PATH ------------------------------------

FAKE_NAIVE = '''#!{py}
"""A fake `naive board ...` CLI over a FileBoard (FAKE_NAIVE_DB), printing {{"data": ...}} like the real one."""
import json, os, sys
sys.path.insert(0, {root!r})
from armsim.board import BoardError, FileBoard
args = [a for a in sys.argv[1:] if a != "--json"]
assert args[0] == "board", args
verb, pos, flags, i = args[1], [], {{}}, 2
while i < len(args):
    if args[i].startswith("--"):
        flags[args[i][2:]] = args[i + 1]
        i += 2
    else:
        pos.append(args[i])
        i += 1
b = FileBoard(os.environ["FAKE_NAIVE_DB"], board_id=pos[0])
try:
    out = {{"add": lambda: b.add(flags["title"], body=flags.get("body", ""), key=flags.get("key")),
           "show": lambda: b.show(flags.get("status")),
           "card": lambda: b.card(pos[1]),
           "comments": lambda: b.comments(pos[1]),
           "comment": lambda: b.comment(pos[1], flags["body"]),
           "move": lambda: b.move(pos[1], status=flags.get("status")),
           "done": lambda: b.done(pos[1], note=flags.get("note"))}}[verb]()
except BoardError as exc:
    sys.exit(f"error {{exc.code}}: {{exc}}")
print(json.dumps({{"data": out}}))
'''

FAKE_HERMES = {
    "silent": 'print("Hermes (fake): thinking about the card")\nprint("Hermes (fake): I give up without a word")\n',
    "closes": '''import re, subprocess, sys
prompt = sys.argv[-1]
card, board = re.search(r"crd_[0-9a-f]+", prompt).group(0), re.search(r"brd_[a-z0-9_]+", prompt).group(0)
print("Hermes (fake): working", card, "on", board, flush=True)
for args in (["comment", board, card, "--body", "RESULT: fake Hermes picked up the block"],
             ["done", board, card, "--note", "Fake Hermes did the job."]):
    subprocess.run(["naive", "board", *args, "--json"], check=True, capture_output=True)
print("\\x1b[32mdone\\x1b[0m")
''',
    "hangs": 'import time\nprint("Hermes (fake): this will take a while", flush=True)\ntime.sleep(60)\n',
}


def hermes_bench(name, behaviour):
    d, _, log, emit = fresh(name)
    bin_dir = d / "bin"
    bin_dir.mkdir()
    for tool, src in (("naive", FAKE_NAIVE.format(py=sys.executable, root=str(ROOT))),
                      ("hermes", f"#!{sys.executable}\n" + FAKE_HERMES[behaviour])):
        (bin_dir / tool).write_text(src)
        (bin_dir / tool).chmod(0o755)
    os.environ["PATH"] = f"{bin_dir}{os.pathsep}{os.environ['PATH']}"
    os.environ["FAKE_NAIVE_DB"] = str(d / "naive.json")
    board = NaiveBoard("brd_test")                           # the real NaiveBoard, through the fake CLI
    _, card = orders.place(board, "pick", source="test")
    ex = dispatch.HermesExecutor(board, emit=emit, log_dir=d / "commander")
    return d, board, card, log, dispatch.Dispatcher(board, ex, call=FakeHost(), emit=emit, every=0.01,
                                                    sleep=lambda s: None)


def with_path(fn):
    @functools.wraps(fn)
    def run():
        saved = os.environ["PATH"]
        try:
            fn()
        finally:
            os.environ["PATH"] = saved
            os.environ.pop("FAKE_NAIVE_DB", None)
    return run


@with_path
def test_hermes_that_exits_without_closing_the_card_is_blocked_by_the_dispatcher():
    d, board, card, log, disp = hermes_bench("hermes-silent", "silent")
    disp.run(once=True)
    c = board.card(card["id"])
    assert c["status"] == "blocked", c
    said = [comment_text(x) for x in board.comments(card["id"])]
    assert said[-1].startswith("BLOCKED: dispatcher: the hermes commander ended without closing the card "
                               "(Hermes exited with code 0"), said
    lines = [e["text"] for e in events.read(log) if e["kind"] == "commander_line"]
    assert lines == ["Hermes (fake): thinking about the card", "Hermes (fake): I give up without a word"], lines
    assert "I give up" in (d / "commander" / f"{card['id']}.log").read_text()


@with_path
def test_hermes_that_closes_the_card_is_done():
    d, board, card, log, disp = hermes_bench("hermes-closes", "closes")
    jobs = disp.run(once=True)
    c = board.card(card["id"])
    assert c["status"] == "done" and c["note"] == "Fake Hermes did the job.", c
    assert jobs[0]["outcome"] == "done" and "card_done" in kinds(log), (jobs, kinds(log))
    lines = [e["text"] for e in events.read(log) if e["kind"] == "commander_line"]
    assert lines[0] == f"Hermes (fake): working {card['id']} on brd_test" and lines[-1] == "done", lines


@with_path
def test_hermes_past_the_job_timeout_is_stopped_and_the_card_blocked():
    d, board, card, log, disp = hermes_bench("hermes-hangs", "hangs")
    disp.job_timeout = 2
    t0 = time.time()
    disp.run(once=True)
    assert time.time() - t0 < 20, time.time() - t0
    said = [comment_text(x) for x in board.comments(card["id"])]
    assert board.card(card["id"])["status"] == "blocked" and "ran past the job timeout (2 s)" in said[-1], said


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            t0 = time.time()
            fn()
            print(f"ok  {name}  ({time.time() - t0:.1f} s)")
