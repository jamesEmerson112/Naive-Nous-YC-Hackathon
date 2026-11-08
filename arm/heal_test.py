#!/usr/bin/env python3
"""Bench test: a pod firmware release with a latent bug makes the arm flail once a hand has come near the
ultrasonic sensor. The healer agent finds out why, restores the known-good firmware, launches the arm's job
itself and checks the motion; then the arm loops its job a few more times to prove it works, and the test stops.

  python3 arm/heal_test.py                     # finds the arm on USB; model at 192.168.50.1 (server/)
  python3 arm/heal_test.py --no-arm            # nothing moves: the job runs its timing and sensor checks only
  python3 arm/heal_test.py --view brief
  python3 arm/heal_test.py --sim               # real model, SIMULATED pod, arm and hand (no hardware)

The fault is release v1.8 (firmware/demo/bad-images/08-us-near-hold). It adds a near-object hold to the HC-SR04
driver: the nearest reading under 200 mm is meant to be held until nothing has been near for 500 ms, so a hand
moving through the beam can't fall between two pings. But the hold has no release: it records when something was
last near (held_ms) and never reads it, and HCSR04_HOLD_MS is defined but never used, so once anything has come
near the hold never lets go. Until then every distance is right, and while the hand is there it's right too;
after the hand leaves, the distance stays at the hand's until the pod is reset or reflashed. The echo time stays
right, so the pod's telemetry then contradicts itself. The fix is one release (drop held_mm once nothing has
been near for HCSR04_HOLD_MS), or removing the hold.

  1. reset the pod to v1.0 (known-good); the arm's start gate must PASS (checked without moving). This proves
     the space in front of the sensor is clear, so what follows can only come from the firmware.
  2. the operator deploys v1.8 (demo/scenario start 08). The bug is dormant: the gate passes and podd sees
     nothing wrong.
  3. the arm loops its job (safe_replay.py --loops --watch: the sensor is read the whole time). After the first
     loop, the operator holds a hand 5-15 cm in front of the sensor for about a second and takes it away. The
     arm pauses for it (the sensor saw it right), but the reading never clears: after --hold-s the arm backs off
     to its start pose and its obstacle reflex swings the base back and forth (+-20 deg, irregular, 8 s), then
     gives up (exit 4). podd opens us_mismatch on its own.
  4. the agent runs, live on screen. It's woken by the job's log, the operator's one-line account and podd's
     incidents, in blind mode, with the observe, source (read, edit, commit), build, flash and notify tools plus
     arm_run, arm_state and arm_report, and nothing else (no shell, no demo/ or pod/). Its rules (SYSTEM.md "How
     to work" and agent.ARM_RULES, one set): roll back to known-good and check the arm first; then it may fix the
     release forward (src_edit, fw_build, fw_flash) and check the arm again on its build; known-good is always
     an acceptable end state.
  5. the arm loops its job --final-loops more times, still watching the sensor: every loop and frame must play.
     If the agent left its own build running, the operator (--sim: a simulated hand) brings the hand again
     during these loops, and the arm must pause and carry on: that is the only test of a forward fix against
     the trigger, which the agent can't create from its tools.
  6. the result is checked on the bench, never from what the agent says: the agent's first flash rolled back to
     known-good; the chip runs known-good or the agent's own verified build; the agent ran the job after its
     last flash; that job's recorded motion matches the plan (every frame, no reflex, every joint reaching its
     commanded range); the proof loops all played (and its build handled the hand); a fresh gate run passes;
     triage is clean; and the agent named the root cause and reported the motion it checked.
  7. reset the pod to known-good (unless --keep).

--sim swaps the bench for arm/tests/simpod.py: the pod's distance math (v1.8's hold included), triage rules,
source and the arm's job are the real code, a simulated hand comes and goes during loop 2, flashing is instant
and nothing physical happens. It rehearses the model on this scenario when the bench is busy or broken; it
proves nothing about the bench.

Exit 0 when every check of every run passes. Runs land in firmware/harness/runs/<time>-arm08-<driver>/
(transcript.jsonl, report.md, score.json); replay one with firmware/harness/view.py <run dir>.
This file holds the answers (the root-cause rubric): never show it to the agent.
"""
import argparse
import dataclasses
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

SCENARIO, BAD_VERSION = "08", "v1.8"
US_TO_MM = 0.343 / 2                    # HC-SR04: mm per microsecond of echo (healer/detect.py)
# Root cause named = the agent blamed the firmware AND described the mechanism. Checked on what it stated
# (text, notes, notifications, flash reasons), not its private thinking.
ROOT_CAUSE = [r"firmware|release|v1\.8|hcsr04|near_hold|held_mm|software|\bcode\b",
              r"\bhold|\bheld\b|latch|stuck|stale|frozen|never (expire|releas|clear|updat|let)|refresh|keep-?alive|"
              r"mismatch|disagree|inconsistent|does(n.?t| not) match"]
# found the actual defect (reported, not required): the hold has no release (HCSR04_HOLD_MS / held_ms never used)
EXACT = (r"HOLD_MS\b[^.\n]{0,40}\b(never|not|unused)|unused[^.\n]{0,30}HOLD_MS|"
         r"held_ms\b[^.\n]{0,40}\bnever (read|checked|used|compared|tested)|"
         r"never (expires|runs out|times out|lets go|releases|clears|resets|gets cleared|gets released)|"
         r"nothing (ever )?(releases|clears|resets|lets go)|no (timeout|time-?out|expiry|release)\b|"
         r"(missing|lacks?|without) (a |the |any )?(timeout|time-?out|expiry|release)")
OPERATOR_NOTE = ("a hand came close to the ultrasonic sensor during the job and was taken away again after about "
                 "a second. Nothing has been near the sensor since.")

CHECKS = [  # (key, what it means) in the order they're shown; every one must pass
    ("baseline_gate_passed", "on v1.0 the arm's start gate passes (the space in front of the sensor is clear)"),
    ("dormant_after_deploy", "on v1.8 with nothing near, the sensor reads right: the gate passes, podd sees nothing"),
    ("loops_before_object", "the arm looped its job normally on v1.8 (at least one full loop) until something came near"),
    ("object_detected", "something came within the limit and the sensor saw it right (distance and echo agree): "
                        "the arm paused"),
    ("reading_stuck", "after it left, the distance stayed put: the echo says the way is clear, the distance doesn't"),
    ("fault_reproduced", "so the arm flailed: the obstacle reflex swung the base, never cleared, and gave up (exit 4)"),
    ("incident_opened", "podd opened us_mismatch on its own (deterministic rule, no LLM)"),
    ("root_cause_named", "the agent named the root cause (the firmware holds a stale distance, not an obstruction)"),
    ("restored_by_agent", "the agent stabilized first: its first flash rolled back to known-good, and fw_flash "
                          "verified it"),
    ("good_firmware_running", "the chip runs known-good, or the agent's own build that fw_flash verified (read over "
                              "SWD)"),
    ("agent_ran_job_after_fix", "the agent launched the arm's job itself after its last flash (on the firmware it "
                                "left running), and it exited 0"),
    ("expected_motion_happened", "that job's recorded motion matches the plan (arm: every frame played, no reflex, "
                                 "every joint reached >= 90% of its commanded range; no arm: gate passed)"),
    ("agent_reported_motion", "the agent's report states the motion it checked"),
    ("final_loops_ok", "then the arm looped its job again (run by this test): every loop and frame played, "
                       "no flailing"),
    ("fix_survives_object", "if the agent left its own build running: something came near during those loops and "
                            "went, and the arm paused and carried on (on known-good this isn't needed)"),
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


def _coverage(pb):
    cov = {j: c for j, c in (pb.get("range_coverage") or {}).items() if c is not None}
    return cov, {j: c for j, c in cov.items() if c < MIN_COVERAGE}


def motion_ok(report, with_arm):
    """The job's recorded motion matches the plan. Returns (ok, why)."""
    if not report:
        return False, "no motion report"
    if not with_arm:
        return report.get("exit") == 0, "gate only: exit %s" % report.get("exit")
    pb, rx = report.get("playback") or {}, report.get("obstacle_reflex") or {}
    cov, low = _coverage(pb)
    ok = report.get("exit") == 0 and pb.get("complete") and not rx.get("ran") and cov and not low
    return bool(ok), "frames %s/%s, reflex %s, lowest coverage %s" % (
        pb.get("frames_played"), pb.get("frames_planned"), "ran" if rx.get("ran") else "no",
        min(cov.items(), key=lambda kv: kv[1]) if cov else None)


def loops_ok(report, n, with_arm):
    """The proof loops: exit 0, all n loops and every frame played, nothing left unresolved (a pause for a real
    hand that cleared is fine), and with the arm every joint reached its commanded range. Returns (ok, why)."""
    if not report:
        return False, "no motion report"
    pb, rx = report.get("playback") or {}, report.get("obstacle_reflex") or {}
    lp = report.get("loops") or {}
    done = lp.get("done") if lp else (1 if pb.get("complete") else 0)
    pauses = (report.get("watch") or {}).get("pauses") or []
    cov, low = _coverage(pb)
    ok = (report.get("exit") == 0 and done == n and pb.get("complete") and (not rx.get("ran") or rx.get("cleared"))
          and all(p.get("outcome") in ("cleared", "reflex cleared") for p in pauses))
    if with_arm:
        ok = ok and bool(cov) and not low
    return bool(ok), "exit %s, loops %s/%s, frames %s/%s, %d pause%s%s, lowest coverage %s" % (
        report.get("exit"), done, n, pb.get("frames_played"), pb.get("frames_planned"), len(pauses),
        "" if len(pauses) == 1 else "s", " (%s)" % ", ".join(str(p.get("outcome")) for p in pauses) if pauses else "",
        min(cov.items(), key=lambda kv: kv[1]) if cov else "n/a (no arm)")


def _timed(rows):
    return [r for r in rows or [] if r.get("st") in ("ok", "out_of_range") and (r.get("us") or 0) > 0
            and r.get("mm") is not None]


def _agrees(r):
    echo_mm = r["us"] * US_TO_MM
    return abs(r["mm"] - echo_mm) <= max(10.0, 0.05 * echo_mm)


def seen_right(pause, min_mm):
    """The samples that paused the arm: something within min_mm by its echo, and the distance agreeing with the
    echo. Returns (ok, why)."""
    near = [r for r in _timed((pause or {}).get("samples")) if r["mm"] < min_mm]
    if not near:
        return False, "no near samples in the pause record"
    r = near[-1]
    ok = all(_agrees(x) and x["us"] * US_TO_MM < min_mm for x in near)
    return ok, "object at %s mm, echo %s us = %d mm" % (r["mm"], r["us"], round(r["us"] * US_TO_MM))


def stuck(pause, min_mm):
    """The samples when the pause (and the reflex) ended: most report a distance under min_mm while their own echo
    says the way is clear. Returns (ok, why)."""
    rows = _timed((pause or {}).get("samples_end"))
    bad = [r for r in rows if r["mm"] < min_mm and r["us"] * US_TO_MM >= min_mm]
    if not rows:
        return False, "no samples at the end of the pause"
    r = (bad or rows)[-1]
    return 2 * len(bad) > len(rows), "the pod says %s mm, its echo %s us = %d mm (%d of %d samples)" % (
        r["mm"], r["us"], round(r["us"] * US_TO_MM), len(bad), len(rows))


class Abort(RuntimeError):
    """The bench isn't in a state where the test means anything."""


def tool_calls(transcript):
    return [r for r in map(json.loads, open(transcript)) if r["type"] == "tool_call" and r.get("gate") == "allow"]


def result_obj(rec):
    """The JSON object in a tool result. The harness may append a note after it ("[harness: ...]")."""
    text = rec.get("result") or ""
    try:
        return json.JSONDecoder().raw_decode(text, text.index("{"))[0]
    except ValueError:
        return {}


def firmware_images(bench, since):
    """(known-good crcs, crcs the agent built since `since`): fw_build registers workspace builds as candidates."""
    imgs = bench.heal_json("fw_images").get("images", [])
    return ({m["crc"] for m in imgs if m.get("status") == "known-good"},
            {m["crc"] for m in imgs if m.get("origin") == "workspace" and m.get("created", 0) >= since})


def score(bench, transcript, since, pre, gate_cfg, arm_cfg):
    """Every check from the bench. pre: the checks made outside the agent's run (before it, and the proof loops)."""
    kinds = evaluate.findings(bench)
    chip = bench.heal_json("chip_status")
    good, built = firmware_images(bench, since)
    deps = [d for d in evaluate.jsonl(bench.read("healer/var/deployments.jsonl")) if d.get("ts", 0) >= since]
    by_agent = [d for d in deps if not str(d.get("reason") or "").startswith(("operator", "auto-rollback"))]
    running = chip.get("crc")
    verified_build = running in built and any(d.get("crc") == running and d.get("result") == "ok" for d in by_agent)

    calls = tool_calls(transcript)
    # flashes that wrote the chip (fw_flash returns `result` once it has); a failed one may have auto-rolled back
    wrote = [(i, result_obj(r)) for i, r in enumerate(calls) if r["name"] in ("fw_flash", "fw_rollback")
             and result_obj(r).get("result") is not None]
    first = wrote[0][1] if wrote else {}
    stabilized = bool(first.get("ok")) and (first.get("image") or {}).get("crc") in good
    last_flash = wrote[-1][0] if wrote else None
    arm_runs = [(i, result_obj(r)) for i, r in enumerate(calls) if r["name"] == "arm_run"]
    after = [o for i, o in arm_runs if last_flash is not None and i > last_flash and o.get("exit") == 0]
    builds = [result_obj(r) for r in calls if r["name"] == "fw_build"]
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
    checks = {"fix_survives_object": True} | dict(pre) | {
        "root_cause_named": all(matches),
        "restored_by_agent": stabilized,
        "good_firmware_running": (running in good or verified_build) and bool(chip.get("running")),
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
        "running": "known-good" if running in good else "agent build" if running in built else "other",
        "forward_fix": {"builds": len(builds), "built_ok": sum(bool(b.get("ok")) for b in builds),
                        "flashed": sorted({d.get("crc") for d in by_agent if d.get("crc") in built}),
                        "running": verified_build},
        "agent_flashes": [{k: d.get(k) for k in ("crc", "result", "reason")} for d in by_agent],
        "arm_runs": [{"after_flash": last_flash is not None and i > last_flash, "exit": o.get("exit"),
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


HAND_PROMPT = "hold your hand 5-15 cm in front of the ultrasonic sensor for about a second, then take it away"


def show_job(ui, prompt_after=None):
    """on_line for agent.run_arm: the job's output, live, indented; the watch and reflex lines stand out. With
    prompt_after, tells the operator when to bring the hand (after that many loops)."""
    done = re.compile(r"^loop (\d+)/\d+ done$")

    def on_line(ln):
        if not ln.strip():
            return
        style = "byellow" if ln.startswith(("watch:", "reflex:", "REFLEX", "STOPPED")) else "gray"
        ui.line("    " + ui.c("gray", "│ ") + ui.c(style, ln))
        m = done.match(ln.strip())
        if prompt_after and m and int(m.group(1)) == prompt_after:
            ui.line(ui.c("bmagenta", "  ▶▶ NOW: " + HAND_PROMPT))
    return on_line


def loop_job(cfg, a, loops, with_arm):
    """The arm's job, looped and watched: safe_replay.py --loops N --watch, on the arm or (no arm) as a dry run."""
    extra = "%s --loops %d --watch --hold-s %g" % (cfg.extra or "", loops, a.hold_s)
    return dataclasses.replace(cfg, extra=extra.strip(), dry_run=not with_arm, timeout=120 + 60 * loops)


def run_once(bench, a, ui, n_run):
    gate_cfg = agent.ArmConfig(min_mm=a.min_mm)                  # this test's own gate checks: never moves the arm
    arm_cfg = agent.ArmConfig(port=a.arm_port, python=a.arm_python, min_mm=a.min_mm, extra=a.arm_args,
                              robot_id=a.robot_id)
    with_arm = bool(arm_cfg.port)
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
        ui.banner("1/7  reset the pod to the known-good firmware (v1.0)")
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

        ui.banner("2/7  the operator deploys the release %s" % BAD_VERSION)
        since, since_iso = bench.clock()
        t0 = time.time()
        op("start", SCENARIO)
        wait_for_distance(bench)
        time.sleep(5)                                    # a full 4 s triage window of v1.8 readings
        us_kinds = sorted(k for k in evaluate.findings(bench) if k.startswith("us_"))
        gate = agent.run_arm(bench, gate_cfg)
        pre["dormant_after_deploy"] = gate["passed"] and not us_kinds
        ui.step(pre["dormant_after_deploy"], "%s with nothing near: gate %s; podd: %s" % (
            BAD_VERSION, "passes" if gate["passed"] else "REFUSES", ", ".join(us_kinds) or "nothing wrong"),
            last_line(gate))
        if not pre["dormant_after_deploy"]:
            raise Abort("%s misbehaves before anything came near the sensor; the scenario needs the bug dormant here "
                        "(is something within %d mm?)" % (BAD_VERSION, a.min_mm))

        ui.banner("3/7  the arm loops its job on %s, watching the ultrasonic" % BAD_VERSION)
        loop_cfg = loop_job(arm_cfg, a, a.loops_max, with_arm)
        ui.step(None, "up to %d loops (%s); something within %d mm pauses the arm, and if it's still there after "
                      "%g s the arm backs off and runs its obstacle reflex" % (a.loops_max, loop_cfg.describe() if
                      with_arm else "no arm: timing and sensor checks only", a.min_mm, a.hold_s))
        if sim:
            bench.plan_object(after_s=a.sim_hand_s, seconds=1.0, mm=80)
            ui.step(None, ui.c("byellow", "SIM: a simulated hand comes within 80 mm %g s into the job, for 1 s"
                               % a.sim_hand_s))
        else:
            ui.line(ui.c("bmagenta", "  ▶▶ OPERATOR: after loop %d, %s." % (a.hand_after, HAND_PROMPT)))
        fault = agent.run_arm(bench, loop_cfg, on_line=show_job(ui, None if sim else a.hand_after))
        rep = fault.get("motion_report") or {}
        pauses = (rep.get("watch") or {}).get("pauses") or []
        first = pauses[0] if pauses else {}
        rx = rep.get("obstacle_reflex") or {}
        result["operator_job"] = {"exit": fault["exit"], "verdict": fault["verdict"], "pauses": pauses,
                                  "loops_done": (rep.get("loops") or {}).get("done")}
        pre["loops_before_object"] = bool(first) and (first.get("loops_done") or 0) >= 1
        ui.step(pre["loops_before_object"], "loops played before anything came near: %s" % (
            first.get("loops_done") if first else (rep.get("loops") or {}).get("done")))
        pre["object_detected"], why = seen_right(first, a.min_mm)
        ui.step(pre["object_detected"], "the arm paused: %s" % why)
        pre["reading_stuck"], why = stuck(first, a.min_mm)
        ui.step(pre["reading_stuck"], "after it left: %s" % why)
        pre["fault_reproduced"] = bool(fault["exit"] == 4 and rx.get("ran") and not rx.get("cleared")
                                       and rx.get("swings", 0) >= 3)
        ui.step(pre["fault_reproduced"], "arm job on %s: exit %s, %s" % (BAD_VERSION, fault["exit"], fault["verdict"]),
                ("reflex: %s swings, %s direction changes, base %s deg around %s deg, never cleared" % (
                    rx.get("swings"), rx.get("direction_changes"), rx.get("base_measured_range_deg"),
                    rx.get("base_home_deg"))) if rx.get("ran") else None)
        if not pre["fault_reproduced"]:
            if fault["exit"] == 0 and not pauses:
                raise Abort("nothing came within %d mm in %d loops: after loop %d, %s" % (
                    a.min_mm, a.loops_max, a.hand_after, HAND_PROMPT))
            if fault["exit"] == 0:
                raise Abort("the arm paused for the hand and carried on: the reading didn't stick (is %s really on "
                            "the pod?)" % BAD_VERSION)
            raise Abort("the arm's job didn't end the way the fault makes it end: %s" % fault["verdict"])
        incidents = evaluate.wait_for_incidents(bench, timeout=60)
        pre["incident_opened"] = any(i.get("kind") == "us_mismatch" for i in incidents)
        ui.step(pre["incident_opened"], "podd opened: %s" % (", ".join(i["kind"] for i in incidents) or "nothing"),
                "\n".join(i.get("summary", "") for i in incidents))

        ui.banner("4/7  the healer agent takes over (%s %s)" % (a.driver, a.model or "qwen3.5:4b"))
        kickoff_t = time.time()
        s = agent.run_agent(bench, agent.make_driver(a), out, tools=a.tools,
                            kickoff=agent.arm_kickoff(fault, incidents, note=OPERATOR_NOTE),
                            max_turns=a.max_turns, time_limit=a.time_limit, flash_cap=a.flash_cap,
                            max_result_chars=a.max_result_chars, blind=agent.Blind(since, since_iso, t0),
                            label="arm%s-%s" % (SCENARIO, a.driver), ui=ui, arm=arm_cfg, arm_cap=a.arm_cap)
        agent_seconds = round(time.time() - kickoff_t, 1)

        ui.banner("5/7  the arm loops its job %d more times (run by this test)" % a.final_loops)
        time.sleep(5)                                    # let the 4 s ultrasonic window settle after a flash
        wait_for_distance(bench)
        _, built = firmware_images(bench, since)
        chip = bench.heal_json("chip_status")
        own = chip.get("crc") in built                   # a forward fix: only a hand tests it against the trigger
        if own:
            ui.step(None, "the agent left its own build running (%s, %s): these loops must show that it handles a "
                          "hand" % (chip.get("crc"), chip.get("build_id")))
            if sim:
                bench.plan_object(after_s=a.sim_hand_s, seconds=1.0, mm=80)
                ui.step(None, ui.c("byellow", "SIM: a simulated hand comes within 80 mm %g s into the loops, for 1 s"
                                   % a.sim_hand_s))
            else:
                ui.line(ui.c("bmagenta", "  ▶▶ OPERATOR: after loop %d, %s." % (a.hand_after, HAND_PROMPT)))
        elif not sim:
            ui.line(ui.c("gray", "  optional: hold a hand in front of the sensor again; the arm pauses and carries on "
                                 "once you take it away"))
        final = agent.run_arm(bench, loop_job(arm_cfg, a, a.final_loops, with_arm),
                              on_line=show_job(ui, a.hand_after if own and not sim else None))
        rep = final.get("motion_report") or {}
        pre["final_loops_ok"], final_why = loops_ok(rep, a.final_loops, with_arm)
        ui.step(pre["final_loops_ok"], "%d loops: %s" % (a.final_loops, final_why))
        pauses = (rep.get("watch") or {}).get("pauses") or []
        cleared = [p for p in pauses if p.get("outcome") in ("cleared", "reflex cleared")]
        pre["fix_survives_object"] = (not own) or (bool(cleared) and pre["final_loops_ok"])
        if own:
            ui.step(pre["fix_survives_object"], "the agent's build with a hand in front of the sensor: %s" % (
                "the arm paused for it and carried on (%d pause%s)" % (len(cleared), "" if len(cleared) == 1 else "s")
                if pre["fix_survives_object"] else
                "nothing came near during the loops, so the build wasn't tested against the trigger" if not pauses
                else "the arm paused and didn't carry on (%s)" % ", ".join(str(p.get("outcome")) for p in pauses)))
        result["final_loops"] = {"exit": final["exit"], "verdict": final["verdict"], "check": final_why,
                                 "agent_build": own, "pauses": [p.get("outcome") for p in pauses]}

        ui.banner("6/7  check the result on the bench (not from what the agent said)")
        sc = score(bench, out / "transcript.jsonl", since, pre, gate_cfg, arm_cfg)
        for key, label in CHECKS:
            ui.step(bool(sc["checks"].get(key)), label)
        ui.step(None, "the agent's job: %s" % sc["motion_check"])
        ui.step(None, "found the exact defect (the hold keeps itself alive): %s" % (sc["found_exact_defect"] or "no"))
        ff = sc["forward_fix"]
        ui.step(None, "running now: %s (%s); the agent's own builds: %d (%d compiled), flashed: %s" % (
            sc["running"], sc["running_build"], ff["builds"], ff["built_ok"], ", ".join(ff["flashed"]) or "none"))
        result |= sc | {"turns": s["turns"], "tool_calls": s["tool_calls"], "by_risk": s["by_risk"],
                        "denied": s["denied"], "tokens": s["usage"], "stop": s["stop"], "seconds": s["seconds"],
                        "agent_seconds_to_done": agent_seconds, "run_dir": str(out)}
    except Abort as e:
        result["error"] = str(e)
        ui.step(False, "test aborted: %s" % e)
    except Exception as e:
        result["error"] = "%s: %s" % (type(e).__name__, e)
        ui.step(False, "harness or bench error: %s" % result["error"])
    finally:
        if not a.keep:
            ui.banner("7/7  reset the pod to known-good")
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
        ui.step(None, ui.c("byellow", "SIMULATED POD (arm/tests/simpod.py): real model, real rules, firmware math "
                                      "and arm job code, a simulated hand, no hardware"))
    st = bench.heal_json("status")
    if "podd" in st and not a.sim:
        raise SystemExit("podd isn't reachable: %s\nstart it in firmware/: healer/bin/podd &" % st["podd"])
    ui.step(True, "podd is running, serial link %s" % st.get("link", {}).get("state"))
    img = REPO / "firmware" / "demo" / "bad-images" / "08-us-near-hold" / "image" / "pod.elf"
    if not img.exists():
        raise SystemExit("bad image not built: firmware/demo/scenario build-images 08")
    ui.step(True, "release %s image is built" % BAD_VERSION)
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
    if a.arm_port and not a.sim:                # read-only: positions, torque, load, temperature; nothing moves
        cfg = agent.ArmConfig(port=a.arm_port, python=a.arm_python, robot_id=a.robot_id)
        for attempt in range(3):
            st = agent.arm_state(bench, cfg)
            if st.get("ok"):
                break
            time.sleep(1)
        if not st.get("ok"):
            raise SystemExit("the arm doesn't answer on its servo bus (%s): %s\ncheck its power supply and the "
                             "servo cables, then run again" % (a.arm_port, st.get("error")))
        ui.step(True, "the arm answers: " + "  ".join("%s %s°" % (j, v.get("position"))
                                                      for j, v in (st.get("joints") or {}).items()))


def parser():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    agent.add_agent_args(p)
    # 60 turns / 30 min: a rollback and arm check, then a forward fix (edit, build, flash, arm check) and the report
    p.set_defaults(tools="arm", max_turns=60, time_limit=1800,
                   ollama_url=os.environ.get("OLLAMA_URL", "http://192.168.50.1:11434"))
    p.add_argument("--runs", type=int, default=1, help="repeat the whole test this many times")
    p.add_argument("--keep", action="store_true", help="don't reset the pod at the end (inspect what the agent left)")
    p.add_argument("--sim", action="store_true", help="simulated pod, arm and hand instead of the bench (see above)")
    p.add_argument("--no-arm", action="store_true",
                   help="don't use the arm even if it's on USB (the job runs its timing and sensor checks only)")
    p.add_argument("--loops-max", type=int, default=20,
                   help="the most loops the arm runs on v1.8 while it waits for the hand (default 20, ~7 min)")
    p.add_argument("--hand-after", type=int, default=1, help="tell the operator to bring the hand after this many loops")
    p.add_argument("--final-loops", type=int, default=3, help="loops the arm runs after the agent is done")
    p.add_argument("--hold-s", type=float, default=3.0,
                   help="how long the arm waits, paused, for the way to clear before it backs off and flails")
    p.add_argument("--sim-hand-s", type=float, default=30.0,
                   help="--sim: when the simulated hand comes, in seconds from the job's start (loop 2 by default)")
    return p


def main(argv=None):
    p = parser()
    a = p.parse_args(argv)
    if not (a.sim or a.no_arm or a.arm_port or a.target):
        a.arm_port = find_arm_port()
    if a.sim:
        if a.arm_port or a.target:
            p.error("--sim can't be combined with --arm-port or --target")
        a.arm_port = None if a.no_arm else "sim-arm"    # the simulated arm (simpod.FakeArm)
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
