"""Sanity checks for the worker-model benchmark (like Ishaan's bench: the reference passes, doing nothing
fails), plus run.py's model path end to end against a fake OpenAI-compatible server. Standard library
only; no MuJoCo, no network. Run from prototypes/arm-sim:

    python3 workerbench/tests/test_workerbench.py          (or: python3 -m pytest workerbench/tests/)

Pod and real-sample items are built in-process (gen_pod, gen_real). Arm items are added only when
data/workerbench/sim-factory-test.jsonl exists (it is generated, and data/ is gitignored).
"""
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from workerbench import gen_pod, gen_real, kinds, l1, prompt, render, run, score  # noqa: E402
from workerbench.tests.fake_openai import FAULT, FakeOpenAI  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="workerbench-test-"))
ARM_FILE = run.DATA / "sim-factory-test.jsonl"


def _items():
    pod, bad = gen_pod.make_items(7, "test", 3)
    assert not bad, bad
    items = pod + gen_real.windows_from_sample()
    if ARM_FILE.exists():
        with ARM_FILE.open() as f:
            for k, line in enumerate(f):
                if k % 4 == 0:
                    items.append(json.loads(line))
    return items


ITEMS = _items()
ITEMS_FILE = TMP / "items-test.jsonl"
ITEMS_FILE.write_text("".join(json.dumps(i) + "\n" for i in ITEMS))


def bench(*args):
    out = TMP / f"run-{time.time_ns()}"
    rc = run.main(["--items", str(ITEMS_FILE), "--out", str(out), *args])
    assert rc == 0, rc
    rep = json.loads((out / "report.json").read_text())["report"]
    answers = [json.loads(line) for line in (out / "answers.jsonl").open()]
    return rep, answers


def test_the_oracle_scores_100_percent():
    rep, _ = bench("--baseline", "oracle")
    assert rep["schema_ok"] == 1.0 and rep["strict"] == 1.0, rep
    assert rep["recall_macro"] == 1.0 and rep["gap_recall_macro"] == 1.0, rep
    assert rep["false_alarms"]["all_healthy"]["alarms"] == 0, rep["false_alarms"]
    assert rep["kind_score"] == 1.0 and rep["kind_exact_given_alarm"] == 1.0, rep
    assert rep["severity_acc"] == 1.0 and rep["escalate_acc"] == 1.0, rep
    assert rep["evidence_hit"] == 1.0 and rep["evidence_precision"] == 1.0, rep
    assert rep["message_score"] == 5.0, rep["message_parts"]


def test_always_healthy_has_no_recall_and_no_false_alarms():
    rep, _ = bench("--baseline", "healthy")
    assert rep["recall_macro"] == 0.0 and rep["gap_recall_macro"] == 0.0, rep
    assert rep["false_alarms"]["all_healthy"]["alarms"] == 0, rep


def test_always_alarm_has_100_percent_false_alarms():
    rep, _ = bench("--baseline", "alarm")
    fa = rep["false_alarms"]["all_healthy"]
    assert fa["alarms"] == fa["n"] and fa["rate"] == 1.0, fa
    assert rep["gates"]["real_alarms==0"] is False, rep["gates"]


def test_l1_answer_matches_each_variants_expectation():
    for it in ITEMS:
        if it["source"] != "synth-pod":
            continue
        exp = it["meta"]["expect_l1"]
        ans = l1.l1_answer(it["snapshot"], accept=it["label"]["accept"])
        if exp["alarm"] is True:
            assert ans["status"] == "fault", (it["id"], ans)
        elif exp["alarm"] is False:
            assert ans["status"] == "healthy", (it["id"], ans)
        ok, got = gen_pod.check_expect(it["snapshot"], {"alarm": exp["alarm"], "names": set(exp["names"])})
        assert ok, (it["id"], got)


def test_the_l1_baseline_replays_the_watchdog_on_incident_windows():
    rep, _ = bench("--baseline", "l1-arm")
    if ARM_FILE.exists():
        assert rep["l1_replay"]["n"] > 0 and rep["l1_replay"]["ok"] == rep["l1_replay"]["n"], rep.get("l1_replay")


def test_line_ids_are_unique_and_every_gold_row_renders():
    for it in ITEMS:
        for profile in ("full", "lite"):
            for track in ("D", "E"):
                text, idmap = render.render(it, profile=profile, track=track)
                ids = [line.split(" ", 1)[0] for line in text.split("\n")[1:]]
                assert len(ids) == len(set(ids)), (it["id"], profile)
                assert set(ids) == set(idmap), (it["id"], set(ids) ^ set(idmap))
                covered = {}
                for ref in idmap.values():
                    covered.setdefault(ref["src"], set()).update(ref["ts"])
                for src, tss in (it["label"].get("evidence_rows") or {}).items():
                    if src == "L":
                        assert "L" in idmap, it["id"]
                        continue
                    missing = set(tss) - covered.get(src, set())
                    assert not missing, (it["id"], profile, src, sorted(missing)[:3])
                lab = it["label"]
                if lab["status"] == "fault" and lab["observable"]:
                    assert score.gold_ids(it, idmap), (it["id"], profile)
                    assert any(isinstance(v, (int, float)) and k != "joint_idx" for k, v in lab["facts"].items()), it["id"]


def _validate(obj, schema):
    """Just enough JSON Schema for ANSWER_SCHEMA: type, enum, required, additionalProperties, maxItems."""
    t = schema.get("type")
    types = {"object": dict, "string": str, "array": list, "boolean": bool}
    assert isinstance(obj, types[t]), (obj, t)
    if "enum" in schema:
        assert obj in schema["enum"], obj
    if t == "object":
        assert set(schema["required"]) <= set(obj), obj
        if schema.get("additionalProperties") is False:
            assert set(obj) <= set(schema["properties"]), obj
        for k, v in obj.items():
            _validate(v, schema["properties"][k])
    if t == "array":
        assert len(obj) <= schema.get("maxItems", 1e9)
        for v in obj:
            _validate(v, schema["items"])
    if t == "string" and "maxLength" in schema:
        assert len(obj) <= schema["maxLength"], obj


def test_the_answer_schema_accepts_the_oracles_answers():
    for it in ITEMS:
        _, idmap = render.render(it)
        _validate(run.oracle_answer(it, idmap), prompt.ANSWER_SCHEMA)


def test_parse_answer_strict_lenient_and_schema():
    idmap = {f"D{i:02d}": {"src": "D", "ts": [i]} for i in range(1, 31)} | {"L": {"src": "L", "ts": [0]}}
    good = json.dumps({"status": "fault", "kind": "us_frozen", "severity": "critical", "evidence": ["D17-D30", "L"],
                       "escalate": True, "message": "x"})
    p = score.parse_answer(good, idmap)
    assert p["strict"] and p["schema_ok"] and len(p["cited"]) == 15, p
    p = score.parse_answer("```json\n" + good + "\n```", idmap)
    assert not p["strict"] and p["lenient"] and p["schema_ok"], p
    p = score.parse_answer(good.replace("D17-D30", "D17-D40"), idmap)
    assert not p["schema_ok"] and "not in the window" in p["errors"][0], p
    p = score.parse_answer(good.replace('"us_frozen"', '"none"'), idmap)
    assert not p["schema_ok"], p
    p = score.parse_answer('{"status": "healthy"}', idmap)
    assert p["strict"] and not p["schema_ok"], p
    assert score.expand_ids(["H02", "D09-D11"]) == {"H02", "D09", "D10", "D11"}
    assert score.compress_ids(["D01", "D02", "D03", "D07", "L"]) == ["L", "D01-D03", "D07"]
    assert kinds.kind_score("us_offset", {"joint_offset"}, "joint_offset") == 0.5
    assert kinds.kind_score("us_scale", {"joint_offset"}, "joint_offset") == 0.0


def test_upper95_is_about_3_over_n_at_zero():
    assert abs(score.upper95(0, 300) - (1 - 0.05 ** (1 / 300))) < 1e-6
    assert 0.0099 < score.upper95(0, 300) < 0.0100
    assert score.upper95(5, 100) > 0.05 and score.upper95(100, 100) == 1.0


def test_quick_subset_is_deterministic():
    a = [i["id"] for i in run.quick_subset(ITEMS)]
    b = [i["id"] for i in run.quick_subset(list(reversed(ITEMS)))]
    assert a == b and len(a) > 0


# ---------------------------------------------------------------- the model path, against a fake server

SRV = FakeOpenAI(slow_s=2.0).start()


def model(name, *extra, limit="4"):
    SRV.requests.clear()
    return bench("--endpoint", SRV.url, "--model", name, "--limit", limit, *extra)


def test_model_path_good_answers_and_headers():
    rep, ans = model("fault", "--json-mode", "schema")
    path, headers, body = SRV.requests[0]
    assert path == "/v1/chat/completions", path
    assert headers.get("User-Agent") == run.USER_AGENT, headers
    assert body["response_format"]["type"] == "json_schema" and body["max_tokens"] == 512, body
    assert body["messages"][0]["role"] == "system" and body["messages"][0]["content"] == prompt.SYSTEM
    assert all(a["row"]["prompt_tokens"] == 1234 and a["row"]["latency_s"] is not None for a in ans), ans[0]["row"]
    assert rep["errors"] == 0 and rep["n"] == 4, rep


def test_model_path_invalid_json_counts_as_no_alarm():
    rep, ans = model("badjson")
    assert rep["strict"] == 0 and rep["lenient"] == 0 and rep["schema_ok"] == 0, rep
    assert not any(a["row"]["alarm"] for a in ans)


def test_model_path_fenced_and_prose_are_lenient_only():
    for mode in ("fenced", "prose"):
        rep, _ = model(mode)
        assert rep["strict"] == 0 and rep["lenient"] == 1.0, (mode, rep["strict"], rep["lenient"])


def test_model_path_json_only_in_reasoning_is_invalid():
    rep, ans = model("reasoning-only")
    assert rep["schema_ok"] == 0 and "Thinking" in ans[0]["reasoning"], rep


def test_model_path_cut_off_for_length_is_invalid():
    rep, ans = model("length")
    assert rep["schema_ok"] == 0 and ans[0]["row"]["errors"][0] == "cut off for length", ans[0]["row"]


def test_model_path_slow_answers_time_out_and_the_run_goes_on():
    t0 = time.time()
    rep, ans = model("slow", "--timeout", "0.5", "--retries", "0", limit="3")
    assert time.time() - t0 < 2.0 * 3, time.time() - t0
    assert rep["errors"] == 3 and all("timed out" in a["error"] for a in ans), [a["error"] for a in ans]
    assert not any(a["row"]["alarm"] for a in ans)
    rep, ans = model("slow", "--timeout", "10", limit="1")
    assert rep["errors"] == 0 and ans[0]["row"]["latency_s"] >= 2.0, ans[0]["row"]


def test_model_path_http_errors_are_recorded_and_429_is_retried():
    rep, ans = model("http500", "--retries", "0", limit="2")
    assert rep["errors"] == 2 and ans[0]["error"].startswith("HTTP 500"), ans[0]["error"]
    rep, ans = model("http429", "--retries", "1", limit="1")
    assert len(SRV.requests) == 2 and ans[0]["error"].startswith("HTTP 429"), (len(SRV.requests), ans[0]["error"])


def test_model_path_cut_transfer_is_recorded_and_the_run_goes_on():
    rep, ans = model("cut", "--retries", "0", limit="3")
    assert rep["errors"] == 3 and all("IncompleteRead" in a["error"] for a in ans), [a["error"] for a in ans]
    assert rep["schema_ok"] == 0


def test_model_path_refused_connection_is_recorded():
    out = TMP / "refused"
    rc = run.main(["--items", str(ITEMS_FILE), "--out", str(out), "--endpoint", "http://127.0.0.1:9/v1",
                   "--model", "x", "--limit", "2", "--retries", "0", "--timeout", "2"])
    rep = json.loads((out / "report.json").read_text())["report"]
    assert rc == 0 and rep["errors"] == 2 and rep["schema_ok"] == 0, rep


def test_model_path_native_usage_fallback_and_streaming():
    _, ans = model("native-usage", limit="1")
    assert ans[0]["row"]["prompt_tokens"] == 777 and ans[0]["row"]["completion_tokens"] == 33, ans[0]["row"]
    rep, ans = model("fault", "--stream", limit="2")
    body = SRV.requests[0][2]
    assert body["stream"] is True and body["stream_options"] == {"include_usage": True}, body
    assert ans[0]["content"] == json.dumps(FAULT), ans[0]["content"]          # reassembled from the deltas
    assert rep["strict"] == 1.0 and ans[0]["row"]["ttft_s"] is not None and ans[0]["row"]["prompt_tokens"] == 1234


def test_resume_skips_answers_already_written():
    out = TMP / "resume"
    run.main(["--items", str(ITEMS_FILE), "--out", str(out), "--endpoint", SRV.url, "--model", "good", "--limit", "3"])
    SRV.requests.clear()
    run.main(["--items", str(ITEMS_FILE), "--out", str(out), "--endpoint", SRV.url, "--model", "good", "--limit", "3",
              "--resume", "--repeat", "2"])
    assert len(SRV.requests) == 3, len(SRV.requests)
    rep = json.loads((out / "report.json").read_text())["report"]
    assert rep["n"] == 6 and rep["stability"]["items"] == 3, rep["stability"]


if __name__ == "__main__":
    import contextlib
    import io
    failed = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            t0 = time.time()
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    fn()
                print(f"ok    {name}  ({time.time() - t0:.1f} s)")
            except Exception as e:  # report every test, then fail
                failed += 1
                print(f"FAIL  {name}: {type(e).__name__}: {str(e)[:300]}")
    SRV.stop()
    print(f"{'FAILED' if failed else 'all passed'}: {failed} failing; items: {len(ITEMS)} "
          f"({'with' if ARM_FILE.exists() else 'without'} arm items)")
    sys.exit(1 if failed else 0)
