#!/usr/bin/env python3
"""Bench test: a bad pod firmware makes the arm misbehave, the healer agent finds out why, restores the
known-good firmware, launches the arm's job itself and checks that the expected movement happened.

  python3 arm/heal_test.py                     # finds the arm on USB; model at 192.168.50.1 (server/)
  python3 arm/heal_test.py --no-arm            # start gate only, nothing moves
  python3 arm/heal_test.py --runs 3 --view brief
  python3 arm/heal_test.py --sim               # real model, SIMULATED pod (no hardware)

The fault is release v1.7 (firmware/demo/bad-images/07-us-dist-16bit): a misplaced uint16_t cast in the
HC-SR04 distance math wraps every distance into 0-32 mm, whatever is in front of the sensor. The echo time
itself stays right, so the pod's own telemetry contradicts itself.

  1. reset the pod to v1.0 (known-good); the arm's start gate must PASS (checked without moving). This proves
     the space in front of the sensor is clear, so what follows can only come from the firmware.
  2. the operator deploys v1.7 (demo/scenario start 07); podd must open us_mismatch. Then the operator runs
     the arm's job: the gate believes something is within ~30 mm, so the obstacle reflex swings the base back
     and forth (+-15 deg, irregular, 8 s) and gives up (exit 4). Without an arm, the gate just refuses (exit 2).
  3. the agent runs, live on screen. It's woken by the job's log and podd's incidents, in blind mode, with the
     observe/flash/notify tools plus arm_run, arm_state and arm_report, and nothing else (no shell, no demo/
     or pod/). Its rules: restore service, launch the job itself, check the motion report, report.
  4. the result is checked on the bench, never from what the agent says: the chip runs known-good and the
     agent flashed it; the agent ran the job after its flash; that job's recorded motion matches the plan
     (every frame, no reflex, every joint reaching its commanded range); a fresh gate run passes; triage is
     clean; and the agent named the root cause and reported the motion it checked.
  5. reset the pod to known-good (unless --keep).

--sim swaps the bench for arm/tests/simpod.py: the pod's distance math, triage rules, source and the arm's
start gate are the real code, but flashing is instant and nothing physical happens. It rehearses the model
on this scenario when the bench is busy or broken; it proves nothing about the bench.

Exit 0 when every check of every run passes. Runs land in firmware/harness/runs/<time>-arm07-<driver>/
(transcript.jsonl, report.md, score.json); replay one with firmware/harness/view.py <run dir>.
This file holds the answers (the root-cause rubric): never show it to the agent.
"""
import argparse
import glob
import json
import os
import re
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "firmware" / "harness"))
import agent  # noqa: E402
import console  # noqa: E402
import evaluate  # noqa: E402

SCENARIO, BAD_VERSION = "07", "v1.7"
# Root cause named = the agent blamed the firmware AND described the mechanism. Checked on what it stated
# (text, notes, notifications, flash reasons), not its private thinking.
ROOT_CAUSE = [r"firmware|release|v1\.7|hcsr04|dist_mm|software|\bcode\b",
              r"uint16|16[- ]?bit|truncat|overflow|wrap|cast|conver(t|sion)|formula|calculat|comput|mismatch|"
              r"disagree|inconsistent|does(n.?t| not) match"]
EXACT = r"uint16|16[- ]?bit|truncat|overflow|wrap|cast"     # found the actual defect (reported, not required)

CHECKS = [  # (key, what it means) in the order they're shown; every one must pass
    ("baseline_gate_passed", "on v1.0 the arm's start gate passes (the space in front of the sensor is clear)"),
    ("incident_opened", "on v1.7 podd opens us_mismatch on its own (deterministic rule, no LLM)"),
    ("fault_reproduced", "on v1.7 the arm's job goes wrong (arm: the reflex swings the base and gives up; "
                         "no arm: the gate refuses)"),
    ("root_cause_named", "the agent named the root cause (firmware distance math, not an obstruction)"),
    ("restored_by_agent", "the agent flashed the known-good image and fw_flash verified it"),
    ("known_good_running", "the chip runs the known-good build now (read over SWD)"),
    ("agent_ran_job_after_fix", "the agent launched the arm's job itself after its flash, and it exited 0"),
    ("expected_motion_happened", "that job's recorded motion matches the plan (arm: every frame played, no reflex, "
                                 "every joint reached >= 90% of its commanded range; no arm: gate passed)"),
    ("agent_reported_motion", "the agent's report states the motion it checked"),
    ("gate_passes_now", "a fresh run of the arm's start gate passes (run by this test, not the agent)"),
    ("triage_clean", "triage no longer reports us_mismatch"),
]
MIN_COVERAGE = 0.9
MOTION_WORDS = r"frames?|coverage|range|moved|motion|wave|playback"


def find_arm_port():
    """The servo bus adapter: a /dev/serial/by-id entry that isn't the pod's debug probe."""
    for link in sorted(glob.glob("/dev/serial/by-id/*")):
        if not any(x in link for x in ("SEGGER", "J-Link", "STLink", "STM32")):
            return link
    return None


def motion_ok(report, with_arm):
    """The job's recorded motion matches the plan. Returns (ok, why)."""
    if not report:
        return False, "no motion report"
    if not with_arm:
        return report.get("exit") == 0, "gate only: exit %s" % report.get("exit")
    pb, rx = report.get("playback") or {}, report.get("obstacle_reflex") or {}
    cov = {j: c for j, c in (pb.get("range_coverage") or {}).items() if c is not None}
    low = {j: c for j, c in cov.items() if c < MIN_COVERAGE}
    ok = report.get("exit") == 0 and pb.get("complete") and not rx.get("ran") and cov and not low
    return bool(ok), "frames %s/%s, reflex %s, lowest coverage %s" % (
        pb.get("frames_played"), pb.get("frames_planned"), "ran" if rx.get("ran") else "no",
        min(cov.items(), key=lambda kv: kv[1]) if cov else None)


class Abort(RuntimeError):
    """The bench isn't in a state where the test means anything."""


def tool_calls(transcript):
    return [r for r in map(json.loads, open(transcript)) if r["type"] == "tool_call" and r.get("gate") == "allow"]


def result_obj(rec):
    text = rec.get("result") or ""
    try:
        return json.loads(text[text.index("{"):])
    except ValueError:
        return {}


def score(bench, transcript, since, pre, gate_cfg, arm_cfg):
    """Every check from the bench. pre: the checks made before the agent ran."""
    kinds = evaluate.findings(bench)
    chip = bench.heal_json("chip_status")
    good = {m["crc"] for m in bench.heal_json("fw_images").get("images", []) if m.get("status") == "known-good"}
    deps = [d for d in evaluate.jsonl(bench.read("healer/var/deployments.jsonl")) if d.get("ts", 0) >= since]
    by_agent = [d for d in deps if not str(d.get("reason") or "").startswith(("operator", "auto-rollback"))]
    restored = [d for d in by_agent if d.get("crc") in good and d.get("result") == "ok"]

    calls = tool_calls(transcript)
    flashed = [i for i, r in enumerate(calls) if r["name"] in ("fw_flash", "fw_rollback")
               and result_obj(r).get("ok") and (result_obj(r).get("image") or {}).get("crc") in good]
    arm_runs = [(i, result_obj(r)) for i, r in enumerate(calls) if r["name"] == "arm_run"]
    after = [o for i, o in arm_runs if flashed and i > flashed[0] and o.get("exit") == 0]
    # the motion is re-read from the run record on the bench, not taken from the tool result the agent saw
    run_id = ((after[-1].get("motion_report") or {}).get("run_id")) if after else None
    report = agent.arm_report(bench, arm_cfg, {"run_id": run_id}) if run_id else None
    motion, motion_why = motion_ok(report, bool(arm_cfg.port))
    final = [json.loads(l) for l in open(transcript)]
    final_text = next((r.get("text") or "" for r in reversed(final) if r["type"] == "model_turn"
                       and (r.get("text") or "").strip()), "")

    gate_now = agent.run_arm(bench, gate_cfg)
    text = evaluate.stated_text(transcript)
    matches = [(m.group(0) if m else None) for m in (re.search(p, text, re.I) for p in ROOT_CAUSE)]
    exact = re.search(EXACT, text, re.I)
    checks = dict(pre) | {
        "root_cause_named": all(matches),
        "restored_by_agent": bool(restored),
        "known_good_running": chip.get("crc") in good and bool(chip.get("running")),
        "agent_ran_job_after_fix": bool(after),
        "expected_motion_happened": motion,
        "agent_reported_motion": bool(after) and bool(re.search(MOTION_WORDS, final_text, re.I)),
        "gate_passes_now": gate_now["passed"],
        "triage_clean": "us_mismatch" not in kinds,
    }
    return {
        "passed": all(checks.get(k) for k, _ in CHECKS),
        "checks": checks,
        "root_cause_matches": matches,
        "found_exact_defect": exact.group(0) if exact else None,
        "running_build": chip.get("build_id"),
        "agent_flashes": [{k: d.get(k) for k in ("crc", "result", "reason")} for d in by_agent],
        "arm_runs": [{"after_flash": bool(flashed) and i > flashed[0], "exit": o.get("exit"),
                      "verdict": o.get("verdict")} for i, o in arm_runs],
        "motion_check": motion_why,
        "gate_now": {k: gate_now[k] for k in ("passed", "exit", "verdict")},
        "final_triage": kinds,
    }


def wait_for_distance(bench, timeout=40):
    """After a flash: wait until D lines flow again (the gate needs 1.5 s of them)."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            if (bench.heal_json("telemetry_stats", {"seconds": 2}).get("distance") or {}).get("n", 0) >= 10:
                return True
        except RuntimeError:
            pass
        time.sleep(1)
    return False


def last_line(job, prefix="ultrasonic:"):
    lines = [ln for ln in job.get("log", "").splitlines() if ln.startswith(prefix)]
    return "\n".join(lines[-2:])


def run_once(bench, a, ui, n_run):
    gate_cfg = agent.ArmConfig(min_mm=a.min_mm)                  # this test's own gate checks: never moves the arm
    arm_cfg = agent.ArmConfig(port=a.arm_port, python=a.arm_python, min_mm=a.min_mm, extra=a.arm_args,
                              robot_id=a.robot_id)
    sim = getattr(a, "sim", False)
    out = agent.run_dir("arm%s%s-%s" % (SCENARIO, "-sim" if sim else "", a.driver))
    out.mkdir(parents=True)
    ops, pre = [], {}
    result = {"scenario": "arm" + SCENARIO, "sim": sim, "run": n_run, "driver": a.driver,
              "model": a.model or "qwen3.5:4b", "passed": False}

    def op(*words):
        code, o, e, secs = bench.scenario(*words)
        ops.append({"cmd": " ".join(words), "exit": code, "seconds": round(secs, 1), "out": (o + e)[-1500:]})
        if code != 0:
            raise Abort("demo/scenario %s failed: %s" % (" ".join(words), (o + e)[-400:]))

    try:
        ui.banner("1/5  reset the pod to the known-good firmware (v1.0)")
        op("reset")
        if not wait_for_distance(bench):
            raise Abort("no distance samples from the pod after the reset (is podd running? is the board on USB?)")
        time.sleep(2)
        base = agent.run_arm(bench, gate_cfg)
        pre["baseline_gate_passed"] = base["passed"]
        ui.step(base["passed"], "arm start gate on v1.0: %s" % base["verdict"], last_line(base))
        if not base["passed"]:
            raise Abort("the gate refuses on known-good firmware too, so the test would prove nothing. Clear the "
                        "space in front of the HC-SR04 (nothing within %d mm), check its wiring (no_echo = wires "
                        "or 5 V), and run again." % a.min_mm)

        ui.banner("2/5  the operator deploys the bad release %s" % BAD_VERSION)
        since, since_iso = bench.clock()
        t0 = time.time()
        op("start", SCENARIO)
        incidents = evaluate.wait_for_incidents(bench, timeout=60)
        pre["incident_opened"] = any(i.get("kind") == "us_mismatch" for i in incidents)
        ui.step(pre["incident_opened"], "podd opened: %s" % (", ".join(i["kind"] for i in incidents) or "nothing"),
                "\n".join(i.get("summary", "") for i in incidents))
        wait_for_distance(bench)
        ui.step(None, "the operator runs the arm's job (%s)" % arm_cfg.describe())
        fault = agent.run_arm(bench, arm_cfg)
        rx = ((fault.get("motion_report") or {}).get("obstacle_reflex") or {})
        pre["fault_reproduced"] = (fault["exit"] == 4 and rx.get("swings", 0) >= 3) if arm_cfg.port else fault["exit"] == 2
        detail = last_line(fault)
        if rx.get("ran"):
            detail = "reflex: %s swings, %s direction changes, base %s deg around %s deg, never cleared\n" % (
                rx.get("swings"), rx.get("direction_changes"), rx.get("base_measured_range_deg"),
                rx.get("base_home_deg")) + detail
        ui.step(pre["fault_reproduced"], "arm job on %s: exit %s, %s" % (BAD_VERSION, fault["exit"], fault["verdict"]),
                detail)
        if not pre["fault_reproduced"]:
            raise Abort("the bad release didn't make the arm's job fail; nothing for the agent to fix")

        ui.banner("3/5  the healer agent takes over (%s %s)" % (a.driver, a.model or "qwen3.5:4b"))
        kickoff_t = time.time()
        s = agent.run_agent(bench, agent.make_driver(a), out, tools=a.tools, kickoff=agent.arm_kickoff(fault, incidents),
                            max_turns=a.max_turns, time_limit=a.time_limit, flash_cap=a.flash_cap,
                            max_result_chars=a.max_result_chars, blind=agent.Blind(since, since_iso, t0),
                            label="arm%s-%s" % (SCENARIO, a.driver), ui=ui, arm=arm_cfg, arm_cap=a.arm_cap)

        ui.banner("4/5  check the result on the bench (not from what the agent said)")
        time.sleep(5)                                    # let the 4 s ultrasonic window settle after a flash
        sc = score(bench, out / "transcript.jsonl", since, pre, gate_cfg, arm_cfg)
        for key, label in CHECKS:
            ui.step(bool(sc["checks"].get(key)), label)
        ui.step(None, "motion: %s" % sc["motion_check"])
        ui.step(None, "found the exact defect (uint16 truncation): %s" % (sc["found_exact_defect"] or "no"))
        result |= sc | {"turns": s["turns"], "tool_calls": s["tool_calls"], "by_risk": s["by_risk"],
                        "denied": s["denied"], "tokens": s["usage"], "stop": s["stop"], "seconds": s["seconds"],
                        "agent_seconds_to_done": round(time.time() - kickoff_t, 1), "run_dir": str(out)}
    except Abort as e:
        result["error"] = str(e)
        ui.step(False, "test aborted: %s" % e)
    except Exception as e:
        result["error"] = "%s: %s" % (type(e).__name__, e)
        ui.step(False, "harness or bench error: %s" % result["error"])
    finally:
        if not a.keep:
            ui.banner("5/5  reset the pod to known-good")
            try:
                op("reset")
                ui.step(None, "known-good v1.0 flashed, workspace back at v1.0")
            except Exception as e:
                result["reset_error"] = str(e)
                ui.step(False, "reset failed: %s" % e)
        result["operator"] = ops
        (out / "score.json").write_text(json.dumps(result, indent=2, default=str) + "\n")
        with open(agent.RUNS / "results.jsonl", "a") as f:
            f.write(json.dumps({k: v for k, v in result.items() if k != "operator"}, default=str) + "\n")
    return out, result


def preflight(bench, a, ui):
    ui.banner("preflight")
    if a.sim:
        ui.step(None, ui.c("byellow", "SIMULATED POD (arm/tests/simpod.py): real model, real rules and gate code, "
                                      "no hardware"))
    st = bench.heal_json("status")
    if "podd" in st and not a.sim:
        raise SystemExit("podd isn't reachable: %s\nstart it in firmware/: healer/bin/podd &" % st["podd"])
    ui.step(True, "podd is running, serial link %s" % st.get("link", {}).get("state"))
    img = REPO / "firmware" / "demo" / "bad-images" / "07-us-dist-16bit" / "image" / "pod.elf"
    if not img.exists():
        raise SystemExit("bad image not built: firmware/demo/scenario build-images 07")
    ui.step(True, "bad release %s image is built" % BAD_VERSION)
    if a.driver == "ollama":
        import urllib.request
        try:
            with urllib.request.urlopen(a.ollama_url.rstrip("/") + "/api/tags", timeout=5) as r:
                names = [m["name"] for m in json.loads(r.read())["models"]]
        except OSError as e:
            raise SystemExit("can't reach the model at %s: %s (is server/start.sh running on the laptop?)"
                             % (a.ollama_url, e))
        model = a.model or "qwen3.5:4b"
        if model not in names:
            raise SystemExit("%s isn't served at %s (has: %s)" % (model, a.ollama_url, ", ".join(names)))
        ui.step(True, "%s is served at %s" % (model, a.ollama_url))
    ui.step(None, "arm: %s" % agent.ArmConfig(port=a.arm_port).describe())


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    agent.add_agent_args(p)
    p.set_defaults(tools="arm", max_turns=30, time_limit=900,
                   ollama_url=os.environ.get("OLLAMA_URL", "http://192.168.50.1:11434"))
    p.add_argument("--runs", type=int, default=1, help="repeat the whole test this many times")
    p.add_argument("--keep", action="store_true", help="don't reset the pod at the end (inspect what the agent left)")
    p.add_argument("--sim", action="store_true", help="simulated pod instead of the bench (see above)")
    p.add_argument("--no-arm", action="store_true", help="don't use the arm even if it's on USB (gate only)")
    a = p.parse_args(argv)
    if not (a.sim or a.no_arm or a.arm_port or a.target):
        a.arm_port = find_arm_port()
    if a.sim:
        if a.arm_port or a.target:
            p.error("--sim can't be combined with --arm-port or --target")
        sys.path.insert(0, str(Path(__file__).resolve().parent / "tests"))
        import simpod
        bench = simpod.SimPod("v1.0")
    else:
        bench = agent.Bench(a.target)
    ui = console.Console(thinking="off" if a.view == "none" else a.view)
    preflight(bench, a, ui)
    results = []
    for n in range(1, a.runs + 1):
        out, r = run_once(bench, a, ui, n)
        results.append(r)
        ui.banner("run %d of %d: %s" % (n, a.runs, "PASSED" if r["passed"] else "FAILED"),
                  "bgreen" if r["passed"] else "bred")
        ui.line("  %s" % out)
    passed = sum(r["passed"] for r in results)
    ui.line()
    ui.line("%d of %d runs passed" % (passed, len(results)))
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
