#!/usr/bin/env python3
"""Fault lab runner: each fault on Naïve's pi (in its cloud sandbox) and on Hermes (on this machine),
both on the same model, then graded by the hidden check the healers never see.

Usage: python3 run_lab.py [--faults D1,D2,...] [--sides pi,hermes] [--out DIR]
Needs: `naive login` (pi side) and Hermes signed in with the model (Hermes side).
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from faults import FAULTS, incident_report, make_workspace
from hidden_check import check, read_workspace

PI_AGENT = os.environ.get("PI_AGENT", "agt_9hmwfy6y72b585cvmcfsc832yh")  # hello-sol: pi + openai/gpt-6-sol
PI_MODEL = os.environ.get("PI_MODEL", "openai/gpt-6-sol")  # the agent's model, recorded with the results
HERMES_MODEL = os.environ.get("HERMES_MODEL", "gpt-6-sol")
RESULTS = Path(__file__).resolve().parent / "results"  # kept in the repo; the Naïve console shows them
HERMES = os.environ.get("HERMES_BIN", str(Path.home() / ".local/bin/hermes"))
TASK_TIMEOUT_S = 15 * 60
MAX_STEPS = 30  # same step limit as Ishaan's benchmark
RATE_LIMITED = re.compile(r"rate_limited|at capacity|too many requests|\b429\b", re.I)
RETRY_DELAYS_S = [2, 4, 8, 15, 30]
PRINTABLE = ("app.py", "config.toml", "REPORT.md", "arm_state.json")

TASK = """You are the healer for our robot app: an SO-101 arm that holds an object. An incident just happened. Find the cause. If it is a software fault, fix it with the smallest change. If it is a hardware fault, park the arm instead (python3 park.py) and change no code. Then verify, and report.

The workspace{where} has:
- SPEC.md: what the app must do. Read it first.
- app.py and config.toml: the robot app and its settings.
- hw.py and hardware.json: the fake hardware. Never edit them.
- traces/: recorded distance traces; traces/incident.json is the incident.
- health.py: replays the incident (python3 health.py). Don't edit it.
- park.py: parks the arm (python3 park.py). Hardware faults only.

Incident report from the supervisor:
{incident}

When you're done, python3 health.py must print HEALTHY (or PARKED for a hardware fault). Last, write REPORT.md with exactly two lines:
Cause: <one sentence>
Action: fixed   (or, for a hardware fault: Action: parked)"""

PI_SETUP = """Your Linux sandbox starts without our files, so first recreate the workspace: run this script exactly as given, then work inside the `ws` directory it creates.

```bash
{script}
```

"""

PI_END = """

When you're finished, print the final contents of app.py, config.toml and REPORT.md, and of arm_state.json if it exists, each in exactly this form:
===FILE <name>===
<contents>
===END==="""


def incident_for(ws):
    return incident_report(ws).replace(str(ws) + "/", "")


def setup_script(ws):
    """A bash script that recreates the workspace in a sandbox (quoted heredocs: no expansion)."""
    lines = ["mkdir -p ws/traces && cd ws"]
    for i, path in enumerate(sorted(p for p in Path(ws).rglob("*") if p.is_file() and "__pycache__" not in p.parts)):
        rel = path.relative_to(ws).as_posix()
        text = path.read_text()
        if not text.endswith("\n"):
            text += "\n"
        lines.append(f"cat > '{rel}' <<'__EOF_{i}__'\n{text}__EOF_{i}__")
    lines.append("python3 health.py || true")
    return "\n".join(lines)


def naive_json(args):
    for i in range(len(RETRY_DELAYS_S) + 1):
        p = subprocess.run(["naive", *args], capture_output=True, text=True)
        if p.returncode == 0:
            return json.loads(p.stdout)
        err = (p.stderr or p.stdout).strip()
        if i < len(RETRY_DELAYS_S) and RATE_LIMITED.search(err):
            time.sleep(RETRY_DELAYS_S[i])
            continue
        raise RuntimeError(err)


def run_pi(fault_id, root):
    ws = make_workspace(fault_id, root / fault_id / "pi")
    msg = PI_SETUP.format(script=setup_script(ws)) + TASK.format(where=" (the `ws` directory)", incident=incident_for(ws)) + PI_END
    out = {"side": "pi", "fault": fault_id, "tools": [], "calls": 0, "prompt_tokens": 0, "output_tokens": 0}
    t0 = time.time()
    try:
        sid = naive_json(["session", "create", "--agent", PI_AGENT, "--message", msg, "--budget-usd", "5"])["id"]
        out["session"] = sid
        after, idle, replies, retry = 0, False, [], 0
        deadline = t0 + TASK_TIMEOUT_S
        while not idle and time.time() < deadline:
            p = subprocess.Popen(["naive", "session", "stream", "--session", sid, "--after-seq", str(after)],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            killer = threading.Timer(max(1, deadline - time.time()), p.kill)
            killer.start()
            for line in p.stdout:
                if not line.startswith('{"id":"evt_'):
                    continue
                ev = json.loads(line)
                after = max(after, ev.get("seq", after))
                data = ev.get("data") or {}
                if ev["type"] == "tool.started":
                    out["tools"].append(data.get("name") or data.get("tool") or "tool")
                elif ev["type"] == "span.model_request.end":
                    u = data.get("token_usage") or {}
                    out["calls"] += 1
                    out["prompt_tokens"] += (u.get("input") or 0) + (u.get("cache_read") or 0) + (u.get("cache_write") or 0)
                    out["output_tokens"] += u.get("output") or 0
                elif ev["type"] == "message.completed" and data.get("role") == "assistant":
                    replies.append(data.get("content") or "")
                elif ev["type"] == "session.idle":
                    idle = True
                    out["stop"] = data.get("stop_reason")
            err = p.stderr.read()
            p.wait()
            killer.cancel()
            if not idle and RATE_LIMITED.search(err or "") and retry < len(RETRY_DELAYS_S):
                time.sleep(RETRY_DELAYS_S[retry])
                retry += 1
            elif not idle:
                out["error"] = (err or "stream ended before the turn finished").strip()[-300:]
                break
        if not idle and "error" not in out:
            out["error"] = f"no result after {TASK_TIMEOUT_S // 60} min"
        out["cost_usd"] = naive_json(["session", "get", sid]).get("consumed_micro_usd", 0) / 1e6
        text = "\n".join(replies)
        printed = dict(re.findall(r"===FILE ([\w.]+)===\n(.*?)\n?===END===", text, re.S))
        out["printed"] = sorted(printed)
        for name, body in printed.items():  # what pi left in its sandbox, as it printed it
            if name in PRINTABLE:
                (ws / name).write_text(body if body.endswith("\n") or not body else body + "\n")
        out["final"] = (replies[-1] if replies else "")[:600]
    except Exception as e:  # noqa: BLE001 — record any failure as the result
        out["error"] = str(e)[-300:]
    out["wall_s"] = round(time.time() - t0, 1)
    healed = read_workspace(ws)
    healed["hw.py"] = healed["hardware.json"] = None  # pi's sandbox copy isn't visible to us
    out["passed"], out["reasons"] = check(fault_id, healed)
    return out


def run_hermes(fault_id, root):
    ws = make_workspace(fault_id, root / fault_id / "hermes")
    prompt = TASK.format(where=" (your current directory)", incident=incident_for(ws))
    args = [HERMES, "chat", "-q", prompt, "--oneshot", "--format", "stream-json", "--source", "tool", "--in", str(ws),
            "-t", "terminal,file", "-m", HERMES_MODEL, "--max-turns", str(MAX_STEPS), "--ignore-rules"]
    out = {"side": "hermes", "fault": fault_id, "tools": []}
    t0 = time.time()
    p = subprocess.Popen(args, cwd=ws, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    killer = threading.Timer(TASK_TIMEOUT_S, p.kill)
    killer.start()
    result = {}
    for line in p.stdout:
        if not line.startswith("{"):
            continue
        ev = json.loads(line)
        if ev.get("type") == "tool_use":
            out["tools"].append(ev.get("name"))
        elif ev.get("type") == "result":
            result = ev
    err = p.stderr.read()
    p.wait()
    killer.cancel()
    out["wall_s"] = round(time.time() - t0, 1)
    out["exit_code"] = result.get("exit_code", p.returncode)
    out["model_s"] = round((result.get("duration_ms") or 0) / 1000, 1)
    tok = result.get("tokens") or {}
    out["prompt_tokens"], out["output_tokens"] = tok.get("input"), tok.get("output")
    out["final"] = (result.get("text") or err[-300:] or "")[:600]
    out["passed"], out["reasons"] = check(fault_id, read_workspace(ws))
    return out


def save_results(stamp, rows):
    """Keep a copy of a run's results in results/<stamp>.json, labelled for the console."""
    RESULTS.mkdir(exist_ok=True)
    out = []
    for r in rows:
        f = FAULTS[r["fault"]]
        out.append({**r, "run": stamp, "title": f["title"], "right": f["right"],
                    "model": PI_MODEL if r["side"] == "pi" else HERMES_MODEL})
    (RESULTS / f"{stamp}.json").write_text(json.dumps(out, indent=1) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--faults", default=",".join(FAULTS))
    ap.add_argument("--sides", default="pi,hermes")
    ap.add_argument("--out", default=str(Path(tempfile.gettempdir()) / "fault-lab-runs" / time.strftime("%Y%m%d-%H%M%S")))
    a = ap.parse_args()
    root = Path(a.out)
    root.mkdir(parents=True, exist_ok=True)
    runners = {"pi": run_pi, "hermes": run_hermes}
    results = []
    for fid in a.faults.split(","):
        with ThreadPoolExecutor(max_workers=2) as pool:  # both sides of one fault at a time
            futures = [pool.submit(runners[s], fid, root) for s in a.sides.split(",")]
            for fut in futures:
                r = fut.result()
                results.append(r)
                mark = "PASS" if r["passed"] else "FAIL"
                print(f"{fid} {r['side']:6} {mark} {r['wall_s']:>6}s tools={len(r['tools'])} "
                      f"{'; '.join(r['reasons']) or ''} {('error: ' + r['error']) if r.get('error') else ''}", flush=True)
        (root / "results.json").write_text(json.dumps(results, indent=1))
        save_results(root.name, results)
    print(f"results: {root / 'results.json'} (copy in {RESULTS.name}/{root.name}.json)")


if __name__ == "__main__":
    sys.exit(main())
