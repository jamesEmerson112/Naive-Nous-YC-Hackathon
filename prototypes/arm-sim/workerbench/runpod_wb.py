"""Worker-model benchmark on one Runpod GPU: an Ollama pod there, workerbench/run.py here.

A fork of level2/runpod_l2.py (Runpod's REST API, no SSH, no runpodctl). The pod runs the official
`ollama/ollama` image as it ships (entrypoint `ollama`, CMD `serve`), so nothing is installed on the pod
and nothing is uploaded: models are pulled through the pod's Ollama HTTP API, and run.py calls
https://<pod-id>-11434.proxy.runpod.net/v1 from here.

From prototypes/arm-sim (standard library only; in a Claude cloud session the network proxy injects the
saved Runpod key, so pass RUNPOD_API_KEY=proxy-injected):

    python3 -m workerbench.runpod_wb verify-tags              # every tag in MODELS is on ollama.com/library
    python3 -m workerbench.runpod_wb plan                     # launch, pull, smoke, benchmark, terminate
    python3 -m workerbench.runpod_wb full <tag>...            # only the full test split, all tags at once
    python3 -m workerbench.runpod_wb launch | wait | pull <tag>... | smoke <tag> | status | terminate
    python3 -m workerbench.runpod_wb pods                     # GET /pods: should be [] at the end
    python3 -m workerbench.runpod_wb report-partial <label>   # report.json/.md from a cut-off answers.jsonl

Caps, so a hang can't drain the account:
- `launch` starts a detached local watchdog that DELETEs the pod at launch + HARD_CAP_S (2 h 50 min);
- `plan` and `full` end in try/finally (SIGTERM included): run.py children are stopped and the pod is
  terminated even when a step raises;
- each benchmark run gets a wall-clock cap from the time left; a run that hits it is cut and scored on
  what it answered (the item files are shuffled, so any prefix is a random sample).
The image has no runpodctl, so the pod cannot remove itself: the watchdog here is the backstop.
The pod's Ollama endpoint is unauthenticated: keep its life short.

The plan (docs/worker-manager-healer.md §3): every model on the quick subset with thinking off at
concurrency 1 (the latency numbers); then, at the same time, thinking on for up to 3 models (lfm2.5:24b
always, it can't think less) and the full test split for the top 2 with thinking off. Those two phases
run at concurrency 4 and share the GPU, so their latency is not single-stream. Ranking: `balanced` (gap
macro recall minus the healthy false-alarm rate), then kind score, message score, fewer output tokens;
the design ranks by gap recall first (§4), see balanced(). Killing a phase's run.py by hand moves the
plan on to the next run. State and logs: runs/wb/runpod/ (gitignored); each run: runs/wb/<label>/.
Env overrides, for a dry run against fake servers: RUNPOD_API_BASE, WB_POD_URL (with {id}), WB_RUNS,
WB_HARD_CAP_S.
"""
import json
import os
import random
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import prompt, render, run, score

API = os.environ.get("RUNPOD_API_BASE", "https://rest.runpod.io/v1")
POD_URL = os.environ.get("WB_POD_URL", "https://{id}-11434.proxy.runpod.net")
HERE = Path(__file__).resolve().parent
ARMSIM = HERE.parent
RUNS = Path(os.environ.get("WB_RUNS", ARMSIM / "runs" / "wb"))      # each run: RUNS/<label>/
OUT = RUNS / "runpod"
STATE = OUT / "pod.json"
LOG = OUT / "plan.log"
UA = "workerbench-runpod/0.1"          # Runpod's Cloudflare blocks Python's default agent (error 1010)
GPUS = ["NVIDIA RTX A6000", "NVIDIA A40", "NVIDIA L40S", "NVIDIA RTX 6000 Ada Generation"]   # 48 GB
IMAGE = "ollama/ollama:latest"
HARD_CAP_S = int(os.environ.get("WB_HARD_CAP_S", 2 * 3600 + 50 * 60))   # watchdog: launch + 2 h 50 min (< 3 h)
MARGIN_S = 10 * 60                     # the plan stops starting work this long before the cap
# Ollama tags checked on ollama.com/library/<name>/tags (Sun Nov 8): baseline first.
MODELS = ["qwen3.5:4b", "lfm3:1.7b", "qwen3.5:2b", "qwen3.5:9b", "lfm2.5:24b"]
# lfm2.5:24b thinks whatever we send: on Ollama 0.40.15 it returned ~1.5k chars of reasoning and hit the
# 512-token cap with reasoning_effort none, reasoning.effort none, "think": false, and native /api/chat
# think:false (tested on the pod, Sun Nov 8 ~5:35 AM PST). So it only runs in the thinking phase.
ALWAYS_THINKS = {"lfm2.5:24b"}
THINK_OFF = {"reasoning_effort": "none"}       # Ollama: "none" requests think=false (docs.ollama.com)
THINK_ON = {"reasoning_effort": "medium"}      # boolean-only models map a known effort to think=true


def log(msg):
    line = f"{time.strftime('%H:%M:%S')} {msg}"
    print(line, flush=True)
    OUT.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as f:
        f.write(line + "\n")


# ------------------------------------------------------------------------------------------ Runpod REST

def api(method, path, body=None, timeout=60):
    key = os.environ.get("RUNPOD_API_KEY")
    if not key:
        raise SystemExit("set RUNPOD_API_KEY (in a Claude cloud session: RUNPOD_API_KEY=proxy-injected)")
    req = urllib.request.Request(API + path, method=method, data=json.dumps(body).encode() if body else None,
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                                          "User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            text = r.read().decode()
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Runpod {method} {path}: HTTP {exc.code}: {exc.read().decode(errors='replace')[:600]}")
    return json.loads(text) if text.strip() else {}


def saved():
    return json.loads(STATE.read_text()) if STATE.exists() else {}


def save(**kw):
    OUT.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps({**saved(), **kw}, indent=1) + "\n")


def pod_id():
    pid = saved().get("id")
    if not pid:
        raise SystemExit(f"no live pod recorded in {STATE}")
    return pid


def launch():
    if saved().get("id"):
        raise SystemExit(f"a pod is already recorded: {saved()['id']}; terminate it first")
    body = {"name": "workerbench-ollama", "imageName": IMAGE, "computeType": "GPU", "cloudType": "COMMUNITY",
            "gpuTypeIds": GPUS, "gpuCount": 1, "containerDiskInGb": 80, "volumeInGb": 0,
            "ports": ["11434/http"], "interruptible": False,
            "env": {"OLLAMA_HOST": "0.0.0.0:11434", "OLLAMA_CONTEXT_LENGTH": "16384",
                    "OLLAMA_NUM_PARALLEL": "4", "OLLAMA_KEEP_ALIVE": "30m"}}
    pod = api("POST", "/pods", body)
    t = time.time()
    save(id=pod.get("id"), launched=t, cap_at=t + HARD_CAP_S, gpu=pod.get("gpu"), machine=pod.get("machine"),
         costPerHr=pod.get("costPerHr") or pod.get("adjustedCostPerHr"), terminated=None)
    log("launched " + json.dumps({k: pod.get(k) for k in ("id", "desiredStatus", "costPerHr", "adjustedCostPerHr",
                                                         "gpu", "machineId", "imageName")}))
    w = subprocess.Popen([sys.executable, "-m", "workerbench.runpod_wb", "watchdog", pod["id"], str(t + HARD_CAP_S)],
                         cwd=ARMSIM, env=os.environ.copy(), stdout=open(OUT / "watchdog.log", "a"),
                         stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
    save(watchdog_pid=w.pid)
    log(f"watchdog pid {w.pid} deletes {pod['id']} at {time.strftime('%H:%M:%S', time.localtime(t + HARD_CAP_S))}")
    return pod["id"]


def watchdog(pid, at):
    """Detached: sleep until `at`, then DELETE the pod, unless it was terminated first."""
    at = float(at)
    while time.time() < at:
        try:
            gone = saved().get("id") != pid
        except ValueError:                      # the state file caught mid-write: look again later
            gone = False
        if gone:
            print(f"{time.strftime('%H:%M:%S')} {pid} already terminated; watchdog exits", flush=True)
            return
        time.sleep(20)
    for attempt in range(5):
        try:
            api("DELETE", f"/pods/{pid}")
            print(f"{time.strftime('%H:%M:%S')} watchdog DELETED {pid} at the hard cap", flush=True)
            s = saved()
            hours = (time.time() - s.get("launched", time.time())) / 3600
            save(id=None, last_id=pid, terminated=time.time(), terminated_by="watchdog", hours=round(hours, 3),
                 cost_usd=round(hours * float(s.get("costPerHr") or 0), 3))
            return
        except Exception as exc:
            print(f"{time.strftime('%H:%M:%S')} watchdog DELETE failed ({exc}); retrying", flush=True)
            time.sleep(15)


def terminate():
    s = saved()
    pid = s.get("id")
    if pid:
        api("DELETE", f"/pods/{pid}")
        t = time.time()
        hours = (t - s["launched"]) / 3600
        rate = float(s.get("costPerHr") or 0)
        save(id=None, last_id=pid, terminated=t, terminated_by="terminate", hours=round(hours, 3),
             cost_usd=round(hours * rate, 3))
        log(f"terminated {pid} after {hours:.2f} h at ${rate}/h = about ${hours * rate:.2f}")
    else:
        log("terminate: no live pod recorded")
    wpid = saved().get("watchdog_pid")
    if wpid and wpid != os.getpid():
        try:
            os.kill(wpid, signal.SIGTERM)
            log(f"stopped watchdog pid {wpid}")
        except OSError:
            pass
    pods()


def pods():
    lst = api("GET", "/pods")
    log("GET /pods -> " + json.dumps([{k: p.get(k) for k in ("id", "name", "desiredStatus", "costPerHr")}
                                      for p in lst] if isinstance(lst, list) else lst))
    return lst


def status():
    s = saved()
    print(json.dumps(s, indent=1))
    if s.get("id"):
        pod = api("GET", f"/pods/{s['id']}")
        up = (time.time() - s["launched"]) / 3600
        rate = float(s.get("costPerHr") or 0)
        print(f"pod {s['id']}: {pod.get('desiredStatus')}, ${rate}/h, up {up:.2f} h, about ${rate * up:.2f}; "
              f"watchdog at {time.strftime('%H:%M:%S', time.localtime(s['cap_at']))}")
        try:
            print("ollama", ollama("GET", "/api/version", timeout=15), ollama("GET", "/api/ps", timeout=15))
        except Exception as exc:
            print(f"ollama not reachable yet ({exc})")


# ------------------------------------------------------------------------------------------ the pod's Ollama

def base():
    return POD_URL.format(id=pod_id())


def ollama(method, path, body=None, timeout=60):
    req = urllib.request.Request(base() + path, method=method, data=json.dumps(body).encode() if body else None,
                                 headers={"Content-Type": "application/json", "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode() or "{}")


def wait(limit_s=900):
    t0 = time.time()
    while time.time() - t0 < limit_s:
        try:
            v = ollama("GET", "/api/version", timeout=15)
            save(ollama_version=v.get("version"))
            log(f"ollama up after {time.time() - t0:.0f} s: {v}")
            return v
        except Exception as exc:
            last = exc
            time.sleep(10)
    raise RuntimeError(f"ollama did not answer within {limit_s} s ({last})")


def have(tag):
    return any(m.get("name") == tag or m.get("model") == tag for m in ollama("GET", "/api/tags").get("models", []))


def pull(tag, limit_s=1800):
    """Streamed /api/pull, re-issued after any disconnect (the proxy cuts long requests; Ollama keeps the
    blobs it has). Done only when /api/tags lists the tag."""
    t0, last_pct = time.time(), -1
    while time.time() - t0 < limit_s:
        try:
            if have(tag):
                log(f"pulled {tag} in {time.time() - t0:.0f} s")
                return True
            req = urllib.request.Request(base() + "/api/pull", method="POST",
                                         data=json.dumps({"model": tag, "stream": True}).encode(),
                                         headers={"Content-Type": "application/json", "User-Agent": UA})
            with urllib.request.urlopen(req, timeout=120) as r:
                for raw in r:
                    j = json.loads(raw.decode() or "{}")
                    if j.get("error"):
                        raise RuntimeError(f"pull {tag}: {j['error']}")
                    if j.get("total") and j.get("completed"):
                        pct = int(100 * j["completed"] / j["total"]) // 20 * 20
                        if pct != last_pct and j["total"] > 50e6:
                            log(f"pull {tag}: {j.get('status')} {pct}% of {j['total'] / 1e9:.1f} GB")
                            last_pct = pct
        except RuntimeError:
            raise
        except Exception as exc:
            log(f"pull {tag}: {type(exc).__name__}: {exc}; re-issuing")
            time.sleep(5)
    raise RuntimeError(f"pull {tag} did not finish within {limit_s} s")


def smoke(tag, extra=THINK_OFF, max_tokens=512, stream=False):
    """/api/show (capabilities, thinking values), one real item (it also loads the model), then /api/ps:
    size_vram 0 would mean Ollama fell back to CPU."""
    show = ollama("POST", "/api/show", {"model": tag})
    caps = show.get("capabilities")
    info = {"capabilities": caps, "thinking": show.get("thinking")}
    item = shuffled("quick")[0]
    text, idmap = render.render(item, profile="lite", track="E")
    r = run.call(base() + "/v1", tag, prompt.messages(text), max_tokens=max_tokens, json_mode="schema",
                 extra_body=extra, stream=stream, timeout=600)
    parsed = score.parse_answer(r["content"], idmap)
    ps = [{"name": m.get("name"), "size": m.get("size"), "size_vram": m.get("size_vram"),
           "context_length": m.get("context_length")} for m in ollama("GET", "/api/ps").get("models", [])]
    info.update(latency_s=r["latency_s"], finish=r["finish_reason"], error=r["error"],
                prompt_tokens=r["prompt_tokens"], completion_tokens=r["completion_tokens"],
                schema_ok=parsed["schema_ok"], reasoning_chars=len(r["reasoning"] or ""),
                content=(r["content"] or "")[:300], ps=ps)
    log(f"smoke {tag} {json.dumps(extra)}: " + json.dumps(info))
    return info


# ------------------------------------------------------------------------------------------ items and runs

def shuffled(which):
    """The quick subset or the full test split, in a fixed shuffled order, so a run cut at its time cap
    is still a random sample. Same items as run.py --quick / the full test split."""
    path = OUT / f"{which}-test-shuffled.jsonl"
    if not path.exists():
        items = run.load_items([str(run.DATA / "*-test.jsonl")], "test")
        if which == "quick":
            items = run.quick_subset(items)
        random.Random(7).shuffle(items)
        OUT.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text("".join(json.dumps(i) + "\n" for i in items))
        tmp.replace(path)                       # atomic: a parallel reader never sees half a file
    return [json.loads(line) for line in path.open()]


def label(tag, think, which):
    return f"gpu-{tag.replace(':', '-')}-{'think' if think else 'nothink'}-{which}"


CHILDREN = set()                       # run.py processes, killed when the plan stops


def bench(tag, think, which, *, concurrency, cap_s):
    """run.py in a subprocess under a wall-clock cap. Returns the report dict (partial if cut)."""
    lab = label(tag, think, which)
    n = len(shuffled(which))
    cmd = [sys.executable, "-m", "workerbench.run", "--items", str(OUT / f"{which}-test-shuffled.jsonl"),
           "--split", "test", "--track", "E", "--profile", "lite", "--json-mode", "schema",
           "--endpoint", base() + "/v1", "--model", tag, "--label", lab, "--out", str(RUNS / lab), "--resume",
           "--extra-body", json.dumps(THINK_ON if think else THINK_OFF),
           "--max-tokens", "4096" if think else "512", "--timeout", "300" if think else "120",
           "--concurrency", str(concurrency)] + (["--stream"] if think else [])   # long replies: keep bytes flowing
    log(f"run {lab}: {n} items, concurrency {concurrency}, cap {cap_s / 60:.0f} min")
    t0 = time.time()
    with open(OUT / f"{lab}.log", "a") as f:
        p = subprocess.Popen(cmd, cwd=ARMSIM, stdout=f, stderr=subprocess.STDOUT, start_new_session=True)
        CHILDREN.add(p)
        try:
            p.wait(timeout=cap_s)
        except subprocess.TimeoutExpired:
            os.killpg(p.pid, signal.SIGTERM)
            p.wait(30)
            log(f"run {lab}: cut at its {cap_s / 60:.0f} min cap")
        except BaseException:                   # the plan is stopping: don't leave run.py calling the pod
            os.killpg(p.pid, signal.SIGTERM)
            raise
    ans = RUNS / lab / "answers.jsonl"
    if not ans.exists() or not ans.read_text().strip():      # a run stopped before its first answer
        log(f"run {lab}: no answers (see {OUT / (lab + '.log')})")
        return None
    rep = report_partial(lab, meta_extra={"concurrency": concurrency, "wall_s": round(time.time() - t0, 1),
                                          "items_planned": n, "think": think, "which": which, "model": tag})
    log(f"run {lab}: {rep['n']}/{n} answered in {time.time() - t0:.0f} s; balanced {balanced(rep)}, gap macro {rep['gap_recall_macro']}, "
        f"kind {rep['kind_score']}, msg {rep['message_score']}, schema_ok {rep['schema_ok']}, "
        f"FA {rep['false_alarms']['all_healthy']['alarms']}/{rep['false_alarms']['all_healthy']['n']}, "
        f"p50/p95 {rep['latency_s']['p50']}/{rep['latency_s']['p95']} s, out tok {rep['completion_tokens']}")
    write_table()
    return rep


def drop_failed(lab):
    """Remove answers whose call failed (timeout, HTTP error, pod gone), so --resume asks again."""
    ans = RUNS / lab / "answers.jsonl"
    if not ans.exists():
        return 0
    lines = [line for line in ans.read_text().splitlines() if line.strip()]
    keep = [line for line in lines if not json.loads(line).get("error")]
    ans.write_text("".join(line + "\n" for line in keep))
    if len(keep) < len(lines):
        log(f"{lab}: dropped {len(lines) - len(keep)} failed calls; they will be asked again")
    return len(lines) - len(keep)


def report_partial(lab, meta_extra=None):
    """Score whatever answers.jsonl holds (run.py writes report.json only when it finishes)."""
    d = RUNS / lab
    rows = [json.loads(line)["row"] for line in (d / "answers.jsonl").open()] if (d / "answers.jsonl").exists() else []
    if not rows:
        raise RuntimeError(f"{lab}: no answers")
    rep = score.summarize(rows)
    rep["stability"] = run.stability(rows)
    old = json.loads((d / "report.json").read_text())["meta"] if (d / "report.json").exists() else {}
    meta = {**old, "label": lab, "what": old.get("what", f"label {lab}"), "split": "test", "quick": False,
            "profile": "lite", "track": "E", "finished": time.strftime("%Y-%m-%d %H:%M:%S"),
            **(meta_extra or {}), "answered": len(rows)}
    meta["partial"] = len(rows) < meta.get("items_planned", len(rows))
    (d / "report.json").write_text(json.dumps({"meta": meta, "report": rep}, indent=1, default=list) + "\n")
    (d / "report.md").write_text(run.to_markdown(rep, meta) + "\n")
    return rep


def balanced(rep):
    """Gap macro recall minus the false-alarm rate on healthy items (inferred). The design ranks by gap
    recall first, among models that pass the gates; on --quick no model can pass the false-alarm gate
    (60 healthy items), and gap recall alone ranks the always-alarm baseline first (1.0). This puts
    always-alarm and always-healthy both at 0."""
    return round((rep["gap_recall_macro"] or 0) - (rep["false_alarms"]["all_healthy"]["rate"] or 0), 4)


def rank_key(rep):
    return (balanced(rep), rep["kind_score"] or 0, rep["message_score"] or 0, -(rep["completion_tokens"] or 0))


def write_table():
    """runs/wb/runpod/table.md: one row per finished GPU run, ranked within each (split, mode)."""
    rows = []
    for p in sorted(RUNS.glob("gpu-*/report.json")):
        j = json.loads(p.read_text())
        rows.append((j["meta"], j["report"]))
    rows.sort(key=lambda mr: (mr[0].get("which", ""), bool(mr[0].get("think")), tuple(-x for x in rank_key(mr[1]))))
    L = ["| run | n | conc | schema_ok | gap recall macro (n) | healthy FA (95% upper) | balanced | kind | message | "
         "p50 / p95 s | prompt / out tok |", "|---|---|---|---|---|---|---|---|---|---|---|"]
    for m, r in rows:
        fa = r["false_alarms"]["all_healthy"]
        L.append(f"| {m['label']}{' (partial)' if m.get('partial') else ''} | {r['n']} | {m.get('concurrency')} | "
                 f"{r['schema_ok']} | {r['gap_recall_macro']} ({r['gap_n']}) | {fa['alarms']}/{fa['n']} "
                 f"({fa['upper95']}) | {balanced(r)} | {r['kind_score']} | {r['message_score']} | {r['latency_s']['p50']} / "
                 f"{r['latency_s']['p95']} | {r['prompt_tokens']} / {r['completion_tokens']} |")
    (OUT / "table.md").write_text("\n".join(L) + "\n")


# ------------------------------------------------------------------------------------------ the plan

def verify_tags(tags=MODELS):
    for tag in tags:
        name = tag.split(":")[0]
        req = urllib.request.Request(f"https://ollama.com/library/{name}/tags", headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            page = r.read().decode(errors="replace")
        if f'/library/{tag}"' not in page:
            raise RuntimeError(f"{tag} is not listed on ollama.com/library/{name}/tags")
        log(f"tag ok: {tag}")


def left():
    return saved()["cap_at"] - MARGIN_S - time.time()


def plan(models=MODELS, keep=False):
    """Launch (or reuse the recorded pod), then the three phases under the time cap; always terminate."""
    verify_tags(models)
    signal.signal(signal.SIGTERM, lambda *a: sys.exit(143))     # so a kill still runs the finally below
    try:
        if not saved().get("id"):
            launch()
        wait()
        pulled = {}

        def puller():
            for tag in models:
                try:
                    pulled[tag] = pull(tag)
                except Exception as exc:
                    pulled[tag] = False
                    log(f"pull {tag} FAILED: {exc}")
        th = threading.Thread(target=puller, daemon=True)
        th.start()
        quick, info = {}, {}
        for tag in models:                                  # phase 1: quick, thinking off, concurrency 1
            if tag in ALWAYS_THINKS:
                continue
            while tag not in pulled and left() > 0:
                time.sleep(5)
            if not pulled.get(tag):
                log(f"skip {tag}: not pulled")
                continue
            done = RUNS / label(tag, False, "quick") / "report.json"
            if done.exists() and not json.loads(done.read_text())["meta"].get("partial", True):
                info[tag] = {"capabilities": ollama("POST", "/api/show", {"model": tag}).get("capabilities")}
                quick[tag] = json.loads(done.read_text())["report"]
                log(f"{tag}: quick run already complete (plan restarted); reusing it")
                continue
            info[tag] = smoke(tag)
            if not any((m.get("size_vram") or 0) > 0 for m in info[tag]["ps"]):
                raise RuntimeError(f"{tag} is not on the GPU (size_vram 0): numbers would be meaningless")
            if left() < 5 * 60:
                log("phase 1 out of time")
                break
            rep = bench(tag, False, "quick", concurrency=1, cap_s=max(60, min(left(), 40 * 60)))
            if rep:
                quick[tag] = rep
        top = sorted(quick, key=lambda t: rank_key(quick[t]), reverse=True)
        log(f"phase 1 ranking: {top}")
        thinkers = [t for t in models if t in ALWAYS_THINKS and pulled.get(t)]
        thinkers += [t for t in top if "thinking" in (info[t].get("capabilities") or [])][:3 - len(thinkers)]
        full = top[:2]
        per_item = {t: quick[t]["latency_s"]["p50"] or 2.0 for t in full}
        reserve = sum(per_item[t] * len(shuffled("full")) / 2.5 * 1.3 for t in full)   # concurrency 4 ~ 2.5x (inferred)
        log(f"thinking on for {thinkers}; full test for {full} at the same time (the GPU has room for both, "
            f"inferred); projected full tests {reserve / 1.3 / 60:.0f} min; {left() / 60:.0f} min left")

        def phase3():                                       # full test, thinking off, concurrency 4
            for i, tag in enumerate(full):
                cap = left() / (len(full) - i)
                if cap < 5 * 60:
                    log(f"skip the full test for {tag}: {cap / 60:.0f} min left")
                    continue
                try:
                    bench(tag, False, "full", concurrency=4, cap_s=cap)
                except Exception as exc:
                    log(f"full test {tag} FAILED: {exc}")
        th3 = threading.Thread(target=phase3, daemon=True)
        th3.start()
        for i, tag in enumerate(thinkers):                  # phase 2: quick, thinking on, concurrency 4
            cap = min(25 * 60, left() / (len(thinkers) - i))
            if cap < 3 * 60:
                log(f"skip thinking on for {tag}: {cap / 60:.0f} min left for it")
                continue
            smoke(tag, THINK_ON, max_tokens=4096, stream=True)
            bench(tag, True, "quick", concurrency=4, cap_s=cap)
        th3.join(max(0, left() + MARGIN_S / 2))
        log("plan done")
    except BaseException as exc:
        log(f"plan stopped: {type(exc).__name__}: {exc}")
        raise
    finally:
        for p in CHILDREN:
            if p.poll() is None:
                os.killpg(p.pid, signal.SIGTERM)
        if not keep:
            terminate()


def full(models):
    """Only the full-test phase: launch (or reuse the recorded pod), pull, warm each model, then the full
    test split for every model at once (concurrency 4 each), under the time cap; always terminate."""
    signal.signal(signal.SIGTERM, lambda *a: sys.exit(143))
    try:
        if not saved().get("id"):
            launch()
        wait()
        for tag in models:
            pull(tag)
            info = smoke(tag)
            if not any((m.get("size_vram") or 0) > 0 for m in info["ps"]):
                raise RuntimeError(f"{tag} is not on the GPU (size_vram 0)")
            drop_failed(label(tag, False, "full"))
        def one(tag, cap):
            try:
                bench(tag, False, "full", concurrency=4, cap_s=cap)
            except Exception as exc:
                log(f"full test {tag} FAILED: {exc}")
        shuffled("full")                        # write the item file once, before the threads read it
        threads = [threading.Thread(target=one, args=(tag, left()), daemon=True) for tag in models]
        for t in threads:
            t.start()
        for t in threads:
            t.join(max(0, left() + MARGIN_S / 2))
        log("full phase done")
    except BaseException as exc:
        log(f"full phase stopped: {type(exc).__name__}: {exc}")
        raise
    finally:
        for p in CHILDREN:
            if p.poll() is None:
                os.killpg(p.pid, signal.SIGTERM)
        terminate()


if __name__ == "__main__":
    cmd, args = (sys.argv[1] if len(sys.argv) > 1 else "status"), sys.argv[2:]
    if cmd == "plan":
        plan(models=[a for a in args if not a.startswith("--")] or MODELS, keep="--keep" in args)
    elif cmd == "full":
        full(args)
    elif cmd == "pull":
        for t in args:
            pull(t)
    elif cmd == "smoke":
        think = "--think" in args
        smoke(args[0], THINK_ON if think else THINK_OFF, max_tokens=4096 if think else 512, stream=think)
    elif cmd == "watchdog":
        watchdog(args[0], args[1])
    elif cmd == "report-partial":
        print(run.to_markdown(report_partial(args[0]), json.loads((RUNS / args[0] / "report.json").read_text())["meta"]))
    elif cmd == "verify-tags":
        verify_tags(args or MODELS)
    else:
        {"launch": launch, "wait": wait, "status": status, "terminate": terminate, "pods": pods,
         "table": write_table}[cmd]()
