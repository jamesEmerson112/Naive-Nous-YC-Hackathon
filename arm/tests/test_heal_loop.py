"""The whole heal loop offline: the real agent loop, gate, arm_test plumbing, scorer, live view and
heal_test.py's run_once, with a scripted model in place of qwen and a simulated pod in place of the bench."""
import argparse
import io
import json

import pytest

from simpod import GOOD, REPO, SimPod
import agent
import console
import heal_test  # noqa: E402  (arm/heal_test.py; simpod put arm/ on sys.path)
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
          "~900 mm. Not an obstruction. Fix: rolled back to known-good v1.0. arm_test passed afterwards.")

GOOD_AGENT = [
    turn(("triage", {}), thinking="The arm refused. Check what podd sees first."),
    turn(("telemetry_recent", {"kind": "D", "limit": 5}),
         thinking="us_mismatch: echo ~5250 us should be ~900 mm, but mm is tiny. Look at raw samples."),
    turn(("src_log", {}), ("src_diff", {"rev_a": "v1.0"}), thinking="Echo is long, so nothing is close. What changed?"),
    turn(("note", {"text": "v1.7 adds a uint16_t cast in the dist_mm computation: truncation, firmware fault"}),
         ("fw_rollback", {"reason": "v1.7 truncates the distance to 16 bits; restore known-good"})),
    turn(("arm_test", {}), thinking="Confirm the arm runs now."),
    turn(text=REPORT),
]

WRONG_AGENT = [
    turn(("triage", {})),
    turn(("notify_human", {"summary": "Something is blocking the ultrasonic sensor", "actions": "clear it"})),
    turn(text="Something is in front of the sensor; asked a person to remove it."),
]

PRE = {"baseline_gate_passed": True, "incident_opened": True, "fault_reproduced": True}


def run(pod, turns, tmp_path, ui=None):
    job = agent.run_arm(pod, agent.ArmConfig())
    kickoff = agent.arm_kickoff(job, agent.open_incidents(pod))
    d = ScriptedDriver(turns)
    s = agent.run_agent(pod, d, tmp_path, tools="arm", kickoff=kickoff, arm=agent.ArmConfig(), ui=ui)
    return d, s


def test_good_agent_restores_known_good_and_proves_it_with_arm_test(tmp_path):
    pod, since = SimPod("v1.7"), 0
    d, s = run(pod, GOOD_AGENT, tmp_path)
    assert "refused to start" in d.kickoff and "us_mismatch" in d.kickoff and "last samples: mm=" in d.kickoff
    assert "arm_test" in d.tools and "src_edit" not in d.tools and "fw_build" not in d.tools
    assert "## The robot arm" in d.system and "fw_rollback" in d.system
    assert s["stop"] == "end_turn" and s["denied"] == [] and s["by_risk"]["arm"] == 1 and s["flashes_allowed"] == 1
    arm_result = json.loads(d.results[4][0][2])
    assert arm_result["passed"] and arm_result["exit"] == 0 and "start gate passed" in arm_result["verdict"]
    assert pod.firmware == "v1.0"
    sc = heal_test.score(pod, tmp_path / "transcript.jsonl", since, PRE, agent.ArmConfig())
    assert sc["passed"], sc["checks"]
    assert sc["found_exact_defect"] and sc["arm_test_runs"] == [{"after_flash": True, "passed": True,
                                                                 "verdict": arm_result["verdict"]}]


def test_wrong_diagnosis_fails_on_bench_evidence(tmp_path):
    pod = SimPod("v1.7")
    run(pod, WRONG_AGENT, tmp_path)
    sc = heal_test.score(pod, tmp_path / "transcript.jsonl", 0, PRE, agent.ArmConfig())
    c = sc["checks"]
    assert not sc["passed"]
    assert not (c["restored_by_agent"] or c["known_good_running"] or c["gate_passes_now"] or c["triage_clean"])
    assert not c["agent_confirmed_with_arm_test"]


def test_arm_test_before_the_flash_does_not_count_as_confirmation(tmp_path):
    turns = [turn(("arm_test", {})), turn(("fw_rollback", {})), turn(text="rolled back because of the firmware conversion")]
    pod = SimPod("v1.7")
    run(pod, turns, tmp_path)
    sc = heal_test.score(pod, tmp_path / "transcript.jsonl", 0, PRE, agent.ArmConfig())
    assert sc["checks"]["known_good_running"] and not sc["checks"]["agent_confirmed_with_arm_test"]


def test_arm_runs_are_capped(tmp_path):
    turns = [turn(*[("arm_test", {})] * 6), turn(text="done")]
    pod = SimPod("v1.0")
    d = ScriptedDriver(turns)
    s = agent.run_agent(pod, d, tmp_path, tools="arm", kickoff="go", arm=agent.ArmConfig(), arm_cap=4)
    assert s["by_risk"]["arm"] == 4 and s["forbidden"] == 2
    assert "budget" in s["denied"][0]["reason"]


def test_live_view_and_replay_show_the_reasoning_and_the_fix(tmp_path):
    buf = io.StringIO()
    run(SimPod("v1.7"), GOOD_AGENT, tmp_path, ui=console.Console(out=buf, color=False))
    live = buf.getvalue()
    for want in ("what woke the agent", "us_mismatch", "Echo is long, so nothing is close", "▶ fw_rollback",
                 "flash ok · now running v1.0 BAADB704", "ARM JOB PASSED", "+        out->dist_mm = (uint16_t)",
                 "Root cause: release v1.7", "run finished · stop: end_turn"):
        assert want in live, want
    buf2 = io.StringIO()
    view.replay(tmp_path / "transcript.jsonl", console.Console(out=buf2, color=False))
    for want in ("Echo is long, so nothing is close", "ARM JOB PASSED", "flash ok · now running v1.0"):
        assert want in buf2.getvalue(), want


def test_heal_test_run_once_end_to_end(tmp_path, monkeypatch):
    """arm/heal_test.py's full sequence on the simulated bench: reset, baseline gate, deploy v1.7, incident,
    gate refuses, agent, bench checks, reset."""
    monkeypatch.setattr(agent, "RUNS", tmp_path)
    monkeypatch.setattr(agent, "DEBOUNCE_S", 0)
    monkeypatch.setattr(heal_test.time, "sleep", lambda s: None)
    monkeypatch.setattr(agent, "make_driver", lambda a: ScriptedDriver(GOOD_AGENT))
    p = argparse.ArgumentParser()
    agent.add_agent_args(p)
    p.add_argument("--keep", action="store_true")
    a = p.parse_args(["--tools", "arm"])
    pod, buf = SimPod("v1.0"), io.StringIO()
    out, r = heal_test.run_once(pod, a, console.Console(out=buf, color=False), 1)
    assert r["passed"], (r.get("error"), r.get("checks"))
    assert [w[0] for w in pod.ops] == ["reset", "start", "reset"] and pod.ops[1] == ("start", "07")
    assert json.loads((out / "score.json").read_text())["passed"]
    assert (tmp_path / "results.jsonl").exists()
    text = buf.getvalue()
    assert text.count("✔ PASS") >= len(heal_test.CHECKS) and "✘ FAIL" not in text


def test_heal_test_aborts_when_the_sensor_is_really_blocked(tmp_path, monkeypatch):
    """If the gate refuses on known-good firmware too, the test must stop: a pass would prove nothing."""
    monkeypatch.setattr(agent, "RUNS", tmp_path)
    monkeypatch.setattr(heal_test.time, "sleep", lambda s: None)
    p = argparse.ArgumentParser()
    agent.add_agent_args(p)
    p.add_argument("--keep", action="store_true")
    a = p.parse_args(["--tools", "arm"])
    pod = SimPod("v1.0", distance_mm=120)              # a real object at 12 cm
    out, r = heal_test.run_once(pod, a, console.Console(out=io.StringIO(), color=False), 1)
    assert not r["passed"] and "refuses on known-good" in r["error"]
    assert [w[0] for w in pod.ops] == ["reset", "reset"]
