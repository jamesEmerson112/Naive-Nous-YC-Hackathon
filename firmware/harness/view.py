#!/usr/bin/env python3
"""Replay a saved agent run in the terminal, the way it looked live: thinking, text, each tool call with
the gate's decision, result summaries, and the final report.

  python3 view.py                         # the newest run in runs/
  python3 view.py runs/<run dir>          # or a transcript.jsonl
  python3 view.py --thinking brief --delay 0.4 runs/<run dir>   # shorter thinking, paced for an audience
"""
import argparse
import json
import signal
import sys
import time
from pathlib import Path

import console

RUNS = Path(__file__).resolve().parent / "runs"


def find(path):
    if path is None:
        runs = sorted(d for d in RUNS.iterdir() if (d / "transcript.jsonl").exists()) if RUNS.exists() else []
        if not runs:
            raise SystemExit("no runs in %s" % RUNS)
        return runs[-1] / "transcript.jsonl"
    p = Path(path)
    return p / "transcript.jsonl" if p.is_dir() else p


def replay(transcript, ui, delay=0.0):
    report, last_turn = "", None
    for line in open(transcript):
        r = json.loads(line)
        kind = r.get("type")
        if kind == "run_start":
            ui.run_start(r)
        elif kind == "model_turn":
            ui.turn_start(r["turn"])
            ui.turn_end(r)
            if (r.get("text") or "").strip():
                report = r["text"]
            last_turn = r["turn"]
        elif kind == "tool_call":
            ui.call(r)
            ui.result(r)
        elif kind == "harness_note":
            ui.note(r.get("text", ""))
        elif kind == "model_error":
            ui.line(ui.c("bred", "  model error: %s" % r.get("error")))
        elif kind == "run_end":
            ui.run_end(r, report)
        if delay and kind in ("model_turn", "tool_call"):
            time.sleep(delay)
    score = Path(transcript).parent / "score.json"
    if score.exists():
        s = json.loads(score.read_text())
        ui.line()
        ui.line(ui.c("bold", "  score: ") + ("PASSED" if s.get("passed") else "FAILED") +
                ("  (%s)" % s["error"] if s.get("error") else ""))
        for k, v in (s.get("checks") or {}).items():
            ui.step(bool(v), k)
    return last_turn


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("run", nargs="?", help="run directory or transcript.jsonl (default: newest in runs/)")
    p.add_argument("--thinking", choices=["full", "brief", "off"], default="full")
    p.add_argument("--delay", type=float, default=0.0, help="seconds to pause after each turn and tool call")
    a = p.parse_args(argv)
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)      # quiet when piped into head or less
    replay(find(a.run), console.Console(thinking=a.thinking), a.delay)
    return 0


if __name__ == "__main__":
    sys.exit(main())
