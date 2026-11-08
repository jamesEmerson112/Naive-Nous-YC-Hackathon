#!/usr/bin/env python3
"""Summarize one or more fault-lab runs as a Markdown table.

Usage: python3 report.py RUN_DIR [RUN_DIR ...]   (each holding a results.json from run_lab.py)
"""
import json
import statistics
import sys
from pathlib import Path

from faults import FAULTS


def main(dirs):
    rows = [r for d in dirs for r in json.loads((Path(d) / "results.json").read_text())]
    sides = [s for s in ("pi", "hermes") if any(r["side"] == s for r in rows)]
    head = "| Task | Fault | " + " | ".join(f"{s}: fixed? | {s}: time | {s}: steps" for s in sides) + " |"
    print(head)
    print("|" + "---|" * (2 + 3 * len(sides)))
    totals = {s: [0, 0] for s in sides}
    for fid, f in FAULTS.items():
        cells = [fid, f["title"]]
        for s in sides:
            rs = [r for r in rows if r["fault"] == fid and r["side"] == s]
            if not rs:
                cells += ["–", "–", "–"]
                continue
            ok = sum(r["passed"] for r in rs)
            totals[s][0] += ok
            totals[s][1] += len(rs)
            verdict = f"{'✅' if ok == len(rs) else '❌' if ok == 0 else '⚠️'} {ok}/{len(rs)}"
            cells += [verdict, f"{statistics.median(r['wall_s'] for r in rs):.0f} s",
                      f"{statistics.median(len(r['tools']) for r in rs):.0f}"]
        print("| " + " | ".join(cells) + " |")
    total_cells = ["**All**", ""] + [c for t in totals.values() for c in (f"**{t[0]}/{t[1]}**", "", "")]
    print("| " + " | ".join(total_cells) + " |")
    print()
    for r in rows:
        if not r["passed"]:
            print(f"- {r['fault']} · {r['side']}: {'; '.join(r['reasons'])}" + (f" (error: {r['error']})" if r.get("error") else ""))
    cost = sum(r.get("cost_usd") or 0 for r in rows if r["side"] == "pi")
    print(f"\npi spent ${cost:.2f} of Naïve credits across {sum(1 for r in rows if r['side'] == 'pi')} runs.")


if __name__ == "__main__":
    main(sys.argv[1:])
