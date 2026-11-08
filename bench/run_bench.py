#!/usr/bin/env python3
"""Run a healer on the benchmark tasks and print a results table.

  bench/.venv/bin/python bench/run_bench.py --agent oracle      # validates the tasks: all should pass
  bench/.venv/bin/python bench/run_bench.py --agent none        # baseline: all should fail
  bench/.venv/bin/python bench/run_bench.py --agent mini --model granite4.1:3b --tasks S1,D2
  bench/.venv/bin/python bench/run_bench.py --agent mini --model granite4.1:3b --no-skills   # what the skills add

Per task: copy the app, plant the fault, commit it as the deployed version, let the supervisor detect
the incident, run the healer, run the hidden check, and roll back to the deployed version if it failed.
--harness takes any bundle directory (default: harness/h3). Each run keeps a copy of the bundle
it used in every task's work area. Work areas and results go to bench/runs/<time>-<model>-h<version>[-nothink][-noskills]/.
"""
import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

BENCH = Path(__file__).resolve().parent
ROOT = BENCH.parent
DEVICE = ROOT / "device"
sys.path[:0] = [str(BENCH), str(DEVICE / "supervisor")]
import agents  # noqa: E402
import monitor  # noqa: E402
import tasks  # noqa: E402

GIT = ["git", "-c", "user.name=edge-bench", "-c", "user.email=edge-bench@localhost",
       "-c", "commit.gpgsign=false", "-c", "init.defaultBranch=main"]


def git(device, *args):
    return subprocess.run([*GIT, *args], cwd=device, capture_output=True, text=True, check=True).stdout


def setup(task, work, harness, no_skills):
    ignore = shutil.ignore_patterns(*tasks.DEVICE_IGNORE)
    shutil.copytree(DEVICE / "robot", work / "device", ignore=ignore)
    shutil.copytree(DEVICE / "sim", work / "sim", ignore=shutil.ignore_patterns(*tasks.SIM_RUNTIME))
    shutil.copytree(DEVICE / "supervisor", work / "supervisor", ignore=ignore)
    skills = json.loads((harness / "harness.json").read_text()).get("knowledge_dir", "skills/").strip("/")
    skip = ["__pycache__"] + ([skills] if no_skills else [])
    shutil.copytree(harness, work / "harness", ignore=shutil.ignore_patterns(*skip))
    (work / "outbox").mkdir()
    tasks.inject(task, work / "device")
    git(work / "device", "init", "-q")
    git(work / "device", "add", "-A")
    git(work / "device", "commit", "-qm", "deployed")
    (work / "scenario.json").write_text(json.dumps(task.scenario, indent=2) + "\n")


def run_task(task, root, args):
    work = root / task.id
    device = work / "device"
    setup(task, work, Path(args.harness).resolve(), args.no_skills)
    row = {"task": task.id, "title": task.title, "cause": task.cause}

    incident = monitor.classify(monitor.run_app(device, work / "sim", task.scenario))
    if incident is None:
        return row | {"detected": "none (invalid task)", "passed": False}
    (work / "incident.json").write_text(json.dumps(incident, indent=2) + "\n")
    row["detected"] = f"tier {incident['tier']} {incident['kind']}"
    snapshot = tasks.sim_snapshot(work / "sim")

    start = time.monotonic()
    try:
        if args.agent == "none":
            outcome = {"exit_status": "did nothing", "steps": 0}
        elif args.agent == "oracle":
            tasks.oracle(task, work)
            outcome = {"exit_status": "done", "steps": 0}
        else:
            outcome = agents.run_mini(work, args.model, args.step_limit, args.time_limit,
                                      think=False if args.no_think else None)
    except Exception as e:  # one broken run must not stop the benchmark
        outcome = {"exit_status": f"error: {e!r}"[:300], "steps": None}
    row |= {"agent_exit": outcome["exit_status"], "agent_steps": outcome["steps"],
            "agent_seconds": round(time.monotonic() - start, 1)}

    row |= tasks.verify(task, work, snapshot)
    git(device, "add", "-A")
    (work / "agent.diff").write_text(git(device, "diff", "--cached"))
    git(device, "reset", "-q")
    if not row["passed"]:  # a failed heal never stays deployed
        git(device, "checkout", "--", ".")
        git(device, "clean", "-fdq")
        row["rolled_back"] = True
    return row


def print_table(rows):
    cols = ["task", "cause", "detected", "agent_exit", "agent_steps", "agent_seconds", "fixed", "reported", "passed"]
    cells = [[str(r.get(c, "")) for c in cols] for r in rows]
    widths = [max(len(c), *(len(row[i]) for row in cells)) for i, c in enumerate(cols)]
    print("  ".join(c.ljust(w) for c, w in zip(cols, widths)))
    for row in cells:
        print("  ".join(v.ljust(w) for v, w in zip(row, widths)))
    print(f"passed {sum(bool(r.get('passed')) for r in rows)}/{len(rows)}")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--agent", choices=["none", "oracle", "mini"], default="oracle")
    p.add_argument("--model", default="granite4.1:3b", help="Ollama tag or LiteLLM model name (agent mini)")
    p.add_argument("--harness", default=str(ROOT / "harness" / "h3"), help="harness bundle directory")
    p.add_argument("--no-skills", action="store_true", help="leave out the bundle's skills, to measure what they add")
    p.add_argument("--no-think", action="store_true", help="turn a thinking model's thinking off (Ollama think=false)")
    p.add_argument("--tasks", default=",".join(tasks.BY_ID), help="comma-separated task ids")
    p.add_argument("--step-limit", type=int, default=30, help="model calls per task")
    p.add_argument("--time-limit", type=int, default=600, help="seconds per task")
    args = p.parse_args()

    started = datetime.now()
    version = json.loads((Path(args.harness) / "harness.json").read_text()).get("version")
    model = re.sub(r"[^A-Za-z0-9.]+", "-", args.model) if args.agent == "mini" else args.agent
    label = f"{model}-h{version}" + ("-nothink" if args.no_think else "") + ("-noskills" if args.no_skills else "")
    root = BENCH / "runs" / f"{started:%Y%m%d-%H%M%S}-{label}"
    root.mkdir(parents=True)
    rows = []
    for task_id in args.tasks.split(","):
        rows.append(run_task(tasks.BY_ID[task_id.strip()], root, args))
        r = rows[-1]
        print(f"{r['task']}: {'PASS' if r.get('passed') else 'FAIL'} ({r.get('detected')}, agent {r.get('agent_exit')})",
              flush=True)
    meta = {"agent": args.agent, "model": args.model if args.agent == "mini" else None,
            "harness": str(Path(args.harness).resolve()), "harness_version": version, "no_skills": args.no_skills,
            "think": "off" if args.no_think else "default",
            "step_limit": args.step_limit, "time_limit": args.time_limit, "started": started.isoformat(timespec="seconds")}
    (root / "results.json").write_text(json.dumps({"meta": meta, "results": rows}, indent=2) + "\n")
    print()
    print_table(rows)
    print(f"results: {root / 'results.json'}")


if __name__ == "__main__":
    main()
