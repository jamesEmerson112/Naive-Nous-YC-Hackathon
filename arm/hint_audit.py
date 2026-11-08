#!/usr/bin/env python3
"""Hint audit for the arm heal test: capture everything the agent would read and flag anything that gives
the answer away.

  python3 arm/hint_audit.py            # deploys v1.7 on the bench, captures, resets to known-good
  python3 arm/hint_audit.py --keep     # leave v1.7 running afterwards
  python3 arm/hint_audit.py --minutes 3 --no-deploy  # audit the bench as it is now, hiding what's older than 3 min

It builds the agent's inputs exactly as arm/heal_test.py does: the system prompt, the tool definitions, the
kickoff (the arm's failure log and podd's incidents), then calls every read tool in the arm tool set through
the harness's blind filter, the way the agent's own calls would go. Each match for an answer term is
labelled: inside firmware source text it's the evidence itself (the changed line has to be visible, or nothing
could be diagnosed); anywhere else it's a leak. Exit 1 if there is a leak. Holds the answer terms, so like
heal_test.py it's operator-side only.
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "firmware" / "harness"))
import agent  # noqa: E402

# "stack overflow" is the briefing's generic list of HardFault causes, not this fault.
TERMS = r"uint16|16[- ]?bits?\b|truncat|(?<!stack )overflow|wrap|\bcast\b|6553[56]|us-dist|16bit|bad-images|" \
        r"scenario|demo/|known-bad|bug\.patch"   # an earlier run's note about this fault would hit uint16/cast/truncat
SOURCE_LINE = re.compile(r"^\s*(\d+\s+|[+\- ])?.*(;|\*/|/\*|\{|\})\s*$")     # C source as src_* tools show it

# The read calls the agent could make: every read tool in agent.ARM_TOOLS, with the arguments a model uses.
CALLS = [("status", {}), ("triage", {}), ("incidents", {"include_resolved": True, "minutes": 600}),
         ("telemetry_stats", {"seconds": 600}), ("telemetry_recent", {"kind": "D", "seconds": 600, "limit": 40}),
         ("telemetry_recent", {"kind": "E", "seconds": 600, "limit": 200}),
         ("telemetry_recent", {"kind": "R", "seconds": 600, "limit": 100}),
         ("telemetry_recent", {"kind": "#", "seconds": 600, "limit": 100}),
         ("telemetry_recent", {"kind": "raw", "seconds": 600, "limit": 100}),
         ("events", {"minutes": 600, "limit": 500}), ("chip_status", {}), ("fw_images", {}), ("src_log", {}),
         ("src_diff", {"rev_a": "v1.0"}), ("src_diff", {"rev_a": "v1.0", "rev_b": "HEAD"}),
         ("src_read", {"path": "src/hcsr04.c"}), ("src_search", {"pattern": "dist_mm|343"}),
         ("pod_cmd", {"command": "help"}), ("pod_cmd", {"command": "info"}), ("pod_cmd", {"command": "health"})]


def lines_of(source, text):
    for ln in text.splitlines():
        yield source, ln


def classify(source, line):
    code_tool = source.split(" ")[0] in ("src_diff", "src_read", "src_search")
    code = re.sub(r'^\s*"text":\s*"|",?\s*$', "", line)         # src_search wraps each source line in JSON
    return "evidence (firmware source)" if code_tool and SOURCE_LINE.match(code) else "LEAK"


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--no-deploy", action="store_true", help="don't deploy v1.7; audit the bench as it is")
    p.add_argument("--minutes", type=float, default=0, help="with --no-deploy: how far back the blind window reaches")
    p.add_argument("--keep", action="store_true", help="leave v1.7 on the pod afterwards")
    a = p.parse_args()
    bench = agent.Bench()
    arm = agent.ArmConfig()
    if a.no_deploy:
        t = time.time() - a.minutes * 60
        since, since_iso, t0 = t, time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(t)), t
    else:
        print("deploying v1.7 (demo/scenario start 07) ...")
        since, since_iso = bench.clock()
        t0 = time.time()
        code, out, err, _ = bench.scenario("start", "07")
        if code:
            raise SystemExit(out + err)
        time.sleep(15)                                  # podd's rule window, then the incident
    blind = agent.Blind(since, since_iso, t0)
    seen = []
    try:
        seen += lines_of("system prompt", agent.system_prompt(bench, agent.FLASH_CAP, arm))
        for t in agent.load_tools(bench, "arm", extra=[agent.ARM_TOOL]):
            seen += lines_of("tool definition %s" % t["name"], t["description"] + " " + json.dumps(t["input_schema"]))
        job = agent.run_arm(bench, arm)
        seen += lines_of("kickoff", agent.arm_kickoff(job, agent.open_incidents(bench)))
        for name, args in CALLS:
            sent = blind.args(name, args)
            obj, text, _, _ = agent.run_tool(bench, name, sent, arm)
            if obj is not None:
                text = json.dumps(blind.result(name, sent, obj), ensure_ascii=False, indent=1)
            label = "%s %s" % (name, json.dumps(args))
            # JSON escapes newlines inside strings: split them so each source line is judged on its own
            seen += lines_of(label, text.replace("\\n", "\n"))
    finally:
        if not a.no_deploy and not a.keep:
            print("resetting to known-good ...")
            bench.scenario("reset")
    hits = [(src, ln.strip(), classify(src, ln)) for src, ln in seen if re.search(TERMS, ln, re.I)]
    sources = sorted({s for s, _ in seen})
    print("\naudited %d lines from %d sources (system prompt, %d tool definitions, kickoff, %d tool calls)"
          % (len(seen), len(sources), sum(s.startswith("tool definition") for s in sources), len(CALLS)))
    for src, ln, kind in hits:
        print("  %-28s %-34s %s" % (kind, src[:34], ln[:110]))
    leaks = [h for h in hits if h[2] == "LEAK"]
    print("\n%d matches: %d in firmware source (the evidence), %d leaks" % (len(hits), len(hits) - len(leaks), len(leaks)))
    return 1 if leaks else 0


if __name__ == "__main__":
    sys.exit(main())
