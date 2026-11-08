"""What the cloud receives for each kind of fault (translate.py's mapping).

The check-in tests call translate directly. The incident tests run the real bridge over a
healer `var/` folder and read what reached the fake cloud, so they cover the mapping as
the bridge applies it (grouping, stream order, evidence windows) with no copy of its
pipeline here.
"""

import hashlib
import json
import math

from edge_bridge import translate
from edge_bridge.bridge import Bridge
from edge_bridge.fake_healer import (
    BAD_CRC,
    FIXED_CRC,
    GOOD_CRC,
    T0,
    FakeSocket,
    PoddVar,
    scenario_01_fixed,
    scenario_06_handed_off,
)
from fake_device.client import DeviceClient
from fake_device.fake_cloud import FakeCloud
from fake_device.outbox import Outbox
from romulus_contracts import CheckIn, IncidentReport, RecordingMeta


def _reported(pod: PoddVar, now: float) -> list[dict]:
    """Run the bridge once over `pod` (every record counts) and return what it sent: the
    incidents in time order, each with its recordings by kind (`_recordings`)."""
    cloud, state = FakeCloud(), pod.healer.parent / "bridge"
    bridge = Bridge(
        pod.paths,
        "pod-1",
        DeviceClient(cloud.http(), "pod-1"),
        Outbox(state / "outbox"),
        FakeSocket(),
        state / "state.json",
        backfill_s=10**6,
    )
    bridge.tick(now)
    incidents = sorted(cloud.incidents.values(), key=lambda i: i["occurred_at"])
    for incident in incidents:
        IncidentReport.model_validate(incident)
        incident["_recordings"] = {
            r["kind"]: (r, cloud.blobs[r["sha256"]])
            for r in cloud.recordings.values()
            if r["incident_id"] == incident["incident_id"]
        }
    return incidents


def _only(pod: PoddVar, now: float) -> dict:
    (incident,) = _reported(pod, now)
    return incident


# -- check-ins -----------------------------------------------------------------------------


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
    assert translate.health(sock.call("status"))[0] == "ok"
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


# -- incidents -----------------------------------------------------------------------------


def test_scenario_01_becomes_one_resolved_software_fix(pod):
    t = T0 + 100
    scenario_01_fixed(pod, t)
    inc = _only(pod, t + 200)
    incident_id = translate.incident_uuid("pod-1", f"crash-{int(t)}")

    assert inc["incident_id"] == str(incident_id)
    assert inc["kind"] == "crash"
    assert inc["summary"] == (
        "HardFault at pc 0x08002B9A (frame_pack) (also: reboot_loop, safe_mode)"
    )
    assert inc["running"]["mcu_build"] == "v1.1@32ACFDAF"  # what crashed, not the fix
    assert inc["overlay"] is None  # evidence only: never rolled out by the gate
    assert inc["outcome"] == {
        "resolved": True,
        "resolving_action": "fw_flash",
        "suspected_cause": "software",
        "category": "fix-code",
    }
    # Only the calls that change something are actions; the notes are the reasoning.
    assert [a["tool"] for a in inc["agent"]["actions"]] == ["src_edit", "fw_build", "fw_flash"]
    assert inc["agent"]["reasoning"].startswith("Unaligned halfword store in frame_pack")
    assert inc["agent"]["tool_calls"] == 7
    assert [p["kind"] for p in inc["podd_incidents"]] == ["crash", "reboot_loop", "safe_mode"]
    assert inc["firmware_patch"]["image"]["crc"] == FIXED_CRC
    assert inc["firmware_patch"]["uncommitted_at_build"] == "--- a/src/frame.c\n"
    assert inc["deployments"][0]["result"] == "ok"
    assert inc["bridge"]["reason"] == "resolved"
    assert inc["signals"]["pod_distance_mm"][0] == 480.0
    assert inc["signals"]["pod_acc_mag_mg"][0] == round(math.sqrt(12**2 + 8**2 + 1002**2), 1)
    assert len(inc["signals"]["pod_acc_mag_mg"]) == translate.MAX_SIGNAL_SAMPLES
    assert any(e.get("ev") == "flash_done" for e in inc["log_tail"])

    recordings = inc["_recordings"]
    assert sorted(recordings) == ["agent_trace", "log_window"]
    assert sorted(inc["recordings"]) == sorted(m["recording_id"] for m, _ in recordings.values())
    for meta, data in recordings.values():
        RecordingMeta.model_validate(meta)
        assert meta["sha256"] == hashlib.sha256(data).hexdigest()
        assert meta["size_bytes"] == len(data)
        assert meta["labels"] == ["crash", "reboot_loop", "safe_mode"]
        assert meta["recording_id"] == str(translate.recording_uuid(incident_id, meta["kind"]))

    meta, data = recordings["log_window"]
    rows = [json.loads(line) for line in data.splitlines()]
    assert meta["streams"] == ["events", "health", "telemetry"]
    assert [r["ts"] for r in rows] == sorted(r["ts"] for r in rows)
    assert {r["log"] for r in rows} == {"telemetry", "health", "events"}
    assert rows[0]["ts"] >= t - translate.PRE_S

    meta, data = recordings["agent_trace"]
    rows = [json.loads(line) for line in data.splitlines()]
    assert meta["trace_source"] == translate.TRACE_SOURCE
    assert [r["source"] for r in rows].count("note") == 2
    assert rows[0]["tool"] == "triage"


def test_scenario_06_hands_off_as_unresolved_hardware(pod):
    t = T0 + 100
    scenario_06_handed_off(pod, t)
    inc = _only(pod, t + 131)
    assert inc["bridge"]["reason"] == "handed_off"
    # Unresolved + hardware is what the cloud's triage turns into a work order.
    assert inc["outcome"] == {
        "resolved": False,
        "resolving_action": "fw_flash",
        "suspected_cause": "hardware",
        "category": None,
    }
    assert inc["kind"] == "acc_error"
    assert inc["notifications"][0]["actions"].startswith("Reseat")


def test_a_precautionary_request_doesnt_make_a_verified_fix_hardware(pod):
    t = T0 + 100
    pod.image(FIXED_CRC, "v1.1-1-gabc1234", "workspace", patch="--- a/src/frame.c\n")
    crash = pod.open("crash", t)
    pod.call("src_edit", t + 5, {"path": "src/frame.c"})
    pod.notify("If the pod crashes again, check the USB cable", t + 8, actions="Check the cable")
    pod.flash(FIXED_CRC, "v1.1-1-gabc1234", t + 10)
    pod.resolve(crash, t + 45)
    outcome = _only(pod, t + 120)["outcome"]
    assert (outcome["suspected_cause"], outcome["category"]) == ("software", "fix-code")


def test_the_build_is_the_one_running_when_the_fault_opened(pod):
    # A bad image boots and crashes, and the healer rolls back before the bridge looks.
    t = T0 + 100
    pod.image(GOOD_CRC, "v1.0", "release", status="known-good")
    pod.boot(t - 20, "v1.3", BAD_CRC)
    crash = pod.open("crash", t)
    pod.call("fw_rollback", t + 2, {"to": "known-good"}, duration=0.1)
    pod.flash(GOOD_CRC, "v1.0", t + 3)
    pod.resolve(crash, t + 40)
    inc = _only(pod, t + 120)
    assert inc["running"]["mcu_build"] == "v1.3@32ACFDAF"
    assert (inc["outcome"]["resolving_action"], inc["outcome"]["category"]) == (
        "fw_rollback",
        "ops",
    )


def test_a_long_flash_holds_the_report_until_it_finishes(pod):
    # The pod reboots into the fix early in the flash and its incidents resolve while
    # fw_flash is still watching; the audit and deployment records come only at the end.
    t = T0 + 100
    pod.image(FIXED_CRC, "v1.1-1-gabc1234", "workspace", patch="--- a/src/frame.c\n")
    crash = pod.open("crash", t)
    pod.call("src_edit", t + 5, {"path": "src/frame.c"})
    pod.flash(FIXED_CRC, "v1.1-1-gabc1234", t + 10, duration=45)  # flash_done at t + 55
    pod.resolve(crash, t + 25)
    inc = _only(pod, t + 200)
    assert inc["outcome"]["resolving_action"] == "fw_flash"
    assert [a["tool"] for a in inc["agent"]["actions"]] == ["src_edit", "fw_flash"]
    assert inc["deployments"][0]["result"] == "ok"


def test_a_runtime_command_counts_only_when_the_pod_accepted_it(pod):
    t = T0 + 100
    stale = pod.open("acc_stale", t, severity="warn")
    pod.pod_cmd("acc init", t + 8)
    pod.resolve(stale, t + 20)
    assert _only(pod, t + 60)["outcome"] == {
        "resolved": True,
        "resolving_action": "pod_cmd: acc init",
        "suspected_cause": "software",
        "category": "ops",
    }


def test_a_refused_command_or_registry_bookkeeping_is_not_the_fix(pod):
    t = T0 + 100
    stale = pod.open("acc_stale", t, severity="warn")
    pod.pod_cmd("acc init", t + 8, ok=False)  # the pod answered ok=0
    pod.call("fw_mark", t + 10, {"image": BAD_CRC, "status": "bad"})  # registry only
    pod.resolve(stale, t + 20)
    inc = _only(pod, t + 60)
    assert inc["outcome"]["resolving_action"] is None
    assert inc["outcome"]["suspected_cause"] == "unknown"
    assert [a["tool"] for a in inc["agent"]["actions"]] == ["pod_cmd", "fw_mark"]


def test_activity_belongs_to_its_own_fault(pod):
    # Fault A's window would reach into fault B; B's request for a person is B's alone.
    t = T0 + 100
    a = pod.open("us_intermittent", t, severity="warn")
    pod.resolve(a, t + 5)
    pod.call("triage", t + 10)
    b = pod.open("acc_error", t + 17, severity="warn")  # 12 s after A's last record
    pod.notify("LIS3DH not responding", t + 20, actions="Reseat the LIS3DH")
    pod.resolve(b, t + 200)
    first, second = _reported(pod, t + 400)
    assert (first["kind"], second["kind"]) == ("us_intermittent", "acc_error")
    assert first["outcome"]["suspected_cause"] == "unknown"
    assert first["notifications"] == []
    assert second["outcome"]["suspected_cause"] == "hardware"


def test_a_fault_that_clears_by_itself_has_no_agent_and_no_trace(pod):
    t = T0 + 100
    pod.telemetry(t - 5, t + 5)
    blip = pod.open("us_intermittent", t, severity="warn")
    pod.resolve(blip, t + 12)
    inc = _only(pod, t + 60)
    assert inc["agent"] is None
    assert inc["outcome"]["suspected_cause"] == "unknown"
    assert inc["outcome"]["resolving_action"] is None
    assert "firmware_patch" not in inc
    assert sorted(inc["_recordings"]) == ["log_window"]


def test_long_log_windows_keep_both_ends_and_say_what_was_left_out():
    rows = [{"log": "telemetry", "ts": float(i)} for i in range(10)]
    kept = translate._elide(rows, 4)
    assert [r["ts"] for r in kept] == [0.0, 1.0, 2.0, 8.0, 9.0]
    assert kept[2] == {"log": "edge_bridge", "ts": 2.0, "ev": "elided", "count": 6}
    assert translate._sample(list(range(10)), 4) == [0, 3, 6, 9]
