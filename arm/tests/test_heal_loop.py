"""The whole heal loop offline: the real agent loop, gate, arm tools (arm_run, arm_state, arm_report), scorer,
live view and heal_test.py's run_once, with a scripted model in place of qwen, a simulated pod in place of the
bench, a simulated hand, and a simulated arm that follows its commands (simpod.FakeArm)."""
import io
import json
import re
import time

from simpod import SimPod
import agent
import console
import heal_test  # noqa: E402  (arm/heal_test.py; conftest put arm/ on sys.path)
import view


class ScriptedDriver:
    name, model, endpoint = "scripted", "none", "test"

    def __init__(self, turns):
        self.turns, self.results = list(turns), []

    def start(self, system, tools, kickoff):
        self.system, self.tools, self.kickoff = system, [t["name"] for t in tools], kickoff

    def step(self):
        return self.turns.pop(0)

    def add_results(self, results):
        self.results.append(results)


def turn(*calls, text="", thinking=""):
    return agent.Turn(text=text, thinking=thinking, calls=[("c%d" % i, n, a) for i, (n, a) in enumerate(calls)],
                      stop="tool_use" if calls else "end_turn", usage={"input_tokens": 10, "output_tokens": 5})


def latched_pod(**kw):
    """v1.8 after a hand came within 80 mm for a second and left: the distance is stuck at the hand's."""
    pod = SimPod("v1.8", **kw)
    pod.run_until(time.time())
    pod.objects.append((pod.t, pod.t + 1.0, 80))
    pod.run_until(pod.t + 3.0)
    return pod


REPORT = ("Root cause: release v1.8 added near_hold() to hcsr04.c. Its keep-alive tests held_mm instead of the new "
          "reading, so once a hand came within 250 mm the hold refreshes itself on every ping and never expires: the "
          "distance is stuck at 78 mm while the echo says ~900 mm. Not an obstruction. Fix: rolled back to known-good "
          "v1.0. Verified: arm_run exit 0, no reflex, 663 of 663 frames played, range coverage 1.0 on every joint.")

GOOD_AGENT = [
    turn(("triage", {}), thinking="The arm paused for the hand, then swung back and forth and gave up. What does podd see?"),
    turn(("arm_report", {}), thinking="What did that job do?"),
    turn(("telemetry_recent", {"kind": "D", "limit": 5}),
         thinking="us_mismatch: echo ~5250 us should be ~900 mm, but mm stays at 78. Look at raw samples."),
    turn(("src_log", {}), ("src_diff", {"rev_a": "v1.0"}), thinking="Echo is long, so nothing is close. What changed?"),
    turn(("note", {"text": "v1.8 near_hold: the keep-alive tests held_mm, not mm, so it refreshes itself every ping"}),
         ("fw_rollback", {"reason": "v1.8 holds a near distance forever once something came close; restore known-good"})),
    turn(("arm_run", {}), thinking="Launch the job and check the motion."),
    turn(("arm_state", {}), thinking="Back at rest?"),
    turn(text=REPORT),
]

WRONG_AGENT = [
    turn(("triage", {})),
    turn(("notify_human", {"summary": "Something is blocking the ultrasonic sensor", "actions": "clear it"})),
    turn(text="Something is in front of the sensor; asked a person to remove it."),
]

PRE = {"baseline_gate_passed": True, "dormant_after_deploy": True, "loops_before_object": True,
       "object_detected": True, "reading_stuck": True, "fault_reproduced": True, "incident_opened": True,
       "final_loops_ok": True}


def arm_cfg(tmp_path, with_arm=True):
    return agent.ArmConfig(port="sim-arm" if with_arm else None, runs_dir=str(tmp_path / "runs"))


def run(pod, turns, tmp_path, ui=None, cfg=None):
    cfg = cfg or arm_cfg(tmp_path)
    job = agent.run_arm(pod, cfg)                       # a job on whatever the pod runs now
    kickoff = agent.arm_kickoff(job, agent.open_incidents(pod), note=heal_test.OPERATOR_NOTE)
    d = ScriptedDriver(turns)
    s = agent.run_agent(pod, d, tmp_path / "agent", tools="arm", kickoff=kickoff, arm=cfg, ui=ui)
    return d, s, cfg


def test_the_bad_image_makes_the_arm_swing_and_the_good_one_plays_the_wave(tmp_path):
    pod, cfg = SimPod("v1.7"), arm_cfg(tmp_path)
    bad = agent.run_arm(pod, cfg)
    rx = bad["motion_report"]["obstacle_reflex"]
    assert bad["exit"] == 4 and rx["ran"] and not rx["cleared"] and rx["swings"] >= 5 and rx["direction_changes"] >= 3
    assert bad["motion_report"]["playback"]["frames_played"] == 0
    assert "reflex: object at" in bad["log"] and "REFLEX GAVE UP" in bad["log"]
    pod.firmware = "v1.0"
    good = agent.run_arm(pod, cfg)
    pb = good["motion_report"]["playback"]
    assert good["exit"] == 0 and not good["motion_report"]["obstacle_reflex"]["ran"]
    assert pb["complete"] and pb["frames_played"] == pb["frames_planned"] == 663
    assert all(c == 1.0 for c in pb["range_coverage"].values())
    assert heal_test.motion_ok(good["motion_report"], True)[0] and not heal_test.motion_ok(bad["motion_report"], True)[0]


def test_good_agent_fixes_launches_the_job_and_verifies_the_motion(tmp_path):
    pod = latched_pod()
    d, s, cfg = run(pod, GOOD_AGENT, tmp_path)
    assert "did not go as expected (exit 4" in d.kickoff and "reflex: object at" in d.kickoff
    assert "The operator adds: a hand came close" in d.kickoff
    assert {"arm_run", "arm_state", "arm_report", "src_edit", "fw_build", "src_commit"} <= set(d.tools)
    assert "## The robot arm" in d.system and "reaches the commanded range on every joint" in d.system
    assert "Stabilize: fw_rollback to known-good" in d.system and "In this run that is the fix" not in d.system
    assert s["stop"] == "end_turn" and s["denied"] == [] and s["by_risk"]["arm"] == 1 and s["flashes_allowed"] == 1
    report_before = json.loads(d.results[1][0][2])
    assert report_before["exit"] == 4 and report_before["obstacle_reflex"]["swings"] >= 5
    samples = json.loads(d.results[2][0][2])["rows"]
    assert all(r["mm"] == samples[0]["mm"] < 100 and r["us"] > 5000 for r in samples)      # stuck mm, long echo
    diff = json.loads(d.results[3][1][2])["diff"]
    assert "+    if (held_mm < HCSR04_HOLD_NEAR_MM)" in diff
    job = json.loads(d.results[5][0][2])
    assert job["exit"] == 0 and job["motion_report"]["playback"]["complete"]
    state = json.loads(d.results[6][0][2])
    assert state["ok"] and state["joints"]["shoulder_pan"]["position"] == 18.0     # back at rest
    sc = heal_test.score(pod, tmp_path / "agent" / "transcript.jsonl", 0, PRE, agent.ArmConfig(), cfg)
    assert sc["passed"], sc["checks"]
    assert sc["found_exact_defect"] and sc["arm_runs"] == [{"after_flash": True, "exit": 0, "verdict": job["verdict"]}]


def test_wrong_diagnosis_fails_on_bench_evidence(tmp_path):
    pod = latched_pod()
    _, _, cfg = run(pod, WRONG_AGENT, tmp_path)
    sc = heal_test.score(pod, tmp_path / "agent" / "transcript.jsonl", 0, PRE, agent.ArmConfig(), cfg)
    c = sc["checks"]
    assert not sc["passed"]
    assert not (c["restored_by_agent"] or c["good_firmware_running"] or c["gate_passes_now"] or c["triage_clean"])
    assert not (c["agent_ran_job_after_fix"] or c["expected_motion_happened"])


def test_rebooting_the_pod_clears_the_reading_but_is_not_a_fix(tmp_path):
    """pod_cmd reset clears v1.8's hold until the next hand: the gate passes and triage is clean, but the bad
    release still runs, so the bench checks fail."""
    turns = [turn(("pod_cmd", {"command": "reset"})), turn(("arm_run", {})),
             turn(text="Reset the pod; the stale firmware reading is gone and the arm moved: 663 frames.")]
    pod = latched_pod()
    _, _, cfg = run(pod, turns, tmp_path)
    sc = heal_test.score(pod, tmp_path / "agent" / "transcript.jsonl", 0, PRE, agent.ArmConfig(), cfg)
    c = sc["checks"]
    assert c["gate_passes_now"] and c["triage_clean"] and c["root_cause_named"]
    assert not (c["restored_by_agent"] or c["good_firmware_running"] or c["agent_ran_job_after_fix"]) and not sc["passed"]


def test_a_job_before_the_flash_or_a_silent_report_does_not_count(tmp_path):
    turns = [turn(("arm_run", {})), turn(("fw_rollback", {})), turn(text="Rolled back the firmware hold bug.")]
    pod = latched_pod()
    _, _, cfg = run(pod, turns, tmp_path)
    sc = heal_test.score(pod, tmp_path / "agent" / "transcript.jsonl", 0, PRE, agent.ArmConfig(), cfg)
    assert sc["checks"]["good_firmware_running"] and not sc["checks"]["agent_ran_job_after_fix"]
    assert not sc["checks"]["expected_motion_happened"] and not sc["checks"]["agent_reported_motion"]


def test_arm_runs_are_capped_and_reads_are_not(tmp_path):
    turns = [turn(*[("arm_run", {})] * 6), turn(("arm_state", {}), ("arm_report", {}), ("arm_report", {"run_id": "../x"})),
             turn(text="done")]
    d = ScriptedDriver(turns)
    s = agent.run_agent(SimPod("v1.0"), d, tmp_path, tools="arm", kickoff="go", arm=arm_cfg(tmp_path), arm_cap=4)
    assert s["by_risk"]["arm"] == 4 and s["by_risk"]["read"] == 2 and s["forbidden"] == 3
    assert "budget" in s["denied"][0]["reason"] and "run_id" in s["denied"][-1]["reason"]


def test_without_an_arm_the_tools_say_so_and_the_job_is_gate_only(tmp_path):
    pod, cfg = SimPod("v1.0"), arm_cfg(tmp_path, with_arm=False)
    assert agent.arm_state(pod, cfg) == {"ok": False, "error": "no arm attached in this run (start gate only)"}
    job = agent.run_arm(pod, cfg)
    assert job["exit"] == 0 and "check only" in job["verdict"] and pod.arm.moves == 0
    assert heal_test.motion_ok(job["motion_report"], False)[0]


def test_live_view_and_replay_show_the_reasoning_and_the_motion(tmp_path):
    buf = io.StringIO()
    run(latched_pod(), GOOD_AGENT, tmp_path, ui=console.Console(out=buf, color=False))
    live = buf.getvalue()
    for want in ("what woke the agent", "us_mismatch", "Echo is long, so nothing is close", "▶ fw_rollback",
                 "flash ok · now running v1.0 BAADB704", "ARM JOB OK", "playback: 663 of 663 frames",
                 "range reached: shoulder_pan 1.0", "reflex:", "never cleared", "+static uint32_t held_mm = UINT32_MAX;",
                 "Root cause: release v1.8", "run finished · stop: end_turn"):
        assert want in live, want
    buf2 = io.StringIO()
    view.replay(tmp_path / "agent" / "transcript.jsonl", console.Console(out=buf2, color=False))
    for want in ("Echo is long, so nothing is close", "ARM JOB OK", "flash ok · now running v1.0"):
        assert want in buf2.getvalue(), want


def test_the_agent_gets_no_answer_in_what_wakes_it(tmp_path):
    """The kickoff (the operator's looped job, its log, the operator's note, podd's incidents) states what happened,
    never the mechanism."""
    pod = SimPod("v1.0", runs_dir=tmp_path / "runs")
    pod.scenario("start", "08")
    pod.plan_object(30, 1.0, 80)
    job = agent.run_arm(pod, heal_test.loop_job(arm_cfg(tmp_path), heal_test.parser().parse_args([]), 5, True))
    kickoff = agent.arm_kickoff(job, agent.open_incidents(pod), note=heal_test.OPERATOR_NOTE)
    assert job["exit"] == 4 and "us_mismatch" in kickoff
    assert not re.search(r"held_mm|near_hold|keep-?alive|refresh|latch|never (expire|lets go)|stale|\bstuck|v1\.8|"
                         r"250 ?mm|500 ?ms", kickoff, re.I), kickoff


def _args(*extra):
    return heal_test.parser().parse_args(["--tools", "arm", *extra])


def _patch(monkeypatch, tmp_path, turns=GOOD_AGENT):
    monkeypatch.setattr(agent, "RUNS", tmp_path)
    monkeypatch.setattr(agent, "DEBOUNCE_S", 0)
    monkeypatch.setattr(heal_test.time, "sleep", lambda s: None)
    monkeypatch.setattr(agent, "make_driver", lambda a: ScriptedDriver(turns))


def test_heal_test_run_once_end_to_end(tmp_path, monkeypatch):
    """arm/heal_test.py's full sequence on the simulated bench, arm and hand: reset, baseline gate, deploy v1.8 (dormant),
    the arm loops until a hand comes and goes, the reading sticks, the arm flails, incident, agent (fix, own job,
    motion check), three proof loops, bench checks, reset."""
    _patch(monkeypatch, tmp_path)
    pod, buf = SimPod("v1.0", runs_dir=tmp_path / "runs"), io.StringIO()
    out, r = heal_test.run_once(pod, _args("--sim", "--arm-port", "sim-arm"), console.Console(out=buf, color=False), 1)
    assert r["passed"], (r.get("error"), r.get("checks"))
    assert [w[0] for w in pod.ops] == ["reset", "start", "reset"] and pod.ops[1] == ("start", "08")
    assert json.loads((out / "score.json").read_text())["passed"]
    op = r["operator_job"]
    assert op["exit"] == 4 and op["loops_done"] == 1 and op["pauses"][0]["outcome"] == "reflex gave up"
    assert r["final_loops"]["exit"] == 0 and "loops 3/3" in r["final_loops"]["check"]
    text = buf.getvalue()
    assert text.count("✔ PASS") >= len(heal_test.CHECKS) and "✘ FAIL" not in text
    for want in ("loop 1/20 done", "watch: object at", "holding still", "REFLEX GAVE UP", "arm job on v1.8: exit 4",
                 "the arm paused: object at", "after it left: the pod says", "loop 3/3 done", "3 loops: exit 0, loops 3/3"):
        assert want in text, want


def test_heal_test_aborts_when_the_sensor_is_really_blocked(tmp_path, monkeypatch):
    """If the gate refuses on known-good firmware too, the test must stop: a pass would prove nothing."""
    _patch(monkeypatch, tmp_path)
    pod = SimPod("v1.0", distance_mm=120, runs_dir=tmp_path / "runs")     # a real object at 12 cm
    out, r = heal_test.run_once(pod, _args("--sim", "--arm-port", "sim-arm"),
                                console.Console(out=io.StringIO(), color=False), 1)
    assert not r["passed"] and "refuses on known-good" in r["error"]
    assert [w[0] for w in pod.ops] == ["reset", "reset"] and pod.arm.moves == 0


def test_heal_test_aborts_when_no_hand_comes(tmp_path, monkeypatch):
    """Nobody brings a hand: the arm loops cleanly on v1.8 (the bug stays dormant), and the test stops before the agent."""
    _patch(monkeypatch, tmp_path)
    pod, buf = SimPod("v1.0", runs_dir=tmp_path / "runs"), io.StringIO()
    out, r = heal_test.run_once(pod, _args("--arm-port", "sim-arm", "--loops-max", "2"),
                                console.Console(out=buf, color=False), 1)
    assert not r["passed"] and "nothing came within 200 mm in 2 loops" in r["error"]
    assert r["operator_job"]["exit"] == 0 and r["operator_job"]["loops_done"] == 2
    assert "NOW: hold your hand" in buf.getvalue() and [w[0] for w in pod.ops] == ["reset", "start", "reset"]


def test_watch_kickoff_names_the_arm_goal_only_when_the_arm_is_there(tmp_path):
    """agent.py watch wakes on podd's incidents alone. With the arm tools, its kickoff says what done means (run the
    arm's job and check the motion), still nothing about the cause; without them it's unchanged."""
    pod = latched_pod()
    incidents = agent.open_incidents(pod)
    plain, with_arm = agent.kickoff_text(incidents), agent.kickoff_text(incidents, arm=arm_cfg(tmp_path))
    assert plain.endswith("Investigate, fix what can be fixed, and report.") and "arm_run" not in plain
    assert "us_mismatch" in with_arm and "arm_run" in with_arm and "moves as expected" in with_arm
    assert not re.search(r"held_mm|near_hold|keep-?alive|refresh|latch|stale|\bstuck|v1\.8|rollback|roll back",
                         with_arm, re.I), with_arm
    d = ScriptedDriver([turn(text="done")])            # run_agent's own default kickoff follows the same rule
    agent.run_agent(pod, d, tmp_path / "agent", tools="arm", arm=arm_cfg(tmp_path))
    assert "arm_run" in d.kickoff and "us_mismatch" in d.kickoff


# ---------------------------------------------------------------------------------- forward fixes (Nov 8)
FIX_EDIT = {"path": "src/hcsr04.c", "old": "    if (held_mm < HCSR04_HOLD_NEAR_MM)", "new": "    if (mm < HCSR04_HOLD_NEAR_MM)"}
WRONG_EDIT = {"path": "src/hcsr04.h", "old": "#define HCSR04_HOLD_MS        500u", "new": "#define HCSR04_HOLD_MS        300u"}
FORWARD_REPORT = ("Root cause: release v1.8's near_hold() keep-alive tests held_mm instead of the new reading, so the "
                  "hold refreshes itself on every ping and never expires once a hand came near: firmware, not an "
                  "obstruction. Rolled back to known-good first and the arm ran 663 of 663 frames. Then fixed it "
                  "forward (the keep-alive now tests mm), built and flashed it: triage clean, and arm_run played 663 of "
                  "663 frames, no reflex, range coverage 1.0 on every joint. Running: my build. A person should "
                  "re-test with a hand in front of the sensor.")


def forward_agent(edit=FIX_EDIT, rollback=True, recheck=True):
    turns = [turn(("triage", {}), thinking="us_mismatch: the echo says clear, the distance says near."),
             turn(("src_diff", {"rev_a": "v1.0", "rev_b": "HEAD"}))]
    if rollback:
        turns += [turn(("fw_rollback", {"reason": "v1.8 reports a distance its own echo contradicts"})),
                  turn(("arm_run", {}), thinking="Stable on known-good; check the arm.")]
    turns += [turn(("src_edit", edit)), turn(("fw_build", {})),
              turn(("fw_flash", {"image": "latest", "reason": "forward fix: the keep-alive tests the new reading"}))]
    if recheck:
        turns += [turn(("triage", {}), ("arm_run", {}), thinking="Check the arm on my build.")]
    return turns + [turn(("src_commit", {"message": "hcsr04: the near hold's keep-alive tests the new reading"})),
                    turn(text=FORWARD_REPORT)]


def test_forward_fix_after_the_rollback_passes_and_runs_the_agents_build(tmp_path):
    pod = latched_pod()
    d, s, cfg = run(pod, forward_agent(), tmp_path)
    assert s["stop"] == "end_turn" and s["denied"] == [] and s["flashes_allowed"] == 2 and s["by_risk"]["arm"] == 2
    build = json.loads(d.results[5][0][2])
    assert build["ok"] and build["image"]["origin"] == "workspace"
    assert pod.heal_json("chip_status")["crc"] == build["image"]["crc"]
    sc = heal_test.score(pod, tmp_path / "agent" / "transcript.jsonl", 0, PRE, agent.ArmConfig(), cfg)
    assert sc["passed"], sc["checks"]
    assert sc["running"] == "agent build" and sc["forward_fix"] == {"builds": 1, "built_ok": 1,
                                                                    "flashed": [build["image"]["crc"]], "running": True}
    assert [r["after_flash"] for r in sc["arm_runs"]] == [False, True]      # only the job on its last flash counts
    pod.run_until(time.time())                     # and the build really handles a hand: the reading lets go
    pod.objects.append((pod.t, pod.t + 1.0, 80))
    pod.run_until(pod.t + 3.0)
    assert all(r["mm"] > 800 for r in pod.d_rows(5))


def test_the_gate_makes_it_stabilize_first(tmp_path):
    """Straight to a forward fix on the bad release: the edit, the build, the flash of the build and the commit are refused (not
    counted) until it has rolled back to known-good and the arm check passed on it. qwen3.5:4b did exactly this in the
    first --sim rehearsal of the forward-fix rules (Nov 8, 03:14)."""
    pod = latched_pod()
    d, s, cfg = run(pod, forward_agent(rollback=False), tmp_path)
    assert s["out_of_order"] == 4 and s["flashes_allowed"] == 0 and "src_edit" not in [c[0] for c in pod.calls]
    assert "stabilize first" in d.results[2][0][2] and "fw_rollback" in d.results[2][0][2]
    sc = heal_test.score(pod, tmp_path / "agent" / "transcript.jsonl", 0, PRE, agent.ArmConfig(), cfg)
    assert not sc["checks"]["restored_by_agent"] and not sc["checks"]["good_firmware_running"] and not sc["passed"]


def test_after_the_rollback_the_arm_check_must_pass_before_repairs(tmp_path):
    turns = [turn(("fw_rollback", {})), turn(("src_edit", FIX_EDIT)), turn(("arm_run", {})), turn(("src_edit", FIX_EDIT)),
             turn(text="done")]
    pod = latched_pod()
    d, s, _ = run(pod, turns, tmp_path)
    assert s["out_of_order"] == 1 and "check the arm first" in d.results[1][0][2]
    assert json.loads(d.results[3][0][2])["replacements"] == 1          # allowed once the arm passed on known-good


def test_a_build_flashed_after_the_last_arm_check_fails(tmp_path):
    pod = latched_pod()
    _, _, cfg = run(pod, forward_agent(recheck=False), tmp_path)
    sc = heal_test.score(pod, tmp_path / "agent" / "transcript.jsonl", 0, PRE, agent.ArmConfig(), cfg)
    assert sc["checks"]["restored_by_agent"] and not sc["checks"]["agent_ran_job_after_fix"] and not sc["passed"]


def test_heal_test_end_to_end_with_a_forward_fix_the_hand_proves_it(tmp_path, monkeypatch):
    """The agent rolls back, then fixes v1.8 forward and leaves its build running: the proof loops bring the hand back,
    and the arm pauses and carries on."""
    _patch(monkeypatch, tmp_path, forward_agent())
    pod, buf = SimPod("v1.0", runs_dir=tmp_path / "runs"), io.StringIO()
    out, r = heal_test.run_once(pod, _args("--sim", "--arm-port", "sim-arm"), console.Console(out=buf, color=False), 1)
    assert r["passed"], (r.get("error"), r.get("checks"))
    assert r["running"] == "agent build" and r["checks"]["fix_survives_object"]
    assert r["final_loops"]["agent_build"] and r["final_loops"]["pauses"] == ["cleared"]
    assert "these loops must show that it handles a hand" in buf.getvalue()


def test_heal_test_end_to_end_a_forward_fix_that_misses_the_bug_fails_on_the_hand(tmp_path, monkeypatch):
    """A wrong fix looks fine to the agent (nothing near: the gate passes, the arm plays every frame); only the hand in
    the proof loops shows the reading still never lets go."""
    _patch(monkeypatch, tmp_path, forward_agent(edit=WRONG_EDIT))
    pod = SimPod("v1.0", runs_dir=tmp_path / "runs")
    out, r = heal_test.run_once(pod, _args("--sim", "--arm-port", "sim-arm"),
                                console.Console(out=io.StringIO(), color=False), 1)
    c = r["checks"]
    assert c["agent_ran_job_after_fix"] and c["expected_motion_happened"] and c["good_firmware_running"]
    assert not c["fix_survives_object"] and not c["final_loops_ok"] and not r["passed"]
    assert r["final_loops"]["exit"] == 4
