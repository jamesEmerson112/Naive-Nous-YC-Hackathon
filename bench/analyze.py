#!/usr/bin/env python3
"""Summarize benchmark runs per config: passes, failure categories, time and tokens.

  python3 bench/analyze.py bench/runs/<run> [<run> ...] [--json trials.json]

A config is (model, thinking, harness version, skills); runs of the same config are pooled. Tokens come
from each model call's usage in trajectory.json: output tokens include thinking, and the largest prompt
is flagged when it passes 15k (the Ollama context is 16k).

Failure category of a failed trial, first match wins:
  context full  a prompt over 15k tokens
  wrong park    a software fault reported as hardware or parked
  gave up       reported --action none
  workaround    the diff adds try/except, .get(, is None / is not None, hasattr or getattr,
                or any code change for the hardware fault
  blind edit    edits that never landed (edit.py misses, sed that changed nothing), or the diff
                misses the file the fault is in
  time limit / step limit   the run hit --time-limit or --step-limit
  wrong fix     anything else (for example a patch that passes health.py but not the hidden check)
"""
import argparse
import json
import re
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tasks  # noqa: E402

WORKAROUND = re.compile(r"\btry:|\bexcept\b|\.get\(|is None|is not None|hasattr\(|getattr\(")
EDIT_CMD = re.compile(r"edit\.py|sed\s+(-\S+\s+)*-i|>\s*\S+\.(py|toml)\b")


def usage_of(message):
    response = (message.get("extra") or {}).get("response")
    return response if isinstance(response, dict) else None


def trajectory_stats(path):
    stats = {"output_tokens": 0, "max_prompt_tokens": 0, "thinking_chars": 0, "edit_attempts": 0,
             "edit_misses": 0, "calls": 0}
    if not path.exists():
        return stats
    for m in json.loads(path.read_text()).get("messages", []):
        response = usage_of(m)
        if response:
            stats["calls"] += 1
            usage = response.get("usage") or {}
            stats["output_tokens"] += usage.get("completion_tokens") or 0
            stats["max_prompt_tokens"] = max(stats["max_prompt_tokens"], usage.get("prompt_tokens") or 0)
            choice = (response.get("choices") or [{}])[0]
            stats["thinking_chars"] += len((choice.get("message") or {}).get("reasoning_content") or "")
        extra = m.get("extra") or {}
        if m.get("role") == "assistant":
            stats["edit_attempts"] += sum(bool(EDIT_CMD.search(a.get("command", ""))) for a in extra.get("actions", []))
        if m.get("role") in ("tool", "user") and "the old text is not in" in str(extra.get("raw_output", "")):
            stats["edit_misses"] += 1
    return stats


def diff_stats(path):
    text = path.read_text() if path.exists() else ""
    files = sorted(set(re.findall(r"^\+\+\+ b/(\S+)", text, re.M)))
    added = [line[1:] for line in text.splitlines() if line.startswith("+") and not line.startswith("+++")]
    return files, added


def categorize(t):
    if t["passed"]:
        return "-"
    task = tasks.BY_ID[t["task"]]
    report = t["report"] or {}
    if t["max_prompt_tokens"] > 15000:
        return "context full"
    if task.cause == "software" and (report.get("action") == "park" or report.get("cause") == "hardware"):
        return "wrong park"
    if report.get("action") == "none":
        return "gave up"
    if (task.cause == "hardware" and t["diff_files"]) or (
            task.cause == "software" and any(WORKAROUND.search(line) for line in t["diff_added"])):
        return "workaround"
    planted = {file for file, _, _ in task.edits}
    if task.cause == "software" and ((t["edit_attempts"] and not t["diff_files"]) or
                                     (t["diff_files"] and not planted & set(t["diff_files"]))):
        return "blind edit"
    if t["agent_exit"] == "TimeExceeded":
        return "time limit"
    if t["agent_exit"] == "LimitsExceeded":
        return "step limit"
    return "wrong fix"


def trials(run_dir):
    results = json.loads((run_dir / "results.json").read_text())
    meta = results["meta"]
    for row in results["results"]:
        task_dir = run_dir / row["task"]
        report_file = task_dir / "outbox" / "report.json"
        files, added = diff_stats(task_dir / "agent.diff")
        t = {"run": run_dir.name, "model": meta.get("model") or meta["agent"], "think": meta.get("think", "default"),
             "harness": meta.get("harness_version", meta.get("harness")), "skills": not meta.get("no_skills"),
             "task": row["task"], "passed": bool(row.get("passed")), "agent_exit": row.get("agent_exit"),
             "steps": row.get("agent_steps"), "seconds": row.get("agent_seconds"),
             "report": json.loads(report_file.read_text()) if report_file.exists() else None,
             "diff_files": files, "diff_added": added, **trajectory_stats(task_dir / "trajectory.json")}
        t["category"] = categorize(t)
        yield t


def fmt_tokens(n):
    return f"{n / 1000:.1f}k" if n >= 1000 else str(round(n))


def table(config, rows):
    runs = sorted({t["run"] for t in rows})
    model, think, harness, skills = config
    print(f"### {model}, thinking {think}, h{harness}{'' if skills else ', no skills'} "
          f"({len(runs)} run{'s' * (len(runs) > 1)}, {len(rows)} trials)")
    print("| task | passed | median s | median steps | median output tok | largest prompt | failures |")
    print("|---|---|---|---|---|---|---|")
    by_task = defaultdict(list)
    for t in rows:
        by_task[t["task"]].append(t)
    for task_id in tasks.BY_ID:
        ts = by_task.get(task_id)
        if not ts:
            continue
        fails = Counter(t["category"] for t in ts if not t["passed"])
        biggest = max(t["max_prompt_tokens"] for t in ts)
        print(f"| {task_id} | {sum(t['passed'] for t in ts)}/{len(ts)} "
              f"| {statistics.median(t['seconds'] or 0 for t in ts):.0f} "
              f"| {statistics.median(t['steps'] or 0 for t in ts):.0f} "
              f"| {fmt_tokens(statistics.median(t['output_tokens'] for t in ts))} "
              f"| {fmt_tokens(biggest)}{' (over 15k)' if biggest > 15000 else ''} "
              f"| {', '.join(f'{k} x{v}' for k, v in fails.most_common()) or '-'} |")
    passed = sum(t["passed"] for t in rows)
    out = sum(t["output_tokens"] for t in rows)
    top = Counter(t["category"] for t in rows if not t["passed"]).most_common(2)
    print(f"| **total** | **{passed}/{len(rows)}** | {statistics.median(t['seconds'] or 0 for t in rows):.0f} "
          f"| {statistics.median(t['steps'] or 0 for t in rows):.0f} "
          f"| {fmt_tokens(out)} total, {fmt_tokens(out / len(rows))} per heal "
          f"| {fmt_tokens(max(t['max_prompt_tokens'] for t in rows))} "
          f"| top: {', '.join(f'{k} x{v}' for k, v in top) or '-'} |")
    thinking = sum(t["thinking_chars"] for t in rows)
    print(f"\nthinking text returned: {thinking} chars; runs: {', '.join(runs)}\n")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("runs", nargs="+", type=Path)
    p.add_argument("--json", type=Path, help="also write every trial's record here")
    args = p.parse_args()
    all_trials = [t for run in args.runs for t in trials(run)]
    configs = defaultdict(list)
    for t in all_trials:
        configs[(t["model"], t["think"], t["harness"], t["skills"])].append(t)
    for config, rows in configs.items():
        table(config, rows)
    if args.json:
        args.json.write_text(json.dumps(all_trials, indent=1, default=str) + "\n")


if __name__ == "__main__":
    main()
