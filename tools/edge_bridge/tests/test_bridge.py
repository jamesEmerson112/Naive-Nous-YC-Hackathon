"""The bridge end to end against the fake cloud: check-ins, reports, restarts, outages."""

import logging

from edge_bridge import translate
from edge_bridge.fake_healer import (
    BAD_CRC,
    GOOD_CRC,
    T0,
    scenario_01_fixed,
    scenario_06_handed_off,
)
from edge_bridge.faults import Timing


def _boot(version, crc):
    return {
        "type": "E",
        "t": 1,
        "ev": "boot",
        "build": f"pod {version} x",
        "crc": f"0x{crc}",
        "ts": T0,
    }


def test_an_idle_pod_checks_in_as_real_hardware(make_bridge, sock, cloud):
    sock.last_boot = _boot("v1.0", GOOD_CRC)
    result = make_bridge().tick(T0)
    assert (result.health, result.checked_in, result.reported) == ("ok", True, [])
    (checkin,) = cloud.checkins["pod-1"]
    assert checkin["kind"] == "real"
    assert checkin["running"]["mcu_build"] == "v1.0@1A2B3C4D"
    assert checkin["metrics"]["pod_acc_hz"] == 50.0
    assert cloud.incidents == {}


def test_scenario_01_is_reported_once_with_its_recordings(make_bridge, pod, sock, cloud):
    bridge = make_bridge()
    sock.last_boot = _boot("v1.1", BAD_CRC)
    bridge.tick(T0)  # first start: nothing before T0 counts
    t = T0 + 60
    scenario_01_fixed(pod, t)

    assert bridge.tick(t + 60).reported == []  # still settling
    result = bridge.tick(t + 200)
    assert len(result.reported) == 1 and result.sent == 3

    (incident,) = cloud.incidents.values()
    assert incident["incident_id"] == result.reported[0]
    assert incident["kind"] == "crash"
    assert incident["running"]["mcu_build"] == "v1.1@32ACFDAF"
    assert incident["outcome"]["category"] == "fix-code"
    assert sorted(r["kind"] for r in cloud.recordings.values()) == ["agent_trace", "log_window"]
    assert {r["incident_id"] for r in cloud.recordings.values()} == {incident["incident_id"]}
    assert all(r["sha256"] in cloud.blobs for r in cloud.recordings.values())
    posts = [p for p in cloud.paths("POST") if not p.endswith("/checkin")]
    assert posts[0] == "/v1/devices/pod-1/incidents"  # the incident before its recordings

    bridge.tick(t + 210)
    assert cloud.paths("POST").count("/v1/devices/pod-1/incidents") == 1


def test_a_restart_resumes_without_reporting_twice(make_bridge, pod, sock, cloud):
    make_bridge().tick(T0)
    t = T0 + 60
    scenario_01_fixed(pod, t)
    make_bridge().tick(t + 60)  # a fresh process: the fault is in progress in state.json
    assert cloud.incidents == {}
    make_bridge().tick(t + 200)
    make_bridge().tick(t + 300)
    assert len(cloud.incidents) == 1
    assert cloud.paths("POST").count("/v1/devices/pod-1/incidents") == 1


def test_reporting_again_after_lost_state_is_harmless(make_bridge, pod, sock, cloud, tmp_path):
    t = T0 + 60
    scenario_01_fixed(pod, t)
    make_bridge(backfill_s=3600).tick(t + 200)
    (tmp_path / "state" / "pod-1" / "state.json").unlink()
    make_bridge(backfill_s=3600).tick(t + 300)  # same faults, same ids
    assert cloud.paths("POST").count("/v1/devices/pod-1/incidents") == 2
    assert len(cloud.incidents) == 1 and len(cloud.recordings) == 2
    assert cloud.paths("PUT").count(f"/v1/recordings/{next(iter(cloud.recordings))}/blob") == 1


def test_reports_wait_in_the_outbox_while_the_cloud_is_down(make_bridge, pod, sock, cloud):
    bridge = make_bridge()
    bridge.tick(T0)
    t = T0 + 60
    scenario_06_handed_off(pod, t)
    cloud.offline = True
    result = bridge.tick(t + 140)
    assert (result.checked_in, len(result.reported), result.sent) == (False, 1, 0)
    assert len(bridge.outbox) == 3
    cloud.offline = False
    result = bridge.tick(t + 400)  # past the outbox's backoff
    assert (result.checked_in, result.sent) == (True, 3)
    (incident,) = cloud.incidents.values()
    assert (incident["outcome"]["resolved"], incident["outcome"]["suspected_cause"]) == (
        False,
        "hardware",
    )


def test_faults_from_before_the_first_start_need_a_backfill(make_bridge, pod, cloud, tmp_path):
    t = T0 - 600
    scenario_01_fixed(pod, t)
    make_bridge().tick(T0)
    assert cloud.incidents == {}
    (tmp_path / "state" / "pod-1" / "state.json").unlink()
    make_bridge(backfill_s=3600).tick(T0 + 1)
    assert len(cloud.incidents) == 1


def test_podd_down_checks_in_failing_and_keeps_the_last_build(make_bridge, sock, cloud):
    bridge = make_bridge()
    sock.last_boot = _boot("v1.0", GOOD_CRC)
    bridge.tick(T0)
    sock.up = False
    result = bridge.tick(T0 + 10)
    assert result.health == "failing"
    last = cloud.checkins["pod-1"][-1]
    assert last["health_detail"]["check"] == "podd_unreachable"
    assert last["running"]["mcu_build"] == "v1.0@1A2B3C4D"


def test_without_a_boot_in_podd_the_build_comes_from_the_logs(make_bridge, pod, sock, cloud):
    pod.boot(T0 - 3600, "v1.0", GOOD_CRC)
    sock.last_boot = None  # podd restarted since the pod last booted
    make_bridge().tick(T0)
    assert cloud.checkins["pod-1"][0]["running"]["mcu_build"] == "v1.0@1A2B3C4D"


def test_an_open_fault_is_reported_when_it_times_out(make_bridge, pod, cloud):
    bridge = make_bridge(timing=Timing(max_open_s=120))
    bridge.tick(T0)
    pod.open("us_no_echo", T0 + 10, severity="warn")
    assert bridge.tick(T0 + 129).reported == []
    assert len(bridge.tick(T0 + 130).reported) == 1
    (incident,) = cloud.incidents.values()
    assert incident["outcome"]["resolved"] is False
    assert incident["bridge"]["reason"] == "timed_out"


def test_a_cloud_that_assigns_arm_firmware_gets_one_warning(make_bridge, cloud, caplog):
    cloud.assign("pod-1", firmware=None, harness=1)  # harness isn't per class: no warning
    with caplog.at_level(logging.WARNING, logger="edge_bridge.bridge"):
        make_bridge().tick(T0)
    assert caplog.records == []
    cloud.assign("pod-1", firmware=1, harness=1)
    bridge = make_bridge()
    with caplog.at_level(logging.WARNING, logger="edge_bridge.bridge"):
        bridge.tick(T0)
        bridge.tick(T0 + 10)
    warnings = [r for r in caplog.records if "installs nothing" in r.getMessage()]
    assert len(warnings) == 1
    assert cloud.installs == []  # it never pulls or installs


def test_the_incident_id_is_stable_across_devices_and_runs():
    a = translate.incident_uuid("pod-1", "crash-1794105660")
    assert a == translate.incident_uuid("pod-1", "crash-1794105660")
    assert a != translate.incident_uuid("pod-2", "crash-1794105660")


def test_a_check_in_reply_it_cant_read_doesnt_stop_the_tick(make_bridge, pod):
    import httpx

    def garbled(request):
        if request.url.path.endswith("/checkin"):
            return httpx.Response(200, json={"unexpected": True})
        return httpx.Response(404, json={"error": "not_found", "detail": ""})

    http = httpx.Client(transport=httpx.MockTransport(garbled), base_url="http://cloud")
    result = make_bridge(http=http).tick(T0)
    assert result.checked_in is False


def test_the_cli_keeps_going_after_a_failed_tick(pod, tmp_path, monkeypatch):
    from edge_bridge import __main__ as cli
    from edge_bridge.bridge import Bridge

    calls = []

    def flaky(self, now):
        calls.append(now)
        if len(calls) == 1:
            raise OSError("disk full")

    monkeypatch.setattr(Bridge, "tick", flaky)
    cli.main(
        [
            "--healer",
            str(pod.healer),
            "--state-dir",
            str(tmp_path / "s"),
            "--url",
            "http://127.0.0.1:9",
            "--interval",
            "0",
            "--ticks",
            "2",
        ]
    )
    assert len(calls) == 2
