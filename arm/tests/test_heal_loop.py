"""The whole heal loop offline: the real agent loop, gate, arm tools (arm_run, arm_state, arm_report), scorer,
live view and heal_test.py's run_once, with a scripted model in place of qwen, a simulated pod in place of the
bench, and a simulated arm that follows its commands (simpod.FakeArm)."""
import argparse
import io
import json

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


REPORT = ("Root cause: release v1.7 changed the distance math in hcsr04.c to (uint16_t)(us*343+1000)/2000. The "
          "uint16_t cast truncates the product, so every distance wraps into 0-32 mm while the echo time says "
          "~900 mm. Not an obstruction. Fix: rolled back to known-good v1.0. Verified: arm_run exit 0, no reflex, "
          "663 of 663 frames played, range coverage 1.0 on every joint.")

GOOD_AGENT = [
    turn(("triage", {}), thinking="The arm swung back and forth and gave up. Check what podd sees first."),
    turn(("arm_report", {}), thinking="What did that job do?"),
    turn(("telemetry_recent", {"kind": "D", "limit": 5}),
         thinking="us_mismatch: echo ~5250 us should be ~900 mm, but mm is tiny. Look at raw samples."),
    turn(("src_log", {}), ("src_diff", {"rev_a": "v1.0"}), thinking="Echo is long, so nothing is close. What changed?"),
    turn(("note", {"text": "v1.7 adds a uint16_t cast in the dist_mm computation: truncation, firmware fault"}),
         ("fw_rollback", {"reason": "v1.7 truncates the distance to 16 bits; restore known-good"})),
    turn(("arm_run", {}), thinking="Launch the job and check the motion."),
    turn(("arm_state", {}), thinking="Back at rest?"),
    turn(text=REPORT),
]

WRONG_AGENT = [
    turn(("triage", {})),
    turn(("notify_human", {"summary": "Something is blocking the ultrasonic sensor", "actions": "clear it"})),
    turn(text="Something is in front of the sensor; asked a person to remove it."),
]

PRE = {"baseline_gate_passed": True, "incident_opened": True, "fault_reproduced": True}


def arm_cfg(tmp_path, with_arm=True):
    return agent.ArmConfig(port="sim-arm" if with_arm else None, runs_dir=str(tmp_path / "runs"))


def run(pod, turns, tmp_path, ui=None, cfg=None):
    cfg = cfg or arm_cfg(tmp_path)
    job = agent.run_arm(pod, cfg)                       # the operator's job on whatever the pod runs now
    kickoff = agent.arm_kickoff(job, agent.open_incidents(pod))
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
    pod = SimPod("v1.7")
    d, s, cfg = run(pod, GOOD_AGENT, tmp_path)
    assert "did not go as expected (exit 4" in d.kickoff and "reflex: object at" in d.kickoff
    assert {"arm_run", "arm_state", "arm_report"} <= set(d.tools) and "src_edit" not in d.tools
    assert "## The robot arm" in d.system and "reach the commanded range on every joint" in d.system
    assert s["stop"] == "end_turn" and s["denied"] == [] and s["by_risk"]["arm"] == 1 and s["flashes_allowed"] == 1
    report_before = json.loads(d.results[1][0][2])
    assert report_before["exit"] == 4 and report_before["obstacle_reflex"]["swings"] >= 5
    job = json.loads(d.results[5][0][2])
    assert job["exit"] == 0 and job["motion_report"]["playback"]["complete"]
    state = json.loads(d.results[6][0][2])
    assert state["ok"] and state["joints"]["shoulder_pan"]["position"] == 18.0     # back at rest
    sc = heal_test.score(pod, tmp_path / "agent" / "transcript.jsonl", 0, PRE, agent.ArmConfig(), cfg)
    assert sc["passed"], sc["checks"]
    assert sc["found_exact_defect"] and sc["arm_runs"] == [{"after_flash": True, "exit": 0, "verdict": job["verdict"]}]


def test_wrong_diagnosis_fails_on_bench_evidence(tmp_path):
    pod = SimPod("v1.7")
    _, _, cfg = run(pod, WRONG_AGENT, tmp_path)
    sc = heal_test.score(pod, tmp_path / "agent" / "transcript.jsonl", 0, PRE, agent.ArmConfig(), cfg)
    c = sc["checks"]
    assert not sc["passed"]
    assert not (c["restored_by_agent"] or c["known_good_running"] or c["gate_passes_now"] or c["triage_clean"])
    assert not (c["agent_ran_job_after_fix"] or c["expected_motion_happened"])


def test_a_job_before_the_flash_or_a_silent_report_does_not_count(tmp_path):
    turns = [turn(("arm_run", {})), turn(("fw_rollback", {})), turn(text="Rolled back the firmware conversion bug.")]
    pod = SimPod("v1.7")
    _, _, cfg = run(pod, turns, tmp_path)
    sc = heal_test.score(pod, tmp_path / "agent" / "transcript.jsonl", 0, PRE, agent.ArmConfig(), cfg)
    assert sc["checks"]["known_good_running"] and not sc["checks"]["agent_ran_job_after_fix"]
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
    run(SimPod("v1.7"), GOOD_AGENT, tmp_path, ui=console.Console(out=buf, color=False))
    live = buf.getvalue()
    for want in ("what woke the agent", "us_mismatch", "Echo is long, so nothing is close", "▶ fw_rollback",
                 "flash ok · now running v1.0 BAADB704", "ARM JOB OK", "playback: 663 of 663 frames",
                 "range reached: shoulder_pan 1.0", "reflex:", "never cleared", "+        out->dist_mm = (uint16_t)",
                 "Root cause: release v1.7", "run finished · stop: end_turn"):
        assert want in live, want
    buf2 = io.StringIO()
    view.replay(tmp_path / "agent" / "transcript.jsonl", console.Console(out=buf2, color=False))
    for want in ("Echo is long, so nothing is close", "ARM JOB OK", "flash ok · now running v1.0"):
        assert want in buf2.getvalue(), want


def _args(*extra):
    p = argparse.ArgumentParser()
    agent.add_agent_args(p)
    p.add_argument("--keep", action="store_true")
    return p.parse_args(["--tools", "arm", *extra])


def test_heal_test_run_once_end_to_end(tmp_path, monkeypatch):
    """arm/heal_test.py's full sequence on the simulated bench with the simulated arm: reset, baseline gate,
    deploy v1.7, incident, the operator's job swings the arm, agent (fix, own job, motion check), bench checks."""
    monkeypatch.setattr(agent, "RUNS", tmp_path)
    monkeypatch.setattr(agent, "DEBOUNCE_S", 0)
    monkeypatch.setattr(heal_test.time, "sleep", lambda s: None)
    monkeypatch.setattr(agent, "make_driver", lambda a: ScriptedDriver(GOOD_AGENT))
    pod, buf = SimPod("v1.0", runs_dir=tmp_path / "runs"), io.StringIO()
    out, r = heal_test.run_once(pod, _args("--arm-port", "sim-arm"), console.Console(out=buf, color=False), 1)
    assert r["passed"], (r.get("error"), r.get("checks"))
    assert [w[0] for w in pod.ops] == ["reset", "start", "reset"] and pod.ops[1] == ("start", "07")
    assert json.loads((out / "score.json").read_text())["passed"]
    text = buf.getvalue()
    assert text.count("✔ PASS") >= len(heal_test.CHECKS) and "✘ FAIL" not in text
    assert "arm job on v1.7: exit 4" in text and "swings" in text


def test_heal_test_aborts_when_the_sensor_is_really_blocked(tmp_path, monkeypatch):
    """If the gate refuses on known-good firmware too, the test must stop: a pass would prove nothing."""
    monkeypatch.setattr(agent, "RUNS", tmp_path)
    monkeypatch.setattr(heal_test.time, "sleep", lambda s: None)
    pod = SimPod("v1.0", distance_mm=120, runs_dir=tmp_path / "runs")     # a real object at 12 cm
    out, r = heal_test.run_once(pod, _args("--arm-port", "sim-arm"), console.Console(out=io.StringIO(), color=False), 1)
    assert not r["passed"] and "refuses on known-good" in r["error"]
    assert [w[0] for w in pod.ops] == ["reset", "reset"] and pod.arm.moves == 0
