"""One append-only event log the autonomous loop's pieces share: runs/events.jsonl
($ARMSIM_EVENTS overrides). The dispatcher, the order intake, the soak and the commander
(Hermes, or the scripted runner) write here; Mission Control and the soak report read it.

Every line: {"v": 1, "ts": <unix s>, "at": <ISO UTC>, "src": ..., "kind": ..., "text": ..., **fields}
  src   order | dispatch | commander | soak | mission | host
  kind  order_placed, card_claimed, job_started, skill, incident, heal, job_result, card_done,
        card_blocked, fault_planted, fault_cleared, soak_tick, soak_outcome, presenter, error
  text  one plain sentence a person can read on the screen
Common fields: card (crd_ id), order (ord- id), job, ok, ms, incident {kind, summary}.
"""
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATH = Path(os.environ.get("ARMSIM_EVENTS") or ROOT / "runs" / "events.jsonl")


def emit(src, kind, text, *, path=None, echo=True, **fields):
    p = Path(path or PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    ts = time.time()
    ev = {"v": 1, "ts": round(ts, 3), "at": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="seconds"),
          "src": src, "kind": kind, "text": text, **fields}
    line = json.dumps(ev, default=str)
    with open(p, "a", encoding="utf-8") as f:     # one write per line: appends don't interleave
        f.write(line + "\n")
    if echo:
        print(f"[{src}] {text}", flush=True)
    return ev


def read(path=None, *, since=0.0, limit=None, src=None, kinds=None):
    p = Path(path or PATH)
    if not p.exists():
        return []
    out = []
    with open(p, encoding="utf-8") as f:
        for line in f:
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get("ts", 0) <= since:
                continue
            if src and ev.get("src") != src:
                continue
            if kinds and ev.get("kind") not in kinds:
                continue
            out.append(ev)
    return out[-limit:] if limit else out
