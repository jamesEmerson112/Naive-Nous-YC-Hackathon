"""Offline tests for the loop, the gate, blind mode, both drivers and the scorer (no bench, no API).

  firmware/harness/.venv/bin/python -m pytest firmware/harness/tests -q
"""
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import agent  # noqa: E402
import evaluate  # noqa: E402

TOOLS = json.loads((agent.FIRMWARE / "healer" / "tools.json").read_text())


class FakeBench:
    """Answers heal calls from a dict of canned results and records every call in order."""
    target = "fake"
    timeout = 180

    def __init__(self, results=None, files=None):
        self.results = results or {}
        self.files = {"healer/tools.json": json.dumps(TOOLS)} | (files or {})
        self.calls, self.ops = [], []

    def heal(self, name, args):
        self.calls.append((name, args))
        r = self.results.get(name, {"ok": True})
        r = r(args) if callable(r) else r
        return (1 if isinstance(r, dict) and r.get("ok") is False else 0), json.dumps(r), "", 0.01

    def heal_json(self, name, args=None):
        return json.loads(self.heal(name, args or {})[1])

    def read(self, rel):
        return self.files.get(rel, "")

    def scenario(self, *words):
        self.ops.append(words)
        return 0, "", "", 0.0


class ScriptedDriver:
    """Plays back turns; records what the loop sends back."""
    name, model = "scripted", "none"

    def __init__(self, turns):
        self.turns, self.results = list(turns), []

    def start(self, system, tools, kickoff):
        self.system, self.tools, self.kickoff = system, tools, kickoff

    def step(self):
        return self.turns.pop(0)

    def add_results(self, results):
        self.results.append(results)


def turn(*calls, text=""):
    return agent.Turn(text=text, thinking="", calls=[("c%d" % i, n, a) for i, (n, a) in enumerate(calls)],
                      stop="tool_use" if calls else "end_turn", usage={"input_tokens": 10, "output_tokens": 5})


def bench_with_system():
    return FakeBench({"system_info": {"text": "BRIEFING"}, "incidents": {"open": []}})


def test_tools_keep_file_order_and_core_is_smaller():
    b = FakeBench()
    assert [t["name"] for t in agent.load_tools(b)] == [t["name"] for t in TOOLS]
    core = agent.load_tools(b, "core")
    assert [t["name"] for t in core] == [t["name"] for t in TOOLS if t["name"] in agent.CORE_TOOLS]
    assert "system_info" not in {t["name"] for t in core} and len(core) < len(TOOLS)
    assert all("risk" not in t for t in agent.api_tools(core))


def test_calls_run_in_order_and_results_come_back_together(tmp_path):
    b = bench_with_system()
    d = ScriptedDriver([turn(("triage", {}), ("status", {}), ("note", {"text": "x"})), turn(text="done")])
    s = agent.run_agent(b, d, tmp_path, kickoff="go")
    # the agent's three calls in order, then the harness's own device check at the end (triage, not shown to it)
    assert [c[0] for c in b.calls if c[0] in ("triage", "status", "note")] == ["triage", "status", "note", "triage"]
    assert len(d.results) == 1 and [r[0] for r in d.results[0]] == ["c0", "c1", "c2"]
    assert d.system.startswith("BRIEFING") and "Operating rules" in d.system and "ask_expert" in d.system
    assert "ask_expert" in [t["name"] for t in d.tools]
    assert s["stop"] == "end_turn" and s["tool_calls"] == 3 and (tmp_path / "report.md").read_text() == "done\n"
    recs = [json.loads(line) for line in open(tmp_path / "transcript.jsonl")]
    assert [r["type"] for r in recs] == ["run_start", "model_turn", "tool_call", "tool_call", "tool_call",
                                         "model_turn", "device_check", "run_end"]
    assert "podd_incidents" in recs[0] and "chip_at_start" in recs[0]
    assert set(recs[-2]) >= {"chip", "findings", "open_incidents"}


def test_ask_expert_is_recorded_and_says_nobody_answers(tmp_path):
    b = bench_with_system()
    d = ScriptedDriver([turn(("ask_expert", {"question": "why does mm stick?", "tried": "rolled back"})),
                        turn(("ask_expert", {})), turn(text="done")])
    agent.run_agent(b, d, tmp_path, kickoff="go")
    calls = [json.loads(line) for line in open(tmp_path / "transcript.jsonl")]
    calls = [r for r in calls if r["type"] == "tool_call"]
    assert [(c["name"], c["risk"], c["gate"]) for c in calls] == [("ask_expert", "notify", "allow")] * 2
    assert '"answered": false' in calls[0]["result"] and not calls[0]["is_error"]
    assert calls[1]["is_error"] and "question is required" in calls[1]["result"]
    assert not any(c[0] == "ask_expert" for c in b.calls)       # a harness tool: never sent to the bench


def test_flash_cap_unknown_tools_and_isolation(tmp_path):
    b = bench_with_system()
    flash = ("fw_flash", {"image": "latest"})
    d = ScriptedDriver([turn(flash, flash, flash, flash, ("bash", {"cmd": "ls"}),
                             ("pod_cmd", {"command": "inject clear"}),
                             ("src_read", {"path": "../../demo/x"}),
                             ("fw_symbolize", {"address": "0x0", "image": "/home/pi/firmware/demo/a.elf"}),
                             ("src_show", {"rev": "--output=/tmp/x"}),
                             ("src_read", {"path": "src/main.c"})),
                        turn(text="report")])
    s = agent.run_agent(b, d, tmp_path, kickoff="go", flash_cap=3)
    assert [c[0] for c in b.calls].count("fw_flash") == 3
    assert s["flashes_allowed"] == 3 and s["invalid"] == 1 and s["forbidden"] == 5
    errors = [r for r in d.results[0] if r[3]]
    assert len(errors) == 6 and all("refused by the harness" in r[2] for r in errors)
    assert ("src_read", {"path": "src/main.c"}) in b.calls


def test_long_results_keep_the_tail(tmp_path):
    big = {"text": "A" * 50_000, "triage_after": ["tail-marker"]}
    b = bench_with_system()
    b.results["src_read"] = big
    d = ScriptedDriver([turn(("src_read", {"path": "src/main.c"})), turn(text="ok")])
    s = agent.run_agent(b, d, tmp_path, kickoff="go", max_result_chars=1000)
    shown = d.results[0][0][2]
    assert s["truncated"] == 1 and shown.startswith("[harness:") and "tail-marker" in shown
    assert len(shown) < 1200


def test_tool_errors_are_returned_not_raised(tmp_path):
    b = bench_with_system()
    b.results["fw_build"] = {"ok": False, "error": "WorkspaceError: boom"}
    d = ScriptedDriver([turn(("fw_build", {})), turn(text="ok")])
    s = agent.run_agent(b, d, tmp_path, kickoff="go")
    assert s["tool_errors"] == 1 and d.results[0][0][3] is True and "boom" in d.results[0][0][2]


def test_blind_mode_hides_older_records():
    blind = agent.Blind(since=1000.0, since_iso="2026-11-07T21:00:00", t0_local=agent.time.time() - 120)
    assert blind.args("events", {})["minutes"] < 2.2
    assert blind.args("log_search", {"pattern": "x", "minutes": 600})["minutes"] < 2.2
    assert blind.args("telemetry_recent", {"kind": "E", "seconds": 99999})["seconds"] < 130
    assert blind.args("telemetry_stats", {"seconds": 5}) == {"seconds": 5}
    imgs = {"images": [{"crc": "OLDFIX", "created": 10, "status": "candidate", "notes": []},
                       {"crc": "GOOD", "created": 5, "status": "known-good", "notes": [{"ts": 1, "note": "old"}]},
                       {"crc": "REL", "created": 6, "status": "candidate",
                        "notes": [{"ts": 900, "note": "fixed by agent: MG_PER_DIGIT"}, {"ts": 1001, "note": "v1.5 release"}]},
                       {"crc": "NEW", "created": 1100, "status": "candidate", "notes": []}],
            "deployments": [{"ts": 950, "crc": "OLDFIX", "result": "ok", "reason": "fix 05"},
                            {"ts": 1002, "crc": "REL", "result": "unverified", "reason": "operator deploy v1.5"}]}
    out = blind.result("fw_images", {}, imgs)
    assert [m["crc"] for m in out["images"]] == ["GOOD", "REL", "NEW"]
    assert [d["crc"] for d in out["deployments"]] == ["REL"]
    assert out["images"][1]["notes"] == [{"ts": 1001, "note": "v1.5 release"}] and out["images"][0]["notes"] == []
    new_row = "2026-11-07T21:00:03.000 E ev=boot crc=REL"
    rows = {"rows": ["2026-11-07T20:59:59.000 E ev=boot crc=OLDFIX", new_row]}
    assert blind.result("log_search", {"log": "serial"}, rows)["rows"] == [new_row]


def test_kickoff_is_neutral():
    text = agent.kickoff_text([{"id": "acc_gravity-1", "kind": "acc_gravity", "severity": "warn",
                                "summary": "Board is at rest but measures 529 mg", "first_evidence": {"ratio": 0.53},
                                "opened": 1.0, "last_seen": 2.0}])
    assert text.startswith("podd opened these incidents:") and "acc_gravity" in text and '"opened"' not in text
    assert "v1.5" not in text and "scenario" not in text.lower()
    assert agent.kickoff_text([]).startswith("podd reports no open incidents")


def fake_claude(monkeypatch, content):
    """A ClaudeDriver whose stream returns one message with this content; `sent` gets the request."""
    pytest.importorskip("anthropic")
    from anthropic.types.beta import BetaMessage
    sent = {}

    class FakeStream:
        def __init__(self, **kw):
            sent.update(kw)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get_final_message(self):
            return BetaMessage.model_validate({
                "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
                "stop_reason": "tool_use", "stop_sequence": None,
                "usage": {"input_tokens": 100, "output_tokens": 50, "cache_read_input_tokens": 80,
                          "cache_creation_input_tokens": 0},
                "content": content})

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    d = agent.ClaudeDriver()
    monkeypatch.setattr(d.client.beta.messages, "stream", lambda **kw: FakeStream(**kw))
    d.start("SYS", TOOLS[:3], "go")
    return d, sent


def test_claude_driver_request_and_response_shape(monkeypatch):
    d, sent = fake_claude(monkeypatch, [{"type": "thinking", "thinking": "check triage first", "signature": "sig"},
                                        {"type": "text", "text": "Looking."},
                                        {"type": "tool_use", "id": "tu_1", "name": "triage", "input": {}}])
    t = d.step()
    assert t.calls == [("tu_1", "triage", {})] and t.thinking == "check triage first" and t.text == "Looking."
    assert t.usage["cache_read_input_tokens"] == 80
    assert sent["model"] == "claude-opus-5-5" and sent["output_config"] == {"effort": "high"}
    assert sent["fallbacks"] == "default" and sent["betas"] == ["server-side-fallback-2026-07-01"]
    assert sent["cache_control"] == {"type": "ephemeral"} and sent["thinking"]["type"] == "adaptive"
    assert all(set(x) == {"name", "description", "input_schema"} for x in sent["tools"])
    d.add_results([("tu_1", "triage", "{}", False)])
    assert d.messages[1]["content"][0].type == "thinking"            # history appended unchanged
    assert d.messages[2]["content"][0]["tool_use_id"] == "tu_1"
    assert str(agent.ClaudeDriver(base_url="http://kent.local:8000").client.base_url).startswith("http://kent.local:8000")


def test_claude_driver_drops_declined_blocks_before_a_fallback(monkeypatch):
    fallback = {"type": "fallback", "from": {"model": "claude-opus-5-5"}, "to": {"model": "claude-opus-5"},
                "trigger": {"type": "refusal", "category": None}}
    d, _ = fake_claude(monkeypatch, [{"type": "thinking", "thinking": "declined", "signature": "s0"},
                                     {"type": "tool_use", "id": "tu_0", "name": "status", "input": {}},
                                     {"type": "text", "text": "partial"}, fallback,
                                     {"type": "thinking", "thinking": "continue", "signature": "s1"},
                                     {"type": "tool_use", "id": "tu_1", "name": "triage", "input": {}}])
    t = d.step()
    assert t.calls == [("tu_1", "triage", {})] and t.thinking == "continue"
    assert [b.type for b in d.messages[1]["content"]] == ["text", "fallback", "thinking", "tool_use"]
    assert t.raw["fallback"] and t.raw["dropped_before_fallback"] == 2


def test_ollama_driver_round_trip():
    seen = []
    replies = [{"model": "qwen3.5:4b", "done_reason": "stop", "prompt_eval_count": 900, "eval_count": 40,
                "message": {"role": "assistant", "content": "", "thinking": "look",
                            "tool_calls": [{"function": {"name": "triage", "arguments": {}}}]}},
               {"model": "qwen3.5:4b", "done_reason": "stop", "prompt_eval_count": 50, "eval_count": 20,
                "message": {"role": "assistant", "content": "all good"}}]

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            seen.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            body = json.dumps(replies[len(seen) - 1]).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        d = agent.OllamaDriver(url="http://127.0.0.1:%d" % srv.server_port)
        d.start("SYS", TOOLS[:3], "go")
        t = d.step()
        assert t.calls[0][1:] == ("triage", {}) and t.thinking == "look" and t.stop == "tool_use"
        d.add_results([(t.calls[0][0], "triage", '{"findings": []}', False)])
        t2 = d.step()
        assert t2.stop == "end_turn" and t2.text == "all good"
        assert seen[0]["think"] is True and seen[0]["options"]["num_ctx"] == agent.NUM_CTX == 65536
        assert seen[0]["tools"][0]["function"]["name"] == TOOLS[0]["name"]
        assert seen[1]["messages"][-1] == {"role": "tool", "tool_name": "triage", "content": '{"findings": []}'}
    finally:
        srv.shutdown()


def test_ollama_driver_streams_into_the_live_view():
    """With on_delta set the request streams: thinking and text reach the callback piece by piece, a
    malformed tool call mid-stream is resampled, and the assembled Turn matches a non-streamed one."""
    seen, deltas = [], []
    chunks = [{"message": {"role": "assistant", "content": "", "thinking": "the echo "}, "done": False},
              {"message": {"role": "assistant", "content": "", "thinking": "is long"}, "done": False},
              {"message": {"role": "assistant", "content": "checking"}, "done": False},
              {"message": {"role": "assistant", "content": "",
                           "tool_calls": [{"function": {"name": "triage", "arguments": {}}}]}, "done": False},
              {"message": {"role": "assistant", "content": ""}, "done": True, "done_reason": "stop",
               "prompt_eval_count": 900, "eval_count": 40, "total_duration": 2_500_000_000, "model": "qwen3.5:4b"}]

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            seen.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.end_headers()
            if len(seen) == 1:              # first attempt: a malformed tool call, reported mid-stream
                lines = [chunks[0], {"error": "error parsing tool call: unexpected end of JSON input"}]
            else:
                lines = chunks
            for c in lines:
                self.wfile.write((json.dumps(c) + "\n").encode())
                self.wfile.flush()

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        d = agent.OllamaDriver(url="http://127.0.0.1:%d" % srv.server_port)
        d.on_delta = lambda kind, text: deltas.append((kind, text))
        d.start("SYS", TOOLS[:3], "go")
        t = d.step()
        assert all(r["stream"] is True for r in seen) and len(seen) == 2 and d.malformed == 1
        assert ("reset", "ollama error: error parsing tool call: unexpected end of JSON input") in deltas
        assert t.thinking == "the echo is long" and t.text == "checking" and t.calls[0][1:] == ("triage", {})
        assert t.stop == "tool_use" and t.usage == {"input_tokens": 900, "output_tokens": 40} and t.raw["seconds"] == 2.5
        assert [k for k, _ in deltas[-3:]] == ["thinking", "thinking", "text"]
        assert d.messages[-1]["thinking"] == "the echo is long" and d.messages[-1]["tool_calls"]
    finally:
        srv.shutdown()


def test_budget_notice_is_sent_once_on_the_last_result(tmp_path):
    b = FakeBench({"system_info": {"text": "BRIEFING"}})
    d = ScriptedDriver([turn(("triage", {}), ("status", {})) for _ in range(8)])
    s = agent.run_agent(b, d, tmp_path, kickoff="go", max_turns=8)
    notes = [(i, r) for i, res in enumerate(d.results) for r in res if "[harness:" in r[2]]
    assert len(notes) == 1 and notes[0][0] == 4 and notes[0][1][1] == "status"   # after turn 5 of 8: 3 left
    assert "3 model turns left" in notes[0][1][2] and s["stop"] == "max_turns"
    rows = [json.loads(l) for l in open(tmp_path / "transcript.jsonl")]
    assert [r["turn"] for r in rows if r["type"] == "harness_note"] == [4]


def test_live_view_renders_a_run(tmp_path):
    import io
    import console
    buf = io.StringIO()
    b = FakeBench({"system_info": {"text": "BRIEFING"},
                   "triage": {"findings": [{"kind": "acc_gravity", "severity": "warn", "summary": "reads 538 mg"}]}})
    d = ScriptedDriver([agent.Turn(text="", thinking="half of 1 g: a scale bug", calls=[("c0", "triage", {}),
                                                                                       ("c1", "fw_flash", {"image": "/x.elf"})],
                                   stop="tool_use", usage={}), turn(text="done: FS mismatch")])
    agent.run_agent(b, d, tmp_path, kickoff="go", ui=console.Console(out=buf, color=False))
    out = buf.getvalue()
    for want in ("half of 1 g: a scale bug", "▶ triage", "warn acc_gravity: reads 538 mg",
                 "REFUSED by the harness: image must be an image crc", "done: FS mismatch", "stop: end_turn"):
        assert want in out, want


def test_model_check_explains_https_to_a_plain_http_server():
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps({"models": [{"name": "qwen3.5:4b"}]}).encode()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        url = "127.0.0.1:%d" % srv.server_port
        assert agent.check_ollama("http://" + url, "qwen3.5:4b") is None
        assert "doesn't serve gemma" in agent.check_ollama("http://" + url, "gemma")
        why = agent.check_ollama("https://" + url, "qwen3.5:4b")
        assert "plain HTTP" in why and ("use http://" + url) in why
    finally:
        srv.shutdown()


def test_ollama_driver_stops_before_overflowing_context():
    d = agent.OllamaDriver(num_ctx=1000)
    d.start("S" * 10_000, TOOLS[:1], "go")
    with pytest.raises(agent.ContextFull):
        d.step()


def score_bench(running, deployments, findings=None, notes=(), mag=1070.0):
    since = 1000.0
    images = {"images": [{"crc": "GOOD", "status": "known-good", "origin": "release", "created": 1},
                         {"crc": "REL", "status": "candidate", "origin": "release", "created": 2},
                         {"crc": "FIX", "status": "candidate", "origin": "workspace", "created": 1100}]}
    b = FakeBench({"triage": {"findings": findings or []}, "chip_status": {"crc": running, "build_id": "pod x"},
                   "fw_images": images, "src_diff": {"diff": "-#define ACC_MG_PER_DIGIT 1\n+#define ACC_MG_PER_DIGIT 2"},
                   "telemetry_stats": {"accel": {"magnitude_mg": {"mean": mag}}}},
                  files={"healer/var/deployments.jsonl": "\n".join(json.dumps(d) for d in deployments),
                         "healer/var/notifications.jsonl": "\n".join(json.dumps(n) for n in notes)})
    return b, since


def summary(**kw):
    return {"label": "05-test", "driver": "scripted", "model": "none", "tools_offered": 38, "forbidden": 0,
            "invalid": 0, "denied": [], "tool_calls": 9, "by_risk": {}, "tool_errors": 0, "turns": 5,
            "usage": {}, "stop": "end_turn", "seconds": 60.0} | kw


def write_transcript(tmp_path, text):
    rec = {"type": "model_turn", "text": text}
    (tmp_path / "transcript.jsonl").write_text(json.dumps(rec) + "\n")


def test_score_forward_fix_passes(tmp_path, monkeypatch):
    monkeypatch.setattr(evaluate.time, "sleep", lambda s: None)
    deps = [{"ts": 1001, "crc": "REL", "result": "unverified", "reason": "operator deploy v1.5"},
            {"ts": 1200, "crc": "FIX", "result": "ok", "reason": "set ACC_MG_PER_DIGIT 2"}]
    b, since = score_bench("FIX", deps)
    write_transcript(tmp_path, "Root cause: FS is ±4 g but ACC_MG_PER_DIGIT stayed 1 (should be 2 mg/digit).")
    sc = evaluate.score_run(b, "05", evaluate.SCENARIOS["05"], tmp_path, summary(), since, 0.0, 10.0)
    assert sc["passed"] and sc["fix"]["kind"] == "forward" and sc["flashes"]["unnecessary"] == 0
    assert sc["time_to_verified_fix_s"] == 190.0 and sc["fix"]["checks"]["gravity_ok"]


def test_score_rejects_unflashed_or_wrong_fixes(tmp_path, monkeypatch):
    monkeypatch.setattr(evaluate.time, "sleep", lambda s: None)
    write_transcript(tmp_path, "The accelerometer reads half: FS ±4 g with 1 mg/digit sensitivity.")
    deps = [{"ts": 1001, "crc": "REL", "result": "unverified", "reason": "operator deploy v1.5"}]
    b, since = score_bench("REL", deps, findings=[{"kind": "acc_gravity", "severity": "warn"}], mag=530.0)
    sc = evaluate.score_run(b, "05", evaluate.SCENARIOS["05"], tmp_path, summary(), since, 0.0, 10.0)
    assert sc["root_cause_named"] and not sc["passed"] and sc["fix"]["kind"] == "none"
    deps.append({"ts": 1200, "crc": "GOOD", "result": "ok", "reason": "rollback"})     # rollback: not ok for 05
    b, since = score_bench("GOOD", deps)
    sc = evaluate.score_run(b, "05", evaluate.SCENARIOS["05"], tmp_path, summary(), since, 0.0, 10.0)
    assert sc["fix"]["kind"] == "rollback" and not sc["fix"]["durable_fix_verified_by_fw_flash"] and not sc["passed"]
    deps[-1] = {"ts": 1200, "crc": "FIX", "result": "failed", "reason": "try"}
    deps.append({"ts": 1230, "crc": "GOOD", "result": "ok", "reason": "auto-rollback after FIX failed verification"})
    b, since = score_bench("GOOD", deps)
    sc = evaluate.score_run(b, "05", evaluate.SCENARIOS["05"], tmp_path, summary(), since, 0.0, 10.0)
    assert sc["fix"]["kind"] == "auto-rollback" and sc["flashes"] == {
        "agent_flashes": 1, "failed": 1, "auto_rollbacks": 1, "stabilizing_rollback": False, "unnecessary": 1}


def test_flash_budget_refunds_calls_that_never_reached_the_chip(tmp_path):
    """The Nov 8 02:52 watch run: fw_flash image='v1.0' failed on the name (KeyError) and still spent a flash."""
    b = bench_with_system()
    b.results["fw_flash"] = lambda a: ({"ok": False, "error": "KeyError: \"no unique image matches 'v9' (0 hits)\""}
                                       if a["image"] == "v9" else
                                       {"ok": True, "result": "ok", "checks": [], "image": {"crc": a["image"]}})
    d = ScriptedDriver([turn(("fw_flash", {"image": "v9"}), ("fw_flash", {"image": "v9"}), ("fw_flash", {"image": "AAAA1111"}),
                             ("fw_flash", {"image": "BBBB2222"})), turn(text="done")])
    s = agent.run_agent(b, d, tmp_path, kickoff="go", flash_cap=2)
    assert [c[1]["image"] for c in b.calls if c[0] == "fw_flash"] == ["v9", "v9", "AAAA1111", "BBBB2222"]
    assert s["flashes_allowed"] == 2 and s["forbidden"] == 0
    assert "[harness: nothing was written to the chip" in d.results[0][0][2]
    assert json.loads(d.results[0][0][2].split("\n[harness:")[0])["ok"] is False      # the tool's own result comes first


def test_reflashing_what_is_already_running_is_refused_and_not_counted(tmp_path):
    """After a verified rollback, flashing known-good again (by keyword, crc or version) changes nothing."""
    b = bench_with_system()
    good = {"ok": True, "result": "ok", "checks": [], "image": {"crc": "BAADB704", "version": "v1.0", "status": "known-good"}}
    b.results["fw_rollback"] = good
    b.results["fw_flash"] = good
    tools = [t for t in TOOLS] + list(agent.ARM_HARNESS_TOOLS)
    d = ScriptedDriver([turn(("fw_rollback", {"to": "known-good"})),
                        turn(("fw_flash", {"image": "known-good"}), ("fw_flash", {"image": "baadb704"}),
                             ("fw_flash", {"image": "v1.0"}), ("fw_rollback", {})),
                        turn(text="done")])
    s = agent.run_agent(b, d, tmp_path, kickoff="go", flash_cap=2, arm=agent.ArmConfig())
    assert [c[0] for c in b.calls].count("fw_flash") == 0 and [c[0] for c in b.calls].count("fw_rollback") == 1
    assert s["flashes_allowed"] == 1 and s["redundant"] == 4 and s["forbidden"] == 0
    msg = d.results[1][0][2]
    assert "already running" in msg and "not counted" in msg and "arm_run" in msg
    assert tools                                         # (the arm tools were offered, so the advice names arm_run)


def test_ollama_driver_drops_old_thinking_before_the_context_fills():
    """At TRIM_AT of num_ctx the thinking of all but the last KEEP_THINKING turns goes; the tool results stay."""
    d = agent.OllamaDriver(num_ctx=4000)
    d.start("S", [], "go")
    for i in range(8):
        d.messages.append({"role": "assistant", "content": "", "thinking": "T%d " % i + "x" * 1200})
        d.messages.append({"role": "tool", "tool_name": "triage", "content": "R%d" % i})
    assert d._approx_ctx() > agent.TRIM_AT * d.num_ctx
    seen = []
    d._chat = lambda: seen.append(json.loads(json.dumps(d.messages))) or {
        "message": {"role": "assistant", "content": "ok"}, "done_reason": "stop"}
    t = d.step()
    kept = [m for m in seen[0] if m.get("thinking")]
    assert t.raw["trimmed_thinking_turns"] == 8 - agent.KEEP_THINKING and len(kept) == agent.KEEP_THINKING
    assert kept[0]["thinking"].startswith("T%d" % (8 - agent.KEEP_THINKING))
    assert sum(m["role"] == "tool" for m in seen[0]) == 8                  # every tool result is still there


def test_rules_are_one_set_rollback_first_then_forward_fix():
    """The arm run's rules and tools agree: build tools offered, rollback first, a forward fix allowed after."""
    assert {"src_edit", "fw_build", "src_commit", "fw_flash", "fw_rollback"} <= set(agent.ARM_TOOLS)
    rules = agent.ARM_RULES
    assert rules.index("Stabilize: fw_rollback") < rules.index("Repair:") < rules.index("acceptable end state")
    assert "In this run that is the fix" not in rules and "person will repair the release" not in rules
    briefing = (agent.FIRMWARE / "healer" / "SYSTEM.md").read_text()
    assert "No robot arm is attached" not in briefing
    assert briefing.index("stabilize first") < briefing.index("Then repair forward")


def test_stabilize_first_is_enforced_when_the_run_starts_on_a_bad_release(tmp_path):
    good = {"ok": True, "result": "ok", "checks": [], "image": {"crc": "GOOD1234", "version": "v1.0", "status": "known-good"}}
    b = bench_with_system()
    b.results |= {"chip_status": {"crc": "REL56789"}, "fw_rollback": good,
                  "fw_images": {"images": [{"crc": "GOOD1234", "version": "v1.0", "status": "known-good"},
                                           {"crc": "REL56789", "version": "v1.8", "status": "candidate"}]}}
    d = ScriptedDriver([turn(("src_edit", {"path": "src/x.c", "old": "a", "new": "b"}), ("fw_build", {}),
                             ("fw_flash", {"image": "REL56789"}), ("src_read", {"path": "src/x.c"})),
                        turn(("fw_rollback", {})), turn(("src_edit", {"path": "src/x.c", "old": "a", "new": "b"})),
                        turn(text="done")])
    s = agent.run_agent(b, d, tmp_path, kickoff="go")
    assert s["out_of_order"] == 3 and not s["started_stable"] and s["flashes_allowed"] == 1
    assert all("stabilize first" in r[2] for r in d.results[0][:3]) and not d.results[0][3][3]   # reads still run
    assert [c[0] for c in b.calls if c[0] in ("src_edit", "fw_build", "fw_flash", "fw_rollback")] == ["fw_rollback", "src_edit"]


def test_nothing_is_enforced_when_the_run_starts_on_known_good_or_unknown(tmp_path):
    b = bench_with_system()                             # chip_status gives no crc: unknown, so not enforced
    d = ScriptedDriver([turn(("src_edit", {"path": "src/x.c", "old": "a", "new": "b"}), ("fw_build", {})), turn(text="ok")])
    s = agent.run_agent(b, d, tmp_path, kickoff="go")
    assert s["out_of_order"] == 0 and s["started_stable"]
