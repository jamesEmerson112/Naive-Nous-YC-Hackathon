"""The worker (armsim/worker.py) in the scripted commander's heal step, with no host, no Hermes and no
Ollama: test_dispatch's FakeHost (plus the window, read and handoff ops), the fake Hermes API server
(tests/fake_manager.py) and a fake OpenAI-compatible server for the worker's model (below).
Run from prototypes/arm-sim:

    PYTHONPATH=. .venv/bin/python tests/test_worker.py      (or: python -m pytest tests/)
"""
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import fake_manager  # noqa: E402
from armsim import dispatch, events, orders  # noqa: E402
from armsim.escalation import ESCALATION_SCHEMA, validate  # noqa: E402
from armsim.manager import ManagerClient  # noqa: E402
from armsim.worker import Worker, render_lite, worker_answer  # noqa: E402
from test_dispatch import TMP, FakeHost, fresh, texts  # noqa: E402


class Host(FakeHost):
    """FakeHost plus the ops the worker reads: 6 s of frozen distance rows, the joints, and handoff."""

    def __call__(self, op, **f):
        if op == "window":
            now = 1_762_600_000.0
            rows = [{"ts": now - 6 + i / 10, "t": i * 100, "mm": 130, "us": 757, "st": "ok",
                     "pred_mm": round(131 - i * 0.5, 1)} for i in range(60)]
            self.calls.append((op, None))
            return {"ok": True, "now": now, "until": now, "seconds": 6.0, "rows": rows, "arm": "sim",
                    "kinematics": True}
        if op == "read":
            return {"ok": True, "pos": {"shoulder_pan.pos": 0.2, "shoulder_lift.pos": -92.8, "elbow_flex.pos": 87.9,
                                        "wrist_flex.pos": 56.3, "wrist_roll.pos": 90.8, "gripper.pos": 2.0}}
        if op == "handoff":
            if f.get("open"):                      # host.py Watchdog.hold: open one for a person, or take the open one
                o = f["open"]
                self.calls.append((op, f"open:{o.get('kind')}"))
                was_open = bool(self.open())
                held = self.open()[0] if was_open else self._open(o.get("kind") or "person_needed",
                                                                  o.get("summary") or "", held_for=o.get("of"))
                held.update(handed_off=1.0, handoff_reason=f.get("reason"))
                return {"ok": True, "id": held["id"],
                        "held": "an incident was already open" if was_open else "opened for a person"}
            self.calls.append((op, f.get("id")))
            for i in self.open():
                if i["id"] == f.get("id"):
                    i.update(handed_off=1.0, handoff_reason=f.get("reason"))
                    return {"ok": True, "id": i["id"]}
            return {"ok": False, "error": "no open incident"}
        return super().__call__(op, **f)


def bench(name, mode="good", *, deadline=10, lm=None, **host_kw):
    _, board, log, emit = fresh(name)
    _, card = orders.place(board, "pick", source="test")
    host = Host(**host_kw)
    srv, url = fake_manager.start(mode, delay=0.05)
    label = "fake" if mode == "good" else f"fake-{mode}"
    worker = Worker(ManagerClient(url, label=label, poll_s=0.05), emit=emit, deadline_s=deadline, lm=lm,
                    packet_dir=TMP / f"{name}-packets")
    ex = dispatch.ScriptedExecutor(board, call=host, emit=emit, poll_s=0.01, pod_clear_s=0.05, hold_s=0.05,
                                   worker=worker)
    d = dispatch.Dispatcher(board, ex, call=host, emit=emit, every=0.01, sleep=lambda s: None)
    return board, card, host, log, d, srv


def evs(log, kind=None):
    return [e for e in events.read(log) if kind is None or e["kind"] == kind]


def inc_card(board):
    return next(c for c in board.show() if c["title"].startswith("INC "))


def test_a_stuck_sensor_heals_through_the_manager():
    board, card, host, log, d, srv = bench("good", fail={"approach": "us_stuck"})
    d.run(once=True)
    srv.shutdown()
    c = board.card(card["id"])
    assert c["status"] == "done", c
    said = texts(board, card["id"])
    assert any(t.startswith("PLAN: us_stuck during approach went to the manager (fake)") for t in said), said
    assert said[-1].startswith("RESULT:") and "the manager (fake) planned restart_sensor" in said[-1], said[-1]
    order = [e["kind"] for e in evs(log) if e["kind"] in ("incident", "escalation", "fix_plan", "plan_check",
                                                          "plan_action", "heal", "job_result")]
    assert order == ["incident", "escalation", "fix_plan", "plan_check", "plan_action", "heal", "job_result"], order
    esc_ev, plan_ev = evs(log, "escalation")[0], evs(log, "fix_plan")[0]
    assert esc_ev["src"] == "worker" and plan_ev["src"] == "manager" and esc_ev["card"] == card["id"], esc_ev
    packet = json.loads(Path(esc_ev["packet"]).read_text())
    assert validate(packet, ESCALATION_SCHEMA) == [] and packet["state"]["job"]["failed_step"] == "approach"
    assert packet["window"].count("\nD") == 60 and "INC1 src=watchdog kind=us_stuck" in packet["window"]
    assert evs(log, "plan_action")[0]["verified"] is True
    assert [n for op, n in host.calls if op == "repair"] == ["restart_sensor"], host.calls
    inc = inc_card(board)
    assert inc["status"] == "done" and inc["title"].startswith(f"INC {packet['incident_id']}: us_stuck"), inc
    assert [t.split(":")[0] for t in texts(board, inc["id"])] == ["ESCALATION", "PLAN", "RESULT"], texts(board, inc["id"])


def test_a_plan_off_the_list_is_refused_and_the_scripted_heal_runs():
    board, card, host, log, d, srv = bench("off_list", "off_list", fail={"reach": "us_stuck"})
    d.run(once=True)
    srv.shutdown()
    assert board.card(card["id"])["status"] == "done"
    check = evs(log, "plan_check")[0]
    assert check["ok"] is False and "not on this incident's list" in check["reason"], check
    assert not evs(log, "plan_action")                            # nothing the manager said ran
    result = texts(board, card["id"])[-1]
    assert result.startswith("RESULT:") and "the manager's plan was refused" in result and \
        "the scripted heal: restarted the sensor" in result, result
    assert inc_card(board)["status"] == "done"


def test_bad_json_is_refused_and_falls_back():
    board, card, host, log, d, srv = bench("bad_json", "bad_json", fail={"reach": "us_stuck"})
    d.run(once=True)
    srv.shutdown()
    assert board.card(card["id"])["status"] == "done"
    assert "not one JSON object" in evs(log, "plan_check")[0]["reason"]
    assert "RESULT:" in texts(board, card["id"])[-1]


def test_a_slow_manager_falls_back_at_the_deadline():
    board, card, host, log, d, srv = bench("slow", "slow", deadline=3, fail={"reach": "us_stuck"})
    t0 = time.time()
    d.run(once=True)
    srv.shutdown()
    assert board.card(card["id"])["status"] == "done"
    plan = evs(log, "fix_plan")[0]
    assert plan["ok"] is False and "3 s deadline" in plan["text"] and 2.8 <= plan["seconds"] < 4.5, plan
    assert time.time() - t0 < 10
    assert "no plan from the manager" in texts(board, card["id"])[-1]


def test_an_unreachable_manager_falls_back_at_once():
    # its /v1/toolsets is read before the ask (finding 6 of round 2): unreadable, so it isn't asked at all
    board, card, host, log, d, srv = bench("refused", "refused", fail={"reach": "us_stuck"})
    d.run(once=True)
    assert board.card(card["id"])["status"] == "done"
    err = next(e for e in evs(log, "error") if e["src"] == "worker")
    assert "didn't ask the manager" in err["text"] and "can't be read" in err["text"], err["text"]
    assert not evs(log, "escalation") and not evs(log, "fix_plan")
    assert "no plan from the manager (it wasn't asked" in texts(board, card["id"])[-1]


def test_a_plan_that_touches_safety_parks_and_hands_off():
    board, card, host, log, d, srv = bench("unsafe", "unsafe", fail={"reach": "us_stuck"})
    jobs = d.run(once=True)
    srv.shutdown()
    assert board.card(card["id"])["status"] == "blocked" and jobs[0]["outcome"] == "blocked"
    assert ("handoff", "inc-1") in host.calls and host.incidents[0].get("handed_off"), host.calls
    assert not [c for c in host.calls if c[0] == "repair"], host.calls          # no fallback, nothing ran
    assert "a person takes it" in texts(board, card["id"])[-1]
    assert inc_card(board)["status"] == "blocked"


def test_a_stalled_joint_is_checked_once_then_handed_to_a_person():
    board, card, host, log, d, srv = bench("stall", fail={"reach": "joint_stall"}, fixable=False)
    d.run(once=True)
    srv.shutdown()
    assert board.card(card["id"])["status"] == "blocked"
    assert evs(log, "escalation")[0]["allowed"] == ["verify", "park", "handoff_human", "no_action"]
    assert [n for op, n in host.calls if op == "repair"] == ["verify"], host.calls   # the failed plan is final
    assert "planned verify" in texts(board, card["id"])[-1]


def test_a_bad_pod_image_gets_the_pod_rollback_row():
    # 6 status polls until the fake pod healer finishes: the worker reads the status once more just before
    # the action (finding 2), so 5 would clear the pod before the wait sees podd's flag
    board, card, host, log, d, srv = bench("pod", pod_on={"approach": 6})
    d.run(once=True)
    srv.shutdown()
    c = board.card(card["id"])
    assert c["status"] == "done" and "us_offset (the sensor pod's us_mismatch)" in c["note"], c
    esc = evs(log, "escalation")[0]
    assert esc["allowed"][0] == "pod_rollback", esc
    act = evs(log, "plan_action")[0]          # what ran is the wait for the pod's own healer, not a rollback
    assert act["planned"] == "pod_rollback" and act["action"].startswith("wait_for_pod"), act
    assert ("repair", "restart_sensor") not in host.calls, host.calls
    said = texts(board, card["id"])
    assert "planned pod_rollback, plain code checked it and waited for the sensor pod's own healer (wait_for_pod" \
        in said[-1] and "rolled back" not in said[-1], said[-1]
    assert any(t.startswith("PLAN: us_offset") and "waited for the sensor pod's own healer" in t for t in said), said


def test_manager_needs_the_scripted_commander():
    try:
        dispatch.main(["--exec", "hermes", "--board", "naive", "--manager", "fake"])
        raise AssertionError("--manager with --exec hermes was accepted")
    except SystemExit as exc:
        assert "--exec scripted only" in str(exc), exc


# ---- the worker's model, against a fake OpenAI-compatible server ----------------------------------------
GOOD = {"status": "fault", "kind": "us_frozen", "severity": "critical", "evidence": ["D01-D60", "INC1"],
        "escalate": True, "message": "The wrist sensor reads 130 mm on all 60 rows while pred falls 30 mm: frozen."}


class FakeLM(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, reply, delay=0.0):
        super().__init__(("127.0.0.1", 0), LMHandler)
        self.reply, self.delay, self.bodies = reply, delay, []
        threading.Thread(target=self.serve_forever, daemon=True).start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server_address[1]}/v1"


class LMHandler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        self.server.bodies.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
        time.sleep(self.server.delay)
        reply = self.server.reply
        message = reply if isinstance(reply, dict) else {"role": "assistant", "content": reply}   # dict: tool calls
        code, body = (500, {"error": "boom"}) if reply == 500 else (200, {
            "choices": [{"message": message, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1100, "completion_tokens": 60}})
        raw = json.dumps(body).encode()
        try:
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        except BrokenPipeError:                  # the client gave up at its cap: that's the test
            pass


def window_text():
    win = Host()("window")
    return render_lite(win, status={"grasp": {"holding": False}},
                       incidents=[{"src": "watchdog", "kind": "us_stuck", "severity": "critical", "summary": "stuck"}])


def test_render_lite_ids_are_unique_and_fit():
    text, idmap = window_text()
    ids = [line.split()[0] for line in text.splitlines()[1:]]
    assert len(ids) == len(set(ids)) == 61 and ids[0] == "D01" and ids[-1] == "INC1", ids[-3:]
    assert text.startswith("WINDOW src=arm-sim span=6.0s kinematics=on holding=no") and "pred=131.0" in text
    assert len(text) < 12000 and set(ids) <= set(idmap)


def test_worker_answer_good():
    text, idmap = window_text()
    srv = FakeLM(json.dumps(GOOD))
    ans, why = worker_answer(text, endpoint=srv.url, model="qwen3.5:4b", idmap=idmap)
    srv.shutdown()
    assert ans == GOOD and why.startswith("answered"), (ans, why)
    body = srv.bodies[0]
    assert body["model"] == "qwen3.5:4b" and body["response_format"]["type"] == "json_schema"
    assert body["messages"][0]["role"] == "system" and "Healthy facts" in body["messages"][0]["content"]
    assert body["messages"][1]["content"] == text


def test_worker_answer_malformed():
    text, idmap = window_text()
    for reply, want in (("The sensor looks frozen to me.", "not one JSON object"),
                        (json.dumps({**GOOD, "fix": "restart"}), "unexpected key 'fix'"),
                        (json.dumps({**GOOD, "kind": "gremlins"}), "is not one of"),
                        (json.dumps({**GOOD, "evidence": ["D99"]}), "not in the window"),
                        (500, "HTTP 500")):
        srv = FakeLM(reply)
        ans, why = worker_answer(text, endpoint=srv.url, model="m", idmap=idmap)
        srv.shutdown()
        assert ans is None and want in why, (reply, why)


def test_worker_answer_times_out_at_its_cap():
    text, idmap = window_text()
    srv = FakeLM(json.dumps(GOOD), delay=3.0)
    t0 = time.time()
    ans, why = worker_answer(text, endpoint=srv.url, model="m", idmap=idmap, timeout=1.0)
    secs = time.time() - t0
    srv.shutdown()
    assert ans is None and "past its 1 s cap" in why and secs < 1.8, (why, secs)


def test_the_worker_answer_rides_in_the_packet():
    srv_lm = FakeLM(json.dumps(GOOD))
    board, card, host, log, d, srv = bench("with-lm", fail={"approach": "us_stuck"},
                                           lm={"endpoint": srv_lm.url, "model": "qwen3.5:4b"})
    d.run(once=True)
    srv.shutdown()
    srv_lm.shutdown()
    assert board.card(card["id"])["status"] == "done"
    sent = json.loads(json.loads(next(r for r in srv.requests if r["method"] == "POST")["body"])["input"])
    assert sent["worker"] == GOOD, sent["worker"]
    assert "the worker's model says" in evs(log, "escalation")[0]["text"]


# ---- step 8: --exec-via lm, the worker's model issues the checked call --------------------------------
def tool_call(name, args):
    return {"role": "assistant", "content": "",
            "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}


def exec_via_lm(name, reply, delay=0.0, cap=None):
    srv_lm = FakeLM(reply, delay=delay)
    board, card, host, log, d, srv = bench(name, fail={"approach": "us_stuck"},
                                           lm={"endpoint": srv_lm.url, "model": "qwen3.5:4b"})
    d.exec.worker.exec_via = "lm"
    if cap:
        d.exec.worker.exec_cap_s = cap
    d.run(once=True)
    srv.shutdown()
    srv_lm.shutdown()
    return board, card, host, log, srv_lm


def test_exec_via_lm_the_exact_call_is_allowed():
    # the first POST is the worker's answer (content), the second the call: the fake gives both the same message
    board, card, host, log, srv_lm = exec_via_lm("lm-ok", tool_call("restart_sensor", {}))
    gate = evs(log, "plan_gate")[0]
    assert gate["ok"] is True and "the gate allows it" in gate["text"], gate
    tools = [t["function"]["name"] for t in srv_lm.bodies[-1]["tools"]]
    assert tools == ["restart_sensor", "verify", "park", "handoff_human", "no_action"], tools   # the allowlist only
    assert board.card(card["id"])["status"] == "done"
    assert [n for op, n in host.calls if op == "repair"] == ["restart_sensor"], host.calls


def test_exec_via_lm_a_different_call_is_refused_and_logged():
    board, card, host, log, srv_lm = exec_via_lm("lm-wrong", tool_call("verify", {}))
    gate = evs(log, "plan_gate")[0]
    assert gate["ok"] is False and "the model called verify({}), not the plan's restart_sensor({})" in gate["text"], gate
    assert [n for op, n in host.calls if op == "repair"] == ["restart_sensor"], host.calls   # plain code ran the plan
    assert board.card(card["id"])["status"] == "done"


def test_exec_via_lm_no_call_in_time_plain_code_issues_it():
    board, card, host, log, srv_lm = exec_via_lm("lm-slow", tool_call("restart_sensor", {}), delay=2.5, cap=1.0)
    gate = evs(log, "plan_gate")[0]
    assert gate["ok"] is False and "no call from the worker's model within 1 s" in gate["text"], gate
    assert board.card(card["id"])["status"] == "done" and ("repair", "restart_sensor") in host.calls


# ---- the review's findings (worker/manager adversarial review) -----------------------------------------
class Plan:
    """A manager client that answers one fixed action (no HTTP); `on_ask(esc)` runs before it answers."""
    label = "stub"

    def __init__(self, action, on_ask=None):
        self.action, self.on_ask, self.asks = action, on_ask, []

    def ask(self, esc, deadline_s=None):
        self.asks.append(esc["incident_id"])
        if self.on_ask:
            self.on_ask(esc)
        return json.dumps({"schema": "arm-fixplan/v1", "incident_id": esc["incident_id"], "action": self.action,
                           "args": {}, "kind": "us_stuck", "confidence": 0.5, "touches_safety": False,
                           "why": "stub"}), "ok"


def rig(name, client, *, orders_n=1, emit_hook=None, **host_kw):
    """A dispatcher with a worker on `client` (a ManagerClient or a stub) and n orders placed."""
    _, board, log, emit = fresh(name)
    cards = [orders.place(board, "pick", source="test")[1] for _ in range(orders_n)]
    host = host_kw.pop("host", None) or Host(**host_kw)
    if emit_hook:
        base = emit

        def emit(src, kind, text, **f):
            emit_hook(src, kind, f)
            return base(src, kind, text, **f)
    worker = Worker(client, emit=emit, deadline_s=10, packet_dir=TMP / f"{name}-packets")
    ex = dispatch.ScriptedExecutor(board, call=host, emit=emit, poll_s=0.01, pod_clear_s=0.05, hold_s=0.05,
                                   worker=worker)
    d = dispatch.Dispatcher(board, ex, call=host, emit=emit, every=0.01, sleep=lambda s: None)
    return board, cards, host, log, d


def test_a_manager_park_is_sticky_the_next_order_waits_for_a_person():
    # finding 3 (the reviewer's park_probe.py, made a test): park hands the incident to a person, so the
    # next orders can't un-park the arm through the scripted heal
    client = Plan("park")
    board, cards, host, log, d = rig("park", client, orders_n=3, fail={"approach": "us_stuck"})
    for _ in range(3):
        d.run(once=True)
    assert [board.card(c["id"])["status"] for c in cards] == ["blocked", "todo", "todo"], \
        [board.card(c["id"])["status"] for c in cards]
    assert len(client.asks) == 1 and ("handoff", "inc-1") in host.calls, (client.asks, host.calls)
    assert host.incidents[0]["state"] == "open" and host.incidents[0]["handed_off"], host.incidents   # a person has it
    assert not [c for c in host.calls if c[0] == "repair"], host.calls          # no scripted heal ran
    act = evs(log, "plan_action")[0]
    assert act["action"] == "park" and act["ok"] is False, act
    assert any(e["kind"] == "waiting" and "waiting for a person" in e["text"] for e in evs(log)), evs(log, "waiting")
    assert "for a person" in texts(board, cards[0]["id"])[-1]


def test_the_plan_is_checked_again_just_before_it_runs():
    # finding 2: the incident closes after the check (the pod's healer, the host's own check), before the action
    def close_after_check(src, kind, f):
        if kind == "plan_check" and f.get("ok"):
            for i in host.open():
                i["state"] = "resolved"
    host = Host(fail={"approach": "us_stuck"})
    board, cards, host, log, d = rig("stale-closed", Plan("restart_sensor"), host=host, emit_hook=close_after_check)
    d.run(once=True)
    assert board.card(cards[0]["id"])["status"] == "done"
    assert not [c for c in host.calls if c[0] == "repair"], host.calls          # nothing ran
    act = evs(log, "plan_action")[0]
    assert act["action"] == "none" and act["planned"] == "restart_sensor" and act["stale"] == "closed", act
    said = texts(board, cards[0]["id"])
    assert any(t.startswith("PLAN:") and "nothing ran" in t for t in said) and "nothing ran" in said[-1], said


def test_a_plan_whose_incident_went_to_a_person_meanwhile_runs_nothing():
    def hand_off_after_check(src, kind, f):
        if kind == "plan_check" and f.get("ok"):
            host.open()[0].update(handed_off=1.0, handoff_reason="Olir took the pod off the bench")
    host = Host(fail={"approach": "us_stuck"})
    board, cards, host, log, d = rig("stale-handed", Plan("restart_sensor"), host=host, emit_hook=hand_off_after_check)
    jobs = d.run(once=True)
    assert board.card(cards[0]["id"])["status"] == "blocked" and jobs[0]["outcome"] == "blocked"
    assert not [c for c in host.calls if c[0] in ("repair", "handoff")], host.calls   # no fallback either
    act = evs(log, "plan_action")[0]
    assert act["action"] == "none" and act["stale"] == "handed" and act["ok"] is False, act
    assert "Olir took the pod off the bench" in texts(board, cards[0]["id"])[-1]


def test_the_recheck_comes_after_the_exec_via_lm_gate():
    # the gate can take up to 15 s: the status read that decides must come after it, right before the action
    srv_lm = FakeLM(tool_call("restart_sensor", {}))

    def close_at_gate(src, kind, f):
        if kind == "plan_gate":
            for i in host.open():
                i["state"] = "resolved"
    host = Host(fail={"approach": "us_stuck"})
    board, cards, host, log, d = rig("stale-gate", Plan("restart_sensor"), host=host, emit_hook=close_at_gate)
    d.exec.worker.lm, d.exec.worker.exec_via = {"endpoint": srv_lm.url, "model": "m"}, "lm"
    d.run(once=True)
    srv_lm.shutdown()
    assert evs(log, "plan_gate")[0]["ok"] is True and evs(log, "plan_action")[0]["action"] == "none"
    assert not [c for c in host.calls if c[0] == "repair"], host.calls


def test_the_plan_comments_go_on_the_cards_after_the_action():
    order = []

    class Seq(Host):
        def __call__(self, op, **f):
            if op == "repair":
                order.append(("repair", f.get("action")))
            return super().__call__(op, **f)
    host = Seq(fail={"approach": "us_stuck"})
    board, cards, host, log, d = rig("order", Plan("restart_sensor"), host=host)
    base = board.comment

    def comment(cid, text, **kw):
        if text.startswith("PLAN:"):
            order.append(("PLAN", cid))
        return base(cid, text, **kw)
    board.comment = comment
    d.run(once=True)
    assert order[0] == ("repair", "restart_sensor") and [o[0] for o in order[1:]] == ["PLAN", "PLAN"], order


def test_a_repeated_fix_refused_by_the_check_is_not_run_by_the_fallback():
    # finding 11: the host's record says restart_sensor already ran on this incident and failed its check; the
    # manager plans it again; the check refuses it, and the scripted fallback must not run it either
    class Tried(Host):
        def _open(self, kind, summary, **extra):
            return super()._open(kind, summary, actions=[{"tool": "arm_restart_sensor", "ok": True}], **extra)
    board, cards, host, log, d = rig("repeat", Plan("restart_sensor"), host=Tried(fail={"approach": "us_stuck"}))
    d.run(once=True)
    assert "not tried again" in evs(log, "plan_check")[0]["reason"]
    assert [n for op, n in host.calls if op == "repair"] == ["verify"], host.calls   # the next fix, not the same one
    assert board.card(cards[0]["id"])["status"] == "done"
    assert "won't run restart_sensor again either" in texts(board, cards[0]["id"])[-1]


def test_when_every_scripted_fix_was_refused_as_a_repeat_a_person_takes_it():
    class Tried(Host):
        def _open(self, kind, summary, **extra):
            return super()._open(kind, summary, actions=[{"tool": "arm_verify", "ok": True}], **extra)
    board, cards, host, log, d = rig("repeat-all", Plan("verify"), host=Tried(fail={"reach": "joint_stall"}))
    d.run(once=True)
    assert not [c for c in host.calls if c[0] == "repair"] and ("handoff", "inc-1") in host.calls, host.calls
    assert board.card(cards[0]["id"])["status"] == "blocked"
    assert evs(log, "heal")[0]["action"] == "handoff_human"


def test_podd_gets_its_hold_once_on_a_fallback():
    # nit 12: us_offset may be the pod's; the worker gives podd hold_s, and a fallback mustn't wait again
    srv, url = fake_manager.start("refused")
    board, cards, host, log, d = rig("hold", ManagerClient(url, poll_s=0.05), fail={"approach": "us_offset"})
    held = []
    real = d.exec._hold_for_pod
    d.exec._hold_for_pod = lambda inc: held.append(inc.get("id")) or real(inc)
    d.run(once=True)
    assert held == ["inc-1"], held
    assert board.card(cards[0]["id"])["status"] == "done" and [n for op, n in host.calls if op == "repair"] == \
        ["restart_sensor"], host.calls


def test_execute_never_fakes_a_redo_step():
    # nit 12: the unreachable redo_step branch moved the arm and reported success; it now runs nothing
    _, board, log, emit = fresh("dead-redo")
    host = Host()
    inc = host._open("us_stuck", "stuck")
    ex = dispatch.ScriptedExecutor(board, call=host, emit=emit)
    ex.ctx = {"card": "crd_x"}
    w = Worker(Plan("verify"), emit=emit, packet_dir=TMP / "dead-redo-packets")
    plan = {"action": "redo_step", "args": {"from_step": "approach"}, "why": "w"}
    w.cur = {}
    assert w.execute(plan, ex, inc, "grip") is None          # round 2, finding 7: not on a us_stuck incident's list
    assert w.cur["stale"][0] == "stale" and "redo_step is no longer on inc-1's list" in w.cur["stale"][1], w.cur
    w.cur, w._recheck = {}, lambda *a, **k: None              # past the recheck, the branch itself runs nothing
    ok, action, detail = w.execute(plan, ex, inc, "grip")
    assert ok is False and action == "redo_step" and "nothing ran" in detail, detail
    assert not [c for c in host.calls if c[0] == "skill"], host.calls


def test_manager_hermes_refuses_to_start_unless_its_tools_are_off():
    # finding 1: /v1/toolsets read before the first ask; anything that can act, or an unreadable answer, refuses
    import argparse
    from armsim import worker as worker_mod

    def args(url):
        return argparse.Namespace(manager="hermes", manager_url=url, fake_mode="good", worker_lm="off",
                                  manager_deadline=90.0, exec_via="code")
    srv, url = fake_manager.start("good")
    try:
        assert isinstance(worker_mod.make(args(url)), Worker)                 # clean: delegation and memory only
    finally:
        srv.shutdown()
    srv, url = fake_manager.start("good", toolsets="dirty")
    try:
        try:
            dispatch.main(["--manager", "hermes", "--manager-url", url])
            raise AssertionError("a manager with terminal and so101_arm on was accepted")
        except SystemExit as exc:
            msg = str(exc)
            assert "won't start" in msg and "terminal is enabled" in msg and "so101_arm is enabled" in msg, msg
            assert "hermes-manager/README.md" in msg and "check.sh" in msg, msg
        assert not srv.stops and not [r for r in srv.requests if r["path"] == "/v1/runs"]   # never asked
    finally:
        srv.shutdown()
    for mode, toolsets in (("refused", "clean"), ("good", "missing")):
        srv, url = fake_manager.start(mode, toolsets=toolsets)
        try:
            worker_mod.make(args(url))
            raise AssertionError(f"an unreadable /v1/toolsets ({mode}, {toolsets}) was accepted")
        except SystemExit as exc:
            assert "fails closed" in str(exc) and "check.sh" in str(exc), exc
        finally:
            if srv:
                srv.shutdown()


# ---- round 2 of the review (three verifiers on the fixes) ---------------------------------------------
class Thinking:
    """A manager client that, while it "thinks", lets `meanwhile(host)` change the host, then answers:
    "bad" (not JSON), "none" (no plan in time) or an action (a plan for it)."""
    label = "stub"

    def __init__(self, host, answer, meanwhile):
        self.host, self.answer, self.meanwhile, self.asks = host, answer, meanwhile, []

    def ask(self, esc, deadline_s=None):
        self.asks.append(esc["incident_id"])
        self.meanwhile(self.host)
        if self.answer == "bad":
            return "Sure! {not json", "completed"
        if self.answer == "none":
            return None, "no answer within the 10 s deadline"
        return json.dumps({"schema": "arm-fixplan/v1", "incident_id": esc["incident_id"], "action": self.answer,
                           "args": {}, "kind": "us_stuck", "confidence": 0.5, "touches_safety": False,
                           "why": "stub"}), "completed"


def person_takes_it(host):
    for i in host.open():
        i.update(handed_off=1.0, handoff_reason="Mission Control: a person took it during the think")


def test_a_person_who_takes_the_incident_while_the_manager_thinks_keeps_it():
    # round 2, finding 1 (blocker): after a refused, missing or late plan the scripted fallback must not repair
    # an incident a person took meanwhile (the reviewer's p1_handoff_then_fallback.py, made a test)
    for answer in ("bad", "none", "pod_rollback", "verify"):     # not JSON, no plan, off the list, a checked plan
        host = Host(fail={"approach": "us_stuck"})
        board, cards, host, log, d = rig(f"think-{answer}", Thinking(host, answer, person_takes_it), orders_n=2,
                                         host=host)
        d.run(once=True)
        d.run(once=True)
        assert [board.card(c["id"])["status"] for c in cards] == ["blocked", "todo"], (answer, host.calls)
        assert not [c for c in host.calls if c[0] == "repair"], (answer, host.calls)      # nothing repaired it
        assert host.incidents[0]["state"] == "open" and host.incidents[0]["handed_off"], host.incidents
        blocked = texts(board, cards[0]["id"])[-1]
        assert "a person took it during the think" in blocked and "it needs a person" in blocked, (answer, blocked)


def test_an_incident_that_closes_while_the_manager_thinks_needs_nothing_more():
    # the fixer's open item: a closed incident found after the manager's round trip runs no scripted fix
    def close(host):
        for i in host.open():
            i["state"] = "resolved"
    host = Host(fail={"approach": "us_stuck"})
    board, cards, host, log, d = rig("think-closed", Thinking(host, "bad", close), host=host)
    d.run(once=True)
    assert board.card(cards[0]["id"])["status"] == "done"
    assert not [c for c in host.calls if c[0] == "repair"], host.calls
    heal = evs(log, "heal")[0]
    assert heal["ok"] and heal["action"] == "none" and "closed on the arm host while the manager thought" in heal["text"]


def test_a_worker_that_fails_after_the_action_runs_nothing_else():
    # round 2, finding 3: an exception after execute() issued the plan's action must not become a second,
    # unplanned action (the reviewer's p2_exception_after_action.py: the plan_action event raises OSError)
    def disk_full(src, kind, f):
        if src == "worker" and kind == "plan_action":
            raise OSError(28, "No space left on device")
    for action, fixable, want_cards in (("restart_sensor", True, ["blocked", "done"]),     # it verified: closed
                                        ("restart_sensor", False, ["blocked", "todo"]),    # still open: a person
                                        ("park", True, ["blocked", "todo"]),
                                        ("handoff_human", True, ["blocked", "todo"])):
        board, cards, host, log, d = rig(f"fails-{action}-{fixable}", Plan(action), orders_n=2, emit_hook=disk_full,
                                         fail={"approach": "us_stuck"}, fixable=fixable)
        d.run(once=True)
        d.run(once=True)
        statuses = [board.card(c["id"])["status"] for c in cards]
        assert statuses == want_cards, (action, fixable, statuses, host.calls)
        repairs = [n for op, n in host.calls if op == "repair"]
        assert repairs == (["restart_sensor"] if action == "restart_sensor" else []), (action, repairs)
        if want_cards[1] == "todo":
            assert host.incidents[0]["state"] == "open" and host.incidents[0]["handed_off"], host.incidents
        assert "the worker failed after the manager's" in texts(board, cards[0]["id"])[-1]


def test_a_full_disk_on_a_plan_comment_does_not_cost_the_park():
    # round 2, finding 3: a FileBoard write that raises OSError (not BoardError) is logged, never fatal
    board, cards, host, log, d = rig("flaky-board", Plan("park"), orders_n=2, fail={"approach": "us_stuck"})
    base = board.comment

    def comment(cid, text, **kw):
        if text.startswith("PLAN:"):
            raise OSError(28, "No space left on device")
        return base(cid, text, **kw)
    board.comment = comment
    d.run(once=True)
    d.run(once=True)
    assert [board.card(c["id"])["status"] for c in cards] == ["blocked", "todo"], host.calls
    assert not [c for c in host.calls if c[0] == "repair"], host.calls
    assert any("couldn't comment PLAN" in e["text"] and "OSError" in e["text"] for e in evs(log, "error"))


def test_a_park_whose_incident_closed_first_is_held_for_a_person():
    # round 2, finding 4: the incident closes after the last check and before the hand-off lands (the host's
    # queued verify ran in between); the hand-off fails, so the host opens one, already handed off
    class Race(Host):
        def __call__(self, op, **f):
            if op == "handoff" and f.get("id") and not f.get("open"):
                for i in self.open():
                    i["state"] = "resolved"
            return super().__call__(op, **f)
    board, cards, host, log, d = rig("park-race", Plan("park"), orders_n=2, host=Race(fail={"approach": "us_stuck"}))
    d.run(once=True)
    d.run(once=True)
    assert [board.card(c["id"])["status"] for c in cards] == ["blocked", "todo"], host.calls
    assert ("handoff", "inc-1") in host.calls and ("handoff", "open:us_stuck") in host.calls, host.calls
    held = host.incidents[-1]
    assert held["id"] == "inc-2" and held["state"] == "open" and held["handed_off"] and held["held_for"] == "inc-1"
    assert "so the host opened inc-2 for the person" in evs(log, "plan_action")[0]["text"]
    host("repair", action="verify")                               # the person checks the arm at home
    d.run(once=True)
    assert board.card(cards[1]["id"])["status"] == "done"


def test_a_hand_off_the_host_does_not_take_is_kept_until_it_does():
    # round 2, finding 4: the host doesn't answer the hand-off; no job starts until it has it, then the
    # dispatcher waits for the person as for any hand-off
    class Deaf(Host):
        down = True

        def __call__(self, op, **f):
            if op == "handoff" and self.down:
                self.calls.append((op, "unreachable"))
                return {"ok": False, "unreachable": True, "error": "the arm host isn't answering"}
            return super().__call__(op, **f)
    host = Deaf(fail={"approach": "us_stuck"})
    board, cards, host, log, d = rig("deaf", Plan("handoff_human"), orders_n=2, host=host)
    d.run(once=True)
    assert d.exec.holds and d.exec.holds[0]["incident"] == "inc-1", d.exec.holds
    d.run(once=True)                                              # still deaf: no job
    assert board.card(cards[1]["id"])["status"] == "todo"
    assert any(e["kind"] == "waiting" and "didn't take yet" in e["text"] for e in evs(log)), evs(log, "waiting")
    host.down = False
    d.run(once=True)                                              # delivered: now it waits for the person
    assert not d.exec.holds and host.incidents[0]["handed_off"], host.incidents
    assert board.card(cards[1]["id"])["status"] == "todo"
    host("repair", action="verify")
    d.run(once=True)
    assert board.card(cards[1]["id"])["status"] == "done"


def test_the_manager_is_not_asked_once_its_tools_can_act():
    # round 2, finding 6 (the reviewer's p8_preflight_once.py): the toolsets are read before every ask, not
    # only at startup; a manager whose gateway came back with terminal and so101_arm on isn't asked
    from armsim.worker import preflight
    srv, url = fake_manager.start("good", delay=0.05)
    try:
        client = ManagerClient(url, label="fake-hermes", poll_s=0.05)
        preflight(client)                                         # clean at startup
        srv.toolsets = "dirty"                                    # the gateway restarted with another config
        board, cards, host, log, d = rig("tools-turned", client, fail={"approach": "us_stuck"})
        d.run(once=True)
        assert not [r for r in srv.requests if r["method"] == "POST" and r["path"] == "/v1/runs"], srv.requests
        assert board.card(cards[0]["id"])["status"] == "done"     # the scripted heal ran
        err = next(e for e in evs(log, "error") if "didn't ask the manager" in e["text"])
        assert "terminal is enabled" in err["text"] and "so101_arm is enabled" in err["text"], err["text"]
        assert len([r for r in srv.requests if r["path"] == "/v1/toolsets"]) == 2      # startup, then the ask
    finally:
        srv.shutdown()


def test_a_plan_whose_action_left_the_list_meanwhile_falls_back_to_the_pod_wait():
    # round 2, finding 7 (the reviewer's p5_action_no_longer_allowed.py): podd flags the pod while the manager
    # thinks; restart_sensor was allowed in the packet but isn't for the incident as it is now
    def pod_flags(host):
        host.pod_crit = [{"kind": "us_mismatch", "severity": "critical", "summary": "distance disagrees with its echo"}]
        host.pod_polls = 6                                        # the pod's own healer fixes it a little later
        for i in host.open():
            i["pod_incidents"] = ["us_mismatch-1"]
    host = Host(fail={"approach": "us_stuck"})
    board, cards, host, log, d = rig("left-list", Thinking(host, "restart_sensor", pod_flags), host=host)
    d.run(once=True)
    assert ("repair", "restart_sensor") not in host.calls, host.calls
    late = [e for e in evs(log, "plan_check") if e.get("stage") == "before_action"]
    assert late and "restart_sensor is no longer on inc-1's list" in late[0]["reason"], evs(log, "plan_check")
    assert board.card(cards[0]["id"])["status"] == "done"
    assert evs(log, "heal")[0]["action"].startswith("wait_for_pod"), evs(log, "heal")


def test_the_pod_healing_itself_does_not_give_a_parked_arm_back():
    # round 2, finding 2 (blocker), the dispatcher's side: a manager park on bad image 07's incident; the pod's
    # own healer then rolls back, and the next order still waits for the person (FakeHost mirrors host.py)
    # 6 status polls: 4 come before the action (the heal, the worker, the check's state, the recheck), so the
    # pod heals after the park, while the dispatcher polls for the next order
    board, cards, host, log, d = rig("park-pod", Plan("park"), orders_n=2, pod_on={"approach": 6})
    for _ in range(6):
        d.run(once=True)
    assert evs(log, "plan_action")[0]["action"] == "park", evs(log, "plan_action")
    assert [board.card(c["id"])["status"] for c in cards] == ["blocked", "todo"], host.calls
    assert host.pod_crit == [] and host.pod_polls is None, (host.pod_crit, host.pod_polls)     # the pod healed
    assert host.incidents[0]["state"] == "open" and host.incidents[0]["handed_off"], host.incidents
    host("repair", action="verify")                               # a person's check at home closes it
    d.run(once=True)
    assert board.card(cards[1]["id"])["status"] == "done"


class TinyArm:
    """What host.Watchdog touches on an arm: rows, a clock, halt_reason, and the home move (recorded)."""

    def __init__(self):
        self.watchers, self.d_rows, self.t, self.halt_reason, self._parking, self.moves = [], [], 100.0, None, False, []

    def goto(self, pose, **kw):
        self.moves.append(pose)
        return {"arrived": True}

    def hold(self, seconds):
        for i in range(int(seconds * 10)):                       # healthy rows: the echo time jitters
            self.t += 0.1
            self.d_rows.append({"ts": round(self.t, 2), "t": int(self.t * 1000), "mm": 103, "us": 600 + i % 3, "st": "ok"})

    def status(self):
        return [0] * 6

    def grasp_check(self, **kw):
        return {"holding": False, "gripper": 0.0, "load_nm": 0.0}


class TinyPod:
    open = []

    def note(self, *a, **k):
        pass


def test_the_host_never_moves_the_arm_on_its_own_for_a_handed_off_incident():
    # round 2, finding 2 (blocker), host.py itself: no verify is queued for a handed-off incident once its pod
    # incidents clear, a verify queued before the hand-off is skipped (auto), and a person's repair still works
    import os
    import types
    os.environ.setdefault("ARMSIM_SPEAK", "off")
    from armsim.host import Watchdog
    arm = TinyArm()
    dog = Watchdog(arm, types.SimpleNamespace(run_id="test"), clock=lambda: arm.t, pod=TinyPod())
    dog.home = "HOME"
    crit = [{"id": "us_mismatch-1", "kind": "us_mismatch", "severity": "critical", "summary": "echo disagrees"}]
    dog.on_pod_incidents(crit)                                    # podd flags the pod: inc-1 opens, tied to it
    (inc,) = dog.open()
    assert inc["kind"] == "pod_us_mismatch" and inc["pod_incidents"] == ["us_mismatch-1"], inc
    assert dog.handoff("inc-1", "the manager chose to keep the arm parked")["ok"]
    dog.on_pod_incidents([])                                      # the pod's own healer rolled back
    assert dog.verify_pending is None and not inc.get("pod_healed"), (dog.verify_pending, inc)
    dog.verify_pending = "pod healed: echo disagrees"            # queued just before the hand-off landed
    r = dog.verify(dog.verify_pending, auto=True)                 # what the host's chores run
    assert r["verified"] is False and "handed to a person" in r["skipped"] and arm.moves == [], (r, arm.moves)
    assert inc["state"] == "open" and dog.verify_pending is None
    r = dog.verify("repair: verify")                              # a person's check at home (not auto)
    assert r["verified"] and r["resolved"] == ["inc-1"] and arm.moves == ["HOME"], (r, arm.moves)
    # and an incident nobody handed off still heals on its own, as before
    dog.on_pod_incidents(crit + [{**crit[0], "id": "us_mismatch-2"}])
    (inc2,) = dog.open()
    dog.on_pod_incidents([])
    assert dog.verify_pending and dog.verify(dog.verify_pending, auto=True)["resolved"] == [inc2["id"]]
    # the hold op: a person is needed with nothing open (a wrong action); it opens one, handed off at once
    r = dog.hold({"kind": "grasp_missed", "summary": "grip closed on nothing", "src": "dispatcher"}, "the redo gave up")
    (held,) = dog.open()
    assert r["ok"] and r["held"] == "opened for a person" and held["kind"] == "grasp_missed" and held["handed_off"], r
    assert arm.halt_reason == "grasp_missed" and held["severity"] == "warn"
    r = dog.hold({"kind": "x", "summary": "y"}, "again")          # one already open: that one is handed off
    assert r["id"] == held["id"] and len(dog.open()) == 1, r


def test_a_bad_manager_deadline_refuses_to_start():
    # finding 7: outside 3-120 every packet fails its schema and every heal would silently fall back
    for bad in ("2", "121", "0"):
        try:
            dispatch.main(["--manager", "fake", "--manager-deadline", bad])
            raise AssertionError(f"--manager-deadline {bad} was accepted")
        except SystemExit as exc:
            assert "--manager-deadline must be 3-120 s" in str(exc), exc


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            t0 = time.time()
            fn()
            print(f"ok  {name}  ({time.time() - t0:.1f} s)")
