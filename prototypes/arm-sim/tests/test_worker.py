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
    board, card, host, log, d, srv = bench("refused", "refused", fail={"reach": "us_stuck"})
    d.run(once=True)
    assert board.card(card["id"])["status"] == "done"
    assert "isn't reachable" in evs(log, "fix_plan")[0]["text"]


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
    w.cur = {}
    ok, action, detail = w.execute({"action": "redo_step", "args": {"from_step": "approach"}, "why": "w"}, ex, inc, "grip")
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
