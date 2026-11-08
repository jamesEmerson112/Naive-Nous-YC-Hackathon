"""Agent run reports: a harness transcript becomes a write-up, a firmware list, requests for
help and an outcome (agent_runs.py), and the bridge sends it whenever it changes."""

import json

from edge_bridge import agent_runs, translate
from edge_bridge.fake_healer import (
    BAD_CRC,
    FIXED_CRC,
    T0,
    AgentRunDir,
    agent_run_01,
    scenario_01_fixed,
)


def _records(run: AgentRunDir) -> list[dict]:
    return [json.loads(line) for line in run.path.read_text().splitlines()]


def test_a_finished_run_reads_as_a_write_up_with_firmware_help_and_outcome(tmp_path):
    run = agent_run_01(tmp_path, T0, ("crash-1", "loop-1", "safe-1"))
    r = agent_runs.build(run.dir.name, _records(run), incident_ids=["inc"])

    assert (r["state"], r["incident_ids"], r["sim"]) == ("finished", ["inc"], False)
    assert r["outcome"]["status"] == "fixed" and r["outcome"]["verified_by"] == "device_check"
    assert r["outcome"]["headline"].startswith("Fixed: the pod runs v1.1-1-dirty (5E6F7081)")

    fw = [(f["event"], f["image"].get("crc"), f["bad"], f["error"]) for f in r["firmware"]]
    assert fw == [
        ("running_at_start", BAD_CRC, True, False),  # the fault appeared on it
        ("flash_failed", "CAFEF00D", True, True),  # failed its checks
        ("auto_rolled_back", BAD_CRC, False, False),
        ("built", FIXED_CRC, False, False),
        ("flashed", FIXED_CRC, False, False),
    ]
    assert r["firmware"][-1]["running_at_end"] and "no_reboots" in r["firmware"][1]["verdict"]

    (ask,) = r["help_requests"]
    assert (ask["to"], ask["source"], ask["step"]) == ("expert_model", "agent", 3)
    assert ask["summary"] == "Why does frame_pack fault on the M0?"

    labels = [p["label"] for p in r["writeup"]["summary"]]
    assert labels[0] == "Problem" and "Result" in labels and labels[-1] == "Agent's report"
    assert r["writeup"]["report"].startswith("Root cause: unaligned halfword store")
    assert [s["phase"] for s in r["steps"]][:3] == ["investigate", "repair", "escalate"]
    assert r["steps"][0]["thought"].startswith("The pod is crash-looping")
    assert r["steps"][0]["effect"].startswith("1 finding: crash (critical)")
    assert r["stats"]["tokens_in"] == 50_000 and r["stats"]["refused"] == 0
    json.dumps(r)  # plain JSON all the way down


def test_a_run_in_progress_is_working_and_the_harness_asks_for_nothing_yet(tmp_path):
    run = agent_run_01(tmp_path, T0, ("crash-1",), finish=False)
    r = agent_runs.build(run.dir.name, _records(run))
    assert r["state"] == "running" and r["ended_at"] is None
    assert r["outcome"]["status"] == "working" and "step 3" in r["outcome"]["headline"]
    assert [h["source"] for h in r["help_requests"]] == ["agent"]


def test_a_run_that_stops_unfixed_asks_for_a_stronger_model(tmp_path):
    run = AgentRunDir(tmp_path, "20261108-130000-ollama-watch")
    run.start(T0, [], {"crc": BAD_CRC, "version": "v1.8", "status": "candidate"})
    run.call(T0 + 5, "triage", {}, {"findings": [{"kind": "us_mismatch", "severity": "critical"}]})
    run.end(
        T0 + 60,
        "",
        {"chip": {"crc": BAD_CRC, "version": "v1.8", "status": "candidate"}, "findings": []},
        stop="context_full",
    )
    r = agent_runs.build(run.dir.name, _records(run))
    assert r["outcome"]["status"] == "broken"  # triage is quiet, but it's still on the faulty image
    assert "the image the fault appeared on" in r["outcome"]["headline"]
    (h,) = r["help_requests"]
    assert (h["to"], h["source"]) == ("expert_model", "harness")
    assert "context window filled up" in h["summary"]


def test_a_bench_score_decides_the_outcome(tmp_path):
    run = agent_run_01(tmp_path, T0, ("crash-1",))
    score = {
        "passed": False,
        "running": "agent build",
        "running_build": "pod v1.1-1-dirty",
        "final_triage": [],
        "final_loops": {"check": "exit 4: reflex gave up"},
        "checks": {"good_firmware_running": True, "triage_clean": True, "final_loops_ok": False},
    }
    r = agent_runs.build(run.dir.name, _records(run), score=score, score_at=T0 + 300)
    assert r["outcome"]["status"] == "broken" and r["outcome"]["verified_by"] == "bench"
    assert "reflex gave up" in r["outcome"]["headline"]
    assert r["verification"]["checks"]["final_loops_ok"] is False
    assert r["updated_at"] == agent_runs.iso(T0 + 300)


def test_the_bridge_reports_the_run_with_its_incident_and_again_when_it_changes(
    make_bridge, pod, cloud
):
    bridge = make_bridge()
    bridge.tick(T0)
    t = T0 + 60
    ids = scenario_01_fixed(pod, t)
    run = agent_run_01(pod.paths.harness_runs, t + 10, ids)

    result = bridge.tick(t + 200)
    assert result.runs == 1 and len(result.reported) == 1
    report = cloud.agent_runs[("pod-1", run.dir.name)]
    (incident_id,) = result.reported
    assert report["incident_ids"] == [incident_id]
    assert report["incident_ids"] == [str(translate.incident_uuid("pod-1", ids[0]))]
    assert report["outcome"]["status"] == "fixed"

    assert bridge.tick(t + 210).runs == 0  # nothing changed, nothing sent
    (run.dir / "score.json").write_text(json.dumps({"checks": {}, "passed": True}))
    assert bridge.tick(t + 220).runs == 1


def test_runs_from_before_the_first_start_are_left_alone(make_bridge, pod, cloud):
    agent_run_01(pod.paths.harness_runs, T0 - 500, ("x",))
    assert make_bridge().tick(T0).runs == 0
    assert cloud.agent_runs == {}


def test_a_rehearsal_is_tied_to_no_incident_and_an_old_transcript_gets_a_window(
    make_bridge, pod, cloud
):
    bridge = make_bridge()
    bridge.tick(T0)
    sim = AgentRunDir(pod.paths.harness_runs, "20261108-140000-arm08-sim-ollama")
    sim.start(T0 + 10, [{"id": "us-1", "kind": "us_mismatch"}], {"crc": BAD_CRC})
    old = AgentRunDir(pod.paths.harness_runs, "20261108-140500-ollama-watch")
    old.start(T0 + 20, None, {"crc": BAD_CRC})  # a transcript from before podd_incidents
    assert bridge.tick(T0 + 30).runs == 2
    sim_r = cloud.agent_runs[("pod-1", sim.dir.name)]
    old_r = cloud.agent_runs[("pod-1", old.dir.name)]
    assert sim_r["sim"] and sim_r["incident_ids"] == [] and sim_r.get("incident_window") is None
    assert old_r["incident_ids"] == [] and len(old_r["incident_window"]) == 2


def test_a_cloud_without_the_route_is_retried_next_tick(make_bridge, pod, cloud):
    bridge = make_bridge()
    bridge.tick(T0)
    agent_run_01(pod.paths.harness_runs, T0 + 10, ("x",))
    cloud.fail_status = None
    original = cloud._agent_run
    cloud._agent_run = lambda request, device, run: __import__("httpx").Response(404)
    assert bridge.tick(T0 + 20).runs == 0
    cloud._agent_run = original
    assert bridge.tick(T0 + 30).runs == 1
