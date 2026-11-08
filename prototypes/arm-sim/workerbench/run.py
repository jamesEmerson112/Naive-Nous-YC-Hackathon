"""Run the worker-model benchmark: a model behind any OpenAI-compatible endpoint, or a baseline.

From prototypes/arm-sim (standard library only; no .venv needed):

    python3 -m workerbench.run --baseline oracle --label oracle            # must score 100%
    python3 -m workerbench.run --baseline l1 --label l1                    # level 1 (watchdog + podd)
    python3 -m workerbench.run --quick --track E --profile lite \
        --endpoint http://127.0.0.1:11434/v1 --model qwen3.5:4b --json-mode schema --label qwen4b-nothink
    python3 -m workerbench.run --quick --endpoint https://<pod-id>-11434.proxy.runpod.net/v1 --model lfm3:1.7b

Items: data/workerbench/*-<split>.jsonl (gen_arm.py, gen_pod.py, gen_real.py). Each item is rendered
(render.py), sent with the frozen system prompt (prompt.py), parsed strictly and scored (score.py).
Baselines: oracle (the gold answer), healthy (always healthy), alarm (always a warn fault), l1 (both
rule sets), l1-arm (armsim/level1.py only: the fault factory's watchdog), l1-pod (detect.py only).

`call()` is a urllib POST to {endpoint}/chat/completions with a plain User-Agent (Runpod's Cloudflare
blocks Python's default one with error 1010, docs/healer-data-levels.md:60). It reads thinking from
message.reasoning or message.reasoning_content, and usage from `usage`, falling back to Ollama's native
prompt_eval_count/eval_count (firmware/harness/agent.py:626). Only `content` is scored: JSON that
appears only in the thinking text, or a reply cut off for length, is invalid. A failed call (timeout,
HTTP error, refused) is recorded and scored as an invalid answer, and the run goes on.

Writes <out>/answers.jsonl (one line per item and repeat; --resume skips lines already there),
<out>/report.json and <out>/report.md. Default out: runs/wb/<label>/.
"""
import argparse
import collections
import concurrent.futures as cf
import glob
import hashlib
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import kinds, l1, prompt, render, score

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
DATA = REPO / "data" / "workerbench"
USER_AGENT = "workerbench/0.1"
BASELINES = ("oracle", "healthy", "alarm", "l1", "l1-arm", "l1-pod")


# ------------------------------------------------------------------------------------------ items

def load_items(patterns, split=None):
    items = []
    for pat in patterns:
        paths = sorted(glob.glob(pat)) or sorted(glob.glob(str(REPO / pat)))
        for p in paths:
            with open(p) as f:
                for line in f:
                    if line.strip():
                        it = json.loads(line)
                        if split is None or it.get("split") == split:
                            items.append(it)
    return items


def _h(s):
    return hashlib.sha1(s.encode()).hexdigest()


def quick_subset(items, per_group=8, healthy=60):
    """Deterministic screening subset: 8 observable fault items per (source family, planted variant), at
    least half of them missed by level 1 where there are enough, plus 60 healthy items spread round-robin
    over the hard-negative groups (about 240 items)."""
    groups, hgroups = collections.defaultdict(list), collections.defaultdict(list)
    for it in sorted(items, key=lambda i: _h(i["id"])):
        lab = it["label"]
        if lab["status"] == "healthy":
            hgroups[(it["source"], lab.get("hard_negative") or "clean")].append(it)
        elif lab.get("observable", True):
            fam = "arm" if it["source"] == "sim-factory" else "pod"
            groups[(fam, (it.get("meta") or {}).get("variant") or lab["kind"])].append(it)
    out = []
    for key in sorted(groups):
        g = groups[key]
        missed = [i for i in g if not (i.get("meta") or {}).get("l1")]     # level 1 silent: the gap items
        caught = [i for i in g if (i.get("meta") or {}).get("l1")]
        take = missed[:per_group // 2]
        take += caught[:per_group - len(take)]
        take += [i for i in missed if i not in take][:per_group - len(take)]
        out += take
    keys, picked = sorted(hgroups), []
    while len(picked) < healthy and any(hgroups[k] for k in keys):
        for k in keys:
            if hgroups[k] and len(picked) < healthy:
                picked.append(hgroups[k].pop(0))
    return out + picked


# ------------------------------------------------------------------------------------------ baselines

def oracle_answer(item, idmap):
    lab = item["label"]
    if lab["status"] == "healthy":
        return {"status": "healthy", "kind": "none", "severity": "info", "evidence": [], "escalate": False,
                "message": ""}
    kind = lab["kind"]
    facts = lab.get("facts") or {}
    nums = [(k, v) for k, v in facts.items() if isinstance(v, (int, float)) and not isinstance(v, bool)
            and k != "joint_idx"]
    if kind.startswith("joint_") and facts.get("joint"):
        part = facts["joint"]
    else:
        part = kinds.PART_WORDS[kinds.PART_OF[kind]][0]
    symptom = (kinds.LEXICON.get(kind) or ("fault",))[0]
    num = f" with {nums[0][0].replace('_', ' ')} {nums[0][1]}" if nums else ""
    msg = f"The {part} shows a {symptom} fault{num} in this window, so the manager should take the {lab['route']} route."
    return {"status": "fault", "kind": kind, "severity": lab["severity"],
            "evidence": score.compress_ids(sorted(score.gold_ids(item, idmap))), "escalate": lab["escalate"],
            "message": msg}


def baseline_answer(name, item, idmap):
    if name == "oracle":
        return oracle_answer(item, idmap)
    if name == "healthy":
        return {"status": "healthy", "kind": "none", "severity": "info", "evidence": [], "escalate": False,
                "message": ""}
    if name == "alarm":
        return {"status": "fault", "kind": "other", "severity": "warn", "evidence": [], "escalate": True,
                "message": "Something in this window looks wrong, so the manager should take a look at it now."}
    which = {"l1": "all", "l1-arm": "arm", "l1-pod": "pod"}[name]
    return l1.l1_answer(item["snapshot"], accept=item["label"].get("accept") or (), which=which)


# ------------------------------------------------------------------------------------------ the model call

def call(endpoint, model, messages, *, max_tokens=512, temperature=None, json_mode="none", extra_body=None,
         stream=False, timeout=300.0, api_key=None):
    """One chat completion. Returns {content, reasoning, finish_reason, prompt_tokens, completion_tokens,
    latency_s, ttft_s, error}. Never raises for HTTP or network failures."""
    url = endpoint.rstrip("/") + "/chat/completions"
    body = {"model": model, "messages": messages, "max_tokens": max_tokens}
    if temperature is not None:
        body["temperature"] = temperature
    if json_mode == "object":
        body["response_format"] = {"type": "json_object"}
    elif json_mode == "schema":
        body["response_format"] = {"type": "json_schema",
                                   "json_schema": {"name": "worker_answer", "strict": True,
                                                   "schema": prompt.ANSWER_SCHEMA}}
    if stream:
        body["stream"] = True
        body["stream_options"] = {"include_usage": True}
    body.update(extra_body or {})
    headers = {"Content-Type": "application/json", "Accept": "application/json", "User-Agent": USER_AGENT}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers, method="POST")
    out = {"content": "", "reasoning": "", "finish_reason": None, "prompt_tokens": None, "completion_tokens": None,
           "latency_s": None, "ttft_s": None, "error": None}
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if stream:
                _read_stream(resp, out, t0, timeout)
            else:
                j = json.loads(resp.read().decode("utf-8", "replace"))
                ch = (j.get("choices") or [{}])[0]
                msg = ch.get("message") or {}
                out["content"] = msg.get("content") or ""
                out["reasoning"] = msg.get("reasoning") or msg.get("reasoning_content") or ""
                out["finish_reason"] = ch.get("finish_reason")
                _usage(out, j)
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode("utf-8", "replace")[:200]
        except Exception:
            detail = ""
        out["error"] = f"HTTP {e.code}: {detail}"
        out["http_status"] = e.code
    except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError, OSError) as e:
        reason = getattr(e, "reason", e)
        out["error"] = f"{type(reason).__name__}: {reason}"
    except ValueError as e:
        out["error"] = f"bad response body: {e}"
    out["latency_s"] = round(time.monotonic() - t0, 3)
    return out


def _usage(out, j):
    u = j.get("usage") or {}
    out["prompt_tokens"] = u.get("prompt_tokens", j.get("prompt_eval_count"))
    out["completion_tokens"] = u.get("completion_tokens", j.get("eval_count"))


def _read_stream(resp, out, t0, timeout):
    """Server-sent events: `data: {chunk}` lines, ending with `data: [DONE]`."""
    content, reasoning = [], []
    for raw in resp:
        if time.monotonic() - t0 > timeout:
            raise TimeoutError(f"stream ran past {timeout} s")
        line = raw.decode("utf-8", "replace").strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            break
        j = json.loads(data)
        if j.get("usage"):
            _usage(out, j)
        for ch in j.get("choices") or []:
            d = ch.get("delta") or {}
            piece = d.get("content")
            think = d.get("reasoning") or d.get("reasoning_content")
            if (piece or think) and out["ttft_s"] is None:
                out["ttft_s"] = round(time.monotonic() - t0, 3)
            if piece:
                content.append(piece)
            if think:
                reasoning.append(think)
            if ch.get("finish_reason"):
                out["finish_reason"] = ch["finish_reason"]
    out["content"], out["reasoning"] = "".join(content), "".join(reasoning)


def call_with_retries(retries, *a, **kw):
    for attempt in range(retries + 1):
        r = call(*a, **kw)
        transient = r["error"] and (r.get("http_status") in (429, 502, 503, 504) or "Connection" in r["error"])
        if not transient or attempt == retries:
            return r
        time.sleep(2.0 * (attempt + 1))
    return r


# ------------------------------------------------------------------------------------------ one item

def run_item(item, a, rep_i):
    text, idmap = render.render(item, profile=a.profile, track=a.track)
    l1ans = l1.l1_answer(item["snapshot"], accept=item["label"].get("accept") or ())
    rec = {"id": item["id"], "repeat": rep_i}
    if a.baseline:
        content = json.dumps(baseline_answer(a.baseline, item, idmap))
        res = {"content": content, "latency_s": None, "error": None, "finish_reason": "stop"}
    else:
        res = call_with_retries(a.retries, a.endpoint, a.model, prompt.messages(text), max_tokens=a.max_tokens,
                                temperature=a.temperature, json_mode=a.json_mode, extra_body=a.extra_body,
                                stream=a.stream, timeout=a.timeout, api_key=a.api_key)
    parsed = score.parse_answer(res["content"], idmap)
    if res.get("finish_reason") == "length":
        parsed["schema_ok"] = parsed["strict"] = parsed["lenient"] = False
        parsed["errors"].insert(0, "cut off for length")
    if res.get("error"):
        parsed["errors"].insert(0, "call failed: " + res["error"])
    row = score.score_item(item, parsed, idmap, text=text, l1=l1ans)
    row.update(latency_s=res.get("latency_s"), ttft_s=res.get("ttft_s"), prompt_tokens=res.get("prompt_tokens"),
               completion_tokens=res.get("completion_tokens"), call_error=res.get("error"),
               role=(item.get("meta") or {}).get("role"), repeat=rep_i, prompt_chars=len(text))
    if a.baseline in ("l1", "l1-arm") and row["role"] == "incident" and item.get("kinematics"):
        det = (item.get("meta") or {}).get("detected") or []
        first = [f["kind"] for f in l1.alarms(l1.l1_findings(item["snapshot"], which="arm"))][:1]
        row["l1_replay_ok"] = bool(det) and first == det[:1]
    rec.update(content=res["content"], reasoning=(res.get("reasoning") or "")[:4000],
               finish_reason=res.get("finish_reason"), error=res.get("error"), row=row)
    return rec


# ------------------------------------------------------------------------------------------ report

def stability(rows):
    by = collections.defaultdict(list)
    for r in rows:
        by[r["id"]].append(r)
    multi = [v for v in by.values() if len(v) > 1]
    if not multi:
        return None
    alarm_flip = sum(1 for v in multi if len({r["alarm"] for r in v}) > 1)
    kind_flip = sum(1 for v in multi if len({r["kind"] for r in v}) > 1)
    return {"items": len(multi), "alarm_flips": alarm_flip, "kind_flips": kind_flip}


def to_markdown(rep, meta):
    L = [f"# workerbench: {meta['label']}", "",
         f"- {meta['what']}; split `{meta['split']}`{' (--quick)' if meta['quick'] else ''}; profile `{meta['profile']}`, "
         f"track `{meta['track']}`; {rep['n']} answers; prompt `{prompt.PROMPT_VERSION}`; {meta['finished']}", ""]
    L += ["| metric | value |", "|---|---|"]
    fa = rep["false_alarms"]["all_healthy"]
    for k, v in [("strict / lenient / schema_ok", f"{rep['strict']} / {rep['lenient']} / {rep['schema_ok']}"),
                 ("recall, macro over kinds (observable)", rep["recall_macro"]),
                 ("gap recall, macro (level 1 silent)", rep["gap_recall_macro"]),
                 ("gap recall, micro (n)", f"{rep['gap_recall_micro']} ({rep['gap_n']})"),
                 ("level 1 recall / combined recall, micro", f"{rep['l1_recall_micro']} / {rep['combined_recall_micro']}"),
                 ("false alarms on healthy (k/n, 95% upper)", f"{fa['alarms']}/{fa['n']}, {fa['upper95']}"),
                 ("kind score (1 exact, 0.5 route)", rep["kind_score"]),
                 ("kind exact / route right, given an alarm", f"{rep['kind_exact_given_alarm']} / {rep['route_given_alarm']}"),
                 ("kind corrections (level 1 wrong -> exact)",
                  f"{rep['kind_corrections']['corrected']}/{rep['kind_corrections']['l1_wrong_kind']}"),
                 ("severity / escalate accuracy", f"{rep['severity_acc']} / {rep['escalate_acc']}"),
                 ("evidence hit / precision", f"{rep['evidence_hit']} / {rep['evidence_precision']}"),
                 ("message score (0-5)", rep["message_score"]),
                 ("latency p50 / p95 / max (s)", f"{rep['latency_s']['p50']} / {rep['latency_s']['p95']} / {rep['latency_s']['max']}"),
                 ("prompt / completion tokens (mean)", f"{rep['prompt_tokens']} / {rep['completion_tokens']}"),
                 ("failed calls", rep["errors"]),
                 ("latent items alarmed (not scored)", f"{rep['latent']['alarmed']}/{rep['latent']['n']}")]:
        L.append(f"| {k} | {v} |")
    if rep.get("l1_replay"):
        L.append(f"| level-1 replay on incident windows (kinematics on) | {rep['l1_replay']['ok']}/{rep['l1_replay']['n']} |")
    if rep.get("stability"):
        s = rep["stability"]
        L.append(f"| stability over repeats (alarm / kind flips) | {s['alarm_flips']} / {s['kind_flips']} of {s['items']} |")
    L += ["", "| kind | n | recall | level 1 | gap n | gap recall | combined | kind score |", "|---|---|---|---|---|---|---|---|"]
    for k, v in rep["per_kind"].items():
        L.append(f"| {k} | {v['n']} | {v['recall']} | {v['l1_recall']} | {v['gap_n']} | {v['gap_recall']} | "
                 f"{v['combined_recall']} | {v['kind_score']} |")
    L += ["", "| healthy group | n | alarms | rate | 95% upper | level-1 alarms |", "|---|---|---|---|---|---|"]
    for g, v in rep["false_alarms"].items():
        L.append(f"| {g} | {v['n']} | {v['alarms']} | {v['rate']} | {v['upper95']} | {v['l1_alarms']} |")
    L += ["", "Gates: " + ", ".join(f"{k} {'PASS' if ok else 'FAIL'}" for k, ok in rep["gates"].items()), ""]
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Run the worker-model benchmark against a model or a baseline.")
    ap.add_argument("--items", nargs="+", default=None, help="globs; default data/workerbench/*-<split>.jsonl")
    ap.add_argument("--split", default="test")
    ap.add_argument("--quick", action="store_true", help="the ~250-item screening subset")
    ap.add_argument("--limit", type=int, default=None, help="first N items only (smoke tests)")
    ap.add_argument("--endpoint", help="OpenAI-compatible base URL, e.g. http://127.0.0.1:11434/v1")
    ap.add_argument("--model")
    ap.add_argument("--api-key", default=os.environ.get("WB_API_KEY"), help="optional bearer token (env WB_API_KEY)")
    ap.add_argument("--baseline", choices=BASELINES)
    ap.add_argument("--profile", choices=("full", "lite"), default="full")
    ap.add_argument("--track", choices=("D", "E"), default="E")
    ap.add_argument("--shots", type=int, default=0, help="reserved: few-shot examples from dev (not built yet)")
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--json-mode", choices=("none", "object", "schema"), default="none")
    ap.add_argument("--max-tokens", type=int, default=512)
    ap.add_argument("--temperature", type=float, default=None, help="unset = the model's default")
    ap.add_argument("--extra-body", type=json.loads, default=None,
                    help='JSON merged into the request, e.g. \'{"reasoning_effort": "none"}\'')
    ap.add_argument("--stream", action="store_true")
    ap.add_argument("--timeout", type=float, default=300.0, help="seconds per call")
    ap.add_argument("--retries", type=int, default=1, help="retries on 429/5xx/connection errors")
    ap.add_argument("--concurrency", type=int, default=1, help="parallel calls (default 1, one at a time)")
    ap.add_argument("--resume", action="store_true", help="skip (item, repeat) pairs already in answers.jsonl")
    ap.add_argument("--label", default=None)
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    if bool(a.baseline) == bool(a.endpoint and a.model):
        ap.error("give either --baseline or both --endpoint and --model")
    if a.shots:
        ap.error("--shots is not built yet; run with --shots 0")
    label = a.label or (a.baseline or f"{a.model}".replace("/", "_").replace(":", "-"))
    out = Path(a.out) if a.out else HERE.parent / "runs" / "wb" / label
    out.mkdir(parents=True, exist_ok=True)
    items = load_items(a.items or [str(DATA / f"*-{a.split}.jsonl")], a.split)
    if not items:
        print(f"no items for split {a.split!r}; generate them first (see workerbench/RESULTS.md)", file=sys.stderr)
        return 2
    if a.quick:
        items = quick_subset(items)
    if a.limit:
        items = items[:a.limit]
    ans_path = out / "answers.jsonl"
    done, rows = set(), []
    if a.resume and ans_path.exists():
        for line in ans_path.open():
            r = json.loads(line)
            done.add((r["id"], r["repeat"]))
            rows.append(r["row"])
    jobs = [(it, k) for k in range(a.repeat) for it in items if (it["id"], k) not in done]
    what = f"baseline `{a.baseline}`" if a.baseline else f"model `{a.model}` at `{a.endpoint}` (json-mode {a.json_mode})"
    print(f"{label}: {len(items)} items x {a.repeat} repeat(s), {len(jobs)} to run; {what} -> {out}", flush=True)
    t0 = time.time()
    with ans_path.open("a" if a.resume else "w") as f:
        def handle(rec, i):
            f.write(json.dumps(rec, default=list) + "\n")
            f.flush()
            rows.append(rec["row"])
            if not a.baseline and (i % 10 == 0 or i == len(jobs)):
                r = rec["row"]
                print(f"  {i}/{len(jobs)} {r['id']} alarm={r['alarm']} ok={r['schema_ok']} "
                      f"{r['latency_s']}s {('ERR ' + r['call_error']) if r['call_error'] else ''}", flush=True)
        if a.concurrency <= 1:
            for i, (it, k) in enumerate(jobs, 1):
                handle(run_item(it, a, k), i)
        else:
            with cf.ThreadPoolExecutor(a.concurrency) as pool:
                futs = [pool.submit(run_item, it, a, k) for it, k in jobs]
                for i, fu in enumerate(cf.as_completed(futs), 1):
                    handle(fu.result(), i)
    rep = score.summarize(rows)
    replay = [r for r in rows if "l1_replay_ok" in r]
    if replay:
        rep["l1_replay"] = {"n": len(replay), "ok": sum(r["l1_replay_ok"] for r in replay)}
    rep["stability"] = stability(rows)
    meta = {"label": label, "what": what, "split": a.split, "quick": a.quick, "profile": a.profile, "track": a.track,
            "items": len(items), "repeat": a.repeat, "seconds": round(time.time() - t0, 1),
            "finished": time.strftime("%Y-%m-%d %H:%M:%S"), "args": {k: v for k, v in vars(a).items() if k != "api_key"},
            "prompt_version": prompt.PROMPT_VERSION}
    (out / "report.json").write_text(json.dumps({"meta": meta, "report": rep}, indent=1, default=list) + "\n")
    md = to_markdown(rep, meta)
    (out / "report.md").write_text(md + "\n")
    print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
