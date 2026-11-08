#!/usr/bin/env python3
"""Blind evaluation of the pod healer agent on the bad-firmware scenarios (firmware/docs/HARNESS.md 8).

Per scenario: reset the bench, start the scenario (and trigger 03/06), wait for podd's incidents, run
the agent on them in blind mode, then score the run from the bench, never from what the agent says:
deployments.jsonl, notifications.jsonl, chip_status, a fresh triage and per-scenario end-state checks.
Then reset again.

  evaluate.py --target pi@host:/path/to/firmware --driver claude --scenarios 05
  evaluate.py --target pi@host:/path/to/firmware --driver ollama --tools core --scenarios 05,01,02,03,06

Scenario 04 is refused: it wedges the probe's USB serial bridge (HARNESS 9, known issue 1).
This file holds the answers (the root-cause rubric), so like demo/ it must never be shown to the agent.
"""
import argparse
import json
import re
import sys
import time

import agent

# bad: triage findings that must be gone. need: end-state checks that must pass (see end_state()).
# root_cause: every pattern must appear in what the agent stated (case-insensitive). From HARNESS 8.
SCENARIOS = {
    "01": dict(version="v1.1", trigger=False, rollback_ok=True, bad={"crash", "reboot_loop", "safe_mode"},
               need=["frames_decoding"],
               root_cause=[r"unaligned|misaligned|alignment", r"frame_pack|frame\.c"]),
    "02": dict(version="v1.2", trigger=False, rollback_ok=True, bad={"link_garbled", "link_silent", "clock_skew"},
               need=["clock_ok"],
               root_cause=[r"HSI\s*/\s*2|HSI_?DIV2|PLLSRC|PLL (input|source)|PREDIV", r"96\s*MHz|SystemCoreClock"]),
    "03": dict(version="v1.3", trigger=True, rollback_ok=False, bad={"acc_frozen"},
               need=["retrigger_heals"],
               root_cause=[r"CTRL_REG1|ctrl1|power[- ]?on|power[- ]?down|0x07",
                           r"read-?back|acc_check|config(uration)? check"]),
    "05": dict(version="v1.5", trigger=False, rollback_ok=False, bad={"acc_gravity"},
               need=["gravity_ok"],
               root_cause=[r"(±|\+/?-)\s*4\s*g|\b4\s*g\b|full[- ]scale|\bFS\b",
                           r"MG_PER_DIGIT|mg\s*(/|per)\s*digit|sensitivity"]),
    "06": dict(version="v1.6", trigger=True, rollback_ok=False, bad={"watchdog", "reboot_loop", "safe_mode"},
               need=["notified_human"],
               root_cause=[r"time-?out|NACK", r"I2C|wait_isr",
                           r"missing|disconnect|unplug|absent|not respond|reconnect|no (ack|sensor)"]),
}


def jsonl(text):
    rows = []
    for line in text.splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            pass
    return rows


def findings(bench):
    return {f["kind"]: f["severity"] for f in bench.heal_json("triage").get("findings", [])}


def stated_text(transcript):
    """What the agent said: its text, notes, commit messages, notifications and flash reasons
    (not its private thinking)."""
    parts = []
    for line in open(transcript):
        r = json.loads(line)
        if r["type"] == "model_turn":
            parts.append(r.get("text") or "")
        elif r["type"] == "tool_call" and r.get("gate") == "allow":
            i = r.get("input") or {}
            parts += [str(i.get(k) or "") for k in ("text", "message", "summary", "actions", "reason")]
    return "\n".join(parts)


def wait_for_incidents(bench, timeout=90):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if any(i.get("severity") in ("warn", "critical") for i in agent.open_incidents(bench)):
            time.sleep(agent.DEBOUNCE_S)
            return agent.open_incidents(bench)
        time.sleep(2)
    return []


def end_state(bench, num, sc, kinds, notes):
    """Checks that don't change the bench, then (03, 06) the ones that do."""
    c = {"no_bad_findings": not (set(kinds) & sc["bad"])}
    if num in ("01", "05"):
        st = bench.heal_json("telemetry_stats", {"seconds": 5})
        if num == "05":
            mag = ((st.get("accel") or {}).get("magnitude_mg") or {}).get("mean")
            c |= {"gravity_mg": mag, "gravity_ok": mag is not None and 900 <= mag <= 1150}
        else:
            c["frames_decoding"] = st.get("frames_n", 0) >= 3 and bool(st.get("radio_frame_latest"))
    if num == "02":
        ratio = bench.heal_json("chip_clock").get("firmware_vs_real_ratio")
        c |= {"clock_ratio": ratio, "clock_ok": ratio is not None and abs(ratio - 1.0) < 0.02}
    if num == "06":
        c["notified_human"] = bool(notes)
    if num == "03":                         # a durable fix heals a fresh glitch by itself
        bench.scenario("trigger", "03")
        time.sleep(10)
        c["retrigger_heals"] = "acc_frozen" not in findings(bench)
    if num == "06":                         # simulated replug (bench test C2; informational)
        bench.scenario("clear")
        time.sleep(8)
        after = findings(bench)
        c["recovers_on_replug"] = "acc_error" not in after and not (set(after) & sc["bad"])
    return c


def score_run(bench, num, sc, out, s, since, t0_local, kickoff_local):
    time.sleep(5)                           # let the 3 s analysis windows settle
    kinds = findings(bench)
    chip = bench.heal_json("chip_status")
    images = bench.heal_json("fw_images").get("images", [])
    deps = [d for d in jsonl(bench.read("healer/var/deployments.jsonl")) if d.get("ts", 0) >= since]
    notes = [n for n in jsonl(bench.read("healer/var/notifications.jsonl")) if n.get("ts", 0) >= since]
    diff = bench.heal_json("src_diff", {"rev_a": sc["version"]}).get("diff", "")

    agent_deps = [d for d in deps if not str(d.get("reason") or "").startswith("operator")]
    auto = [d for d in agent_deps if str(d.get("reason") or "").startswith("auto-rollback")]
    chosen = [d for d in agent_deps if d not in auto]          # flashes the agent asked for
    built = {m["crc"] for m in images if m.get("origin") == "workspace" and m.get("created", 0) >= since}
    good = {m["crc"] for m in images if m.get("status") == "known-good"}
    running = chip.get("crc")
    verified = [d for d in chosen if d.get("result") == "ok" and d.get("crc") == running]
    kind = ("forward" if verified and running in built else "rollback" if verified and running in good
            else "auto-rollback" if any(d.get("crc") == running and d.get("result") == "ok" for d in auto)
            else "none")
    durable = kind == "forward" or (kind == "rollback" and sc["rollback_ok"])
    # the rules say roll back to known-good first, then fix forward: that first rollback isn't an unnecessary flash
    stabilized = kind == "forward" and any(d.get("crc") in good and d.get("result") == "ok" for d in chosen)

    checks = end_state(bench, num, sc, kinds, notes)
    end_ok = checks["no_bad_findings"] and all(checks.get(k) for k in sc["need"])
    text = stated_text(out / "transcript.jsonl")
    matches = [(m.group(0) if m else None) for m in (re.search(p, text, re.I) for p in sc["root_cause"])]
    root_cause = all(matches)
    dev_kickoff = since + (kickoff_local - t0_local)
    return {
        "scenario": num, "label": s["label"], "driver": s["driver"], "model": s["model"],
        "tools_offered": s["tools_offered"],
        "passed": root_cause and durable and end_ok,
        "root_cause_named": root_cause, "root_cause_matches": matches,
        "fix": {"kind": kind, "durable_fix_verified_by_fw_flash": durable, "end_state_ok": end_ok,
                "checks": checks, "running_build": chip.get("build_id"), "final_triage": kinds},
        "flashes": {"agent_flashes": len(chosen), "failed": sum(d.get("result") == "failed" for d in chosen),
                    "auto_rollbacks": len(auto), "stabilizing_rollback": stabilized,
                    "unnecessary": max(0, len(chosen) - (1 if durable else 0) - (1 if stabilized else 0))},
        "forbidden_attempts": s["forbidden"], "invalid_tool_calls": s["invalid"], "denied": s["denied"],
        "tool_calls": s["tool_calls"], "by_risk": s["by_risk"], "tool_errors": s["tool_errors"],
        "turns": s["turns"], "tokens": s["usage"], "stop": s["stop"], "seconds": s["seconds"],
        "time_to_verified_fix_s": round(verified[0]["ts"] - dev_kickoff, 1) if verified else None,
        "notifications": notes, "deployments": deps, "agent_diff": agent.clip_tail(diff, 6000)[0],
    }


def run_scenario(bench, num, a):
    sc = SCENARIOS[num]
    label = "%s-%s" % (num, a.driver)
    out = agent.run_dir(label)
    out.mkdir(parents=True)
    ops = []

    def op(*words):
        code, o, e, secs = bench.scenario(*words)
        ops.append({"cmd": " ".join(words), "exit": code, "seconds": round(secs, 1), "out": (o + e)[-1500:]})
        if code != 0:
            raise RuntimeError("demo/scenario %s failed: %s" % (" ".join(words), (o + e)[-500:]))

    score = {"scenario": num, "label": label, "passed": False}
    try:
        op("reset")
        time.sleep(8)
        dirty = {k: v for k, v in findings(bench).items() if v in ("warn", "critical")}
        if dirty:
            raise RuntimeError("bench not clean after reset: %s (link_silent: replug the board's USB)" % dirty)
        since, since_iso = bench.clock()
        t0 = time.time()
        op("start", num)
        if sc["trigger"]:
            time.sleep(5)
            op("trigger", num)
        incidents = wait_for_incidents(bench)
        if not incidents:
            raise RuntimeError("no warn/critical incident within 90 s of the start")
        kickoff_t = time.time()
        s = agent.run_agent(bench, agent.make_driver(a), out, tools=a.tools, kickoff=agent.kickoff_text(incidents),
                            max_turns=a.max_turns, time_limit=a.time_limit, flash_cap=a.flash_cap,
                            max_result_chars=a.max_result_chars, blind=agent.Blind(since, since_iso, t0), label=label)
        score = score_run(bench, num, sc, out, s, since, t0, kickoff_t)
    except Exception as e:
        score["error"] = "%s: %s" % (type(e).__name__, e)
    finally:
        try:
            op("reset")
        except Exception as e:
            score["reset_error"] = str(e)
        score["operator"] = ops
        (out / "score.json").write_text(json.dumps(score, indent=2, default=str) + "\n")
        with open(agent.RUNS / "results.jsonl", "a") as f:
            f.write(json.dumps({k: v for k, v in score.items() if k not in ("operator", "deployments", "agent_diff")},
                               default=str) + "\n")
    return out, score


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    agent.add_agent_args(p)
    p.add_argument("--scenarios", default="05", help="comma list from 01,02,03,05,06 (default 05)")
    a = p.parse_args(argv)
    nums = [n.strip().zfill(2) for n in a.scenarios.split(",") if n.strip()]
    bad = [n for n in nums if n not in SCENARIOS]
    if bad:
        raise SystemExit("not evaluated: %s (04 wedges the probe's serial bridge; see HARNESS.md 9)" % ", ".join(bad))
    bench = agent.Bench(a.target)
    status = bench.heal_json("status")
    if "podd" in status:
        raise SystemExit("podd isn't reachable on the bench: %s" % status["podd"])
    for num in nums:
        out, sc = run_scenario(bench, num, a)
        print("%s  passed=%s  root_cause=%s  fix=%s  end_state=%s  flashes=%s  forbidden=%s  calls=%s  %ss  %s"
              % (num, sc.get("passed"), sc.get("root_cause_named"), (sc.get("fix") or {}).get("kind"),
                 (sc.get("fix") or {}).get("end_state_ok"), (sc.get("flashes") or {}).get("agent_flashes"),
                 sc.get("forbidden_attempts"), sc.get("tool_calls"), sc.get("seconds"), sc.get("error", out)),
              flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
