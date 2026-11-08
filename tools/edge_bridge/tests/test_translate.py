"""The mapping from the healer's records to the contract (translate.py). Pure."""

import hashlib
import json
import math

from edge_bridge import podd, translate
from edge_bridge.fake_healer import (
    BAD_CRC,
    FIXED_CRC,
    GOOD_CRC,
    T0,
    FakeSocket,
    scenario_01_fixed,
    scenario_06_handed_off,
)
from edge_bridge.faults import FaultTracker
from romulus_contracts import CheckIn, IncidentReport, RecordingMeta


def test_mcu_build_reads_like_the_registry():
    assert translate.mcu_build("pod v1.1 2026-11-08T01:57:45Z", "0x32acfdaf") == "v1.1@32ACFDAF"
    assert translate.mcu_build(None, "32ACFDAF") == "32ACFDAF"
    assert translate.mcu_build("pod v1.0", None) == "v1.0"
    assert translate.normalize_crc("0xABC") == "00000ABC"
    boot = {"type": "E", "ev": "boot", "build": "pod v1.0 2026-11-07", "crc": "0x1A2B3C4D"}
    assert translate.build_from_boot(boot) == "v1.0@1A2B3C4D"
    assert translate.build_from_boot({"ev": "crash"}) is None


def test_health_follows_podd():
    sock = FakeSocket()
    status = sock.call("status")
    assert translate.health(status)[0] == "ok"
    assert translate.health(None) == (
        "failing",
        {"check": "podd_unreachable", "source": "edge_bridge"},
    )
    sock.open_incidents = [{"id": "us_no_echo-1", "kind": "us_no_echo", "severity": "warn"}]
    state, detail = translate.health(sock.call("status"))
    assert state == "degraded"
    assert detail["open_incidents"] == [
        {"id": "us_no_echo-1", "kind": "us_no_echo", "severity": "warn"}
    ]
    sock.open_incidents.append({"id": "crash-2", "kind": "crash", "severity": "critical"})
    assert translate.health(sock.call("status"))[0] == "failing"
    sock.open_incidents, sock.link_state = [], "silent"
    assert translate.health(sock.call("status"))[0] == "failing"


def test_checkin_is_a_real_device_with_pod_metrics():
    sock = FakeSocket()
    body = translate.checkin(sock.call("status"), sock.call("stats"), "v1.0@1A2B3C4D")
    payload = body.model_dump(mode="json")
    assert payload["kind"] == "real"  # K17: real hardware never claims sim
    assert payload["running"]["mcu_build"] == "v1.0@1A2B3C4D"
    assert payload["health"] == "ok"
    m = body.numeric_metrics()
    assert m["pod_acc_hz"] == 50.0 and m["pod_distance_mm"] == 480.0
    assert m["pod_loop_us"] == 850.0 and m["pod_temp"] == 31.5
    assert "pod_mode" not in m  # strings aren't metrics
    CheckIn.model_validate(payload)


def _fault_and_evidence(pod, t, now):
    """Run the tracker over the var/ folder and gather evidence like the bridge does."""
    tracker = FaultTracker()
    rows = [("i", r) for r in podd.read_all(pod.paths.log("incidents"))]
    rows += [("a", r) for r in podd.read_all(pod.paths.log("audit"))]
    rows += [("n", r) for r in podd.read_all(pod.paths.notifications)]
    rows.sort(key=lambda kr: kr[1]["ts"] + (kr[1].get("duration_s", 0) if kr[0] == "a" else 0))
    for kind, rec in rows:
        if kind == "i":
            tracker.observe_incident(rec, mcu_build="v1.1@32ACFDAF")
        elif kind == "a":
            tracker.observe_activity(rec["ts"] + rec["duration_s"])
        else:
            tracker.observe_activity(rec["ts"], handoff=bool(rec.get("actions")))
    ((fault, reason),) = tracker.pop_ready(now)
    lo, hi = translate.window(fault, now)
    deployments = [d for d in podd.read_all(pod.paths.deployments) if lo <= d["ts"] <= hi]
    images = {d["crc"]: podd.image(pod.paths, d["crc"]) for d in deployments}
    ev = translate.Evidence(
        lo=lo,
        hi=hi,
        logs={
            n: podd.read_window(pod.paths.log(n), lo, hi) for n in ("telemetry", "health", "events")
        },
        audit=podd.read_window(pod.paths.log("audit"), lo, hi),
        notifications=[n for n in podd.read_all(pod.paths.notifications) if lo <= n["ts"] <= hi],
        deployments=deployments,
        risks=podd.tool_risks(pod.paths),
        images=images,
        workspace_log=None,
    )
    return fault, reason, ev


def test_scenario_01_becomes_one_resolved_software_fix(pod):
    t = T0 + 100
    scenario_01_fixed(pod, t)
    fault, reason, ev = _fault_and_evidence(pod, t, now=t + 200)
    report = translate.report("pod-1", fault, reason, ev)
    inc = report.incident
    IncidentReport.model_validate(inc.model_dump(mode="json"))

    assert inc.incident_id == translate.incident_uuid("pod-1", f"crash-{int(t)}")
    assert inc.kind == "crash"
    assert inc.summary == "HardFault at pc 0x08002B9A (frame_pack) (also: reboot_loop, safe_mode)"
    assert inc.running.mcu_build == "v1.1@32ACFDAF"
    assert inc.overlay is None  # evidence only: never rolled out by the gate
    assert inc.outcome.model_dump() == {
        "resolved": True,
        "resolving_action": "fw_flash",
        "suspected_cause": "software",
        "category": "fix-code",
    }
    # Only the calls that change something are actions; the notes are the reasoning.
    assert [a["tool"] for a in inc.agent.actions] == ["src_edit", "fw_build", "fw_flash"]
    assert inc.agent.reasoning.startswith("Unaligned halfword store in frame_pack")
    assert inc.agent.model_dump()["tool_calls"] == 7

    extra = inc.model_dump(mode="json")
    assert [p["kind"] for p in extra["podd_incidents"]] == ["crash", "reboot_loop", "safe_mode"]
    assert extra["firmware_patch"]["image"]["crc"] == FIXED_CRC
    assert extra["firmware_patch"]["uncommitted_at_build"] == "--- a/src/frame.c\n"
    assert extra["deployments"][0]["result"] == "ok"
    assert extra["bridge"]["reason"] == "resolved"
    assert inc.signals["pod_distance_mm"][0] == 480.0
    assert inc.signals["pod_acc_mag_mg"][0] == round(math.sqrt(12**2 + 8**2 + 1002**2), 1)
    assert len(inc.signals["pod_acc_mag_mg"]) == translate.MAX_SIGNAL_SAMPLES
    assert any(e.get("ev") == "flash_done" for e in inc.log_tail)

    kinds = {m.kind: (m, data) for m, data in report.recordings}
    assert sorted(kinds) == ["agent_trace", "log_window"]
    assert inc.recordings == [m.recording_id for m, _ in report.recordings]
    for meta, data in report.recordings:
        RecordingMeta.model_validate(meta.model_dump(mode="json"))
        assert meta.sha256 == hashlib.sha256(data).hexdigest()
        assert meta.size_bytes == len(data)
        assert meta.incident_id == inc.incident_id
        assert meta.labels == ["crash", "reboot_loop", "safe_mode"]
        assert meta.recording_id == translate.recording_uuid(inc.incident_id, meta.kind)

    meta, data = kinds["log_window"]
    rows = [json.loads(line) for line in data.splitlines()]
    assert meta.streams == ["events", "health", "telemetry"]
    assert [r["ts"] for r in rows] == sorted(r["ts"] for r in rows)
    assert {r["log"] for r in rows} == {"telemetry", "health", "events"}
    assert rows[0]["ts"] >= ev.lo and rows[-1]["ts"] <= ev.hi

    meta, data = kinds["agent_trace"]
    rows = [json.loads(line) for line in data.splitlines()]
    assert meta.model_dump()["trace_source"] == translate.TRACE_SOURCE
    assert [r["source"] for r in rows].count("note") == 2
    assert rows[0]["tool"] == "triage"


def test_scenario_06_hands_off_as_unresolved_hardware(pod):
    t = T0 + 100
    scenario_06_handed_off(pod, t)
    fault, reason, ev = _fault_and_evidence(pod, t, now=t + 131)
    assert reason == "handed_off"
    inc = translate.report("pod-1", fault, reason, ev).incident
    # Unresolved + hardware is what the cloud's triage turns into a work order.
    assert inc.outcome.model_dump() == {
        "resolved": False,
        "resolving_action": "fw_flash",
        "suspected_cause": "hardware",
        "category": None,
    }
    assert inc.kind == "acc_error"
    assert inc.model_dump(mode="json")["notifications"][0]["actions"].startswith("Reseat")


def test_a_rollback_to_a_release_image_is_ops_not_a_code_fix(pod):
    t = T0 + 100
    pod.image(BAD_CRC, "v1.1", "release")
    pod.image(GOOD_CRC, "v1.0", "release", status="known-good")
    crash = pod.open("crash", t)
    pod.call("fw_rollback", t + 10, {"to": "known-good"}, duration=0.1)
    pod.flash(GOOD_CRC, "v1.0", t + 10)
    pod.resolve(crash, t + 45)
    fault, reason, ev = _fault_and_evidence(pod, t, now=t + 100)
    outcome = translate.report("pod-1", fault, reason, ev).incident.outcome
    assert (outcome.resolving_action, outcome.category) == ("fw_rollback", "ops")


def test_a_runtime_mitigation_is_ops(pod):
    t = T0 + 100
    stale = pod.open("acc_stale", t, severity="warn")
    pod.call("pod_cmd", t + 8, {"line": "acc init"})
    pod.resolve(stale, t + 20)
    fault, reason, ev = _fault_and_evidence(pod, t, now=t + 60)
    outcome = translate.report("pod-1", fault, reason, ev).incident.outcome
    assert outcome.model_dump() == {
        "resolved": True,
        "resolving_action": "pod_cmd: acc init",
        "suspected_cause": "software",
        "category": "ops",
    }


def test_a_fault_that_clears_by_itself_has_no_agent_and_no_trace(pod):
    t = T0 + 100
    pod.telemetry(t - 5, t + 5)
    blip = pod.open("us_intermittent", t, severity="warn")
    pod.resolve(blip, t + 12)
    fault, reason, ev = _fault_and_evidence(pod, t, now=t + 60)
    report = translate.report("pod-1", fault, reason, ev)
    assert report.incident.agent is None
    assert report.incident.outcome.suspected_cause == "unknown"
    assert report.incident.outcome.resolving_action is None
    assert "firmware_patch" not in report.incident.model_dump()
    assert [m.kind for m, _ in report.recordings] == ["log_window"]


def test_long_log_windows_keep_both_ends_and_say_what_was_left_out():
    rows = [{"log": "telemetry", "ts": float(i)} for i in range(10)]
    kept = translate._elide(rows, 4)
    assert [r["ts"] for r in kept] == [0.0, 1.0, 2.0, 8.0, 9.0]
    assert kept[2] == {"log": "edge_bridge", "ts": 2.0, "ev": "elided", "count": 6}
    assert translate._sample(list(range(10)), 4) == [0, 3, 6, 9]
