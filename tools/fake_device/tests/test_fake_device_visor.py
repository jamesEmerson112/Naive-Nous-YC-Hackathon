"""Visor: pull, verify, install, health check, roll back; frozen; offline (D7, D8, flow F1)."""

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from fake_device.arm import FakeArm
from fake_device.fake_cloud import V1_MANIFEST, FakeCloud, tarball
from romulus_contracts import Overlay

T0 = datetime(2026, 11, 7, 22, 0, tzinfo=UTC)


def v2_manifest(code_rev: str = "c0de001") -> dict:
    return {**V1_MANIFEST, "version": 2, "code_rev": code_rev}


@pytest.fixture
def cloud() -> FakeCloud:
    return FakeCloud.seeded()


@pytest.fixture
def arm(cloud, tmp_path) -> FakeArm:
    return FakeArm(cloud.http(), "sim-1", tmp_path)


def installs(cloud: FakeCloud) -> list[tuple]:
    return [(b["type"], b["version"], b["result"]) for _, b in cloud.installs]


def test_first_boot_installs_assigned_versions(cloud, arm):
    """F1: first boot pulls firmware, code and harness, reports installed, runs what is assigned."""
    result = arm.tick(T0)
    assert result.online
    assert cloud.paths() == [
        "/v1/devices/sim-1/checkin",
        "/v1/firmware/1",
        "/v1/code/c0de001/package",
        "/v1/harness/1/package",
        "/v1/devices/sim-1/installs",
        "/v1/devices/sim-1/installs",
        "/v1/devices/sim-1/installs",
    ]
    assert installs(cloud) == [
        ("firmware", 1, "installed"),
        ("code", "c0de001", "installed"),
        ("harness", 1, "installed"),
    ]
    assert arm.visor.last_good == {"firmware": 1, "code": "c0de001", "harness": 1}

    arm.tick(T0 + timedelta(seconds=10))
    running = cloud.checkins["sim-1"][-1]["running"]
    assert (running["firmware"], running["harness"], running["code_rev"]) == (1, 1, "c0de001")
    assert cloud.paths()[-1] == "/v1/devices/sim-1/checkin"  # nothing more to pull


def test_hash_mismatch_is_reported_and_not_installed(cloud, arm):
    cloud.corrupt.add("/v1/firmware/1")
    arm.tick(T0)
    # The code needs that firmware, so it waits; the harness doesn't, so it installs.
    assert installs(cloud) == [("firmware", 1, "hash_mismatch"), ("harness", 1, "installed")]
    assert "sha256 mismatch" in cloud.installs[0][1]["detail"]
    assert arm.device.firmware is None and arm.visor.last_good["firmware"] is None
    assert arm.device.code_rev is None

    arm.tick(T0 + timedelta(seconds=10))  # same assignment: not pulled or reported again
    assert len(cloud.installs) == 2
    assert cloud.paths().count("/v1/firmware/1") == 1


def test_invalid_manifest_is_reported_and_not_installed(cloud, arm):
    arm.tick(T0)
    cloud.add_firmware({**V1_MANIFEST, "version": 2, "rules": {"r": {"signal": "x"}}})
    cloud.assign("sim-1", firmware=2, harness=1)
    arm.tick(T0 + timedelta(seconds=10))
    assert installs(cloud)[-1] == ("firmware", 2, "invalid")
    assert arm.device.firmware == 1
    arm.tick(T0 + timedelta(seconds=20))
    assert cloud.paths().count("/v1/firmware/2") == 1  # not retried


def test_harness_hash_mismatch(cloud, arm):
    cloud.corrupt.add("/v1/harness/1/package")
    arm.tick(T0)
    assert installs(cloud)[-1] == ("harness", 1, "hash_mismatch")
    assert arm.device.harness is None and arm.device.firmware == 1


def test_failed_health_rolls_back_to_last_good(cloud, arm):
    arm.tick(T0)
    cloud.add_firmware(v2_manifest())
    cloud.assign("sim-1", firmware=2, harness=1)
    arm.device.bad_versions[2] = "install"

    arm.tick(T0 + timedelta(seconds=10))
    assert installs(cloud)[-1] == ("firmware", 2, "rolled_back")
    assert "back on firmware 1" in cloud.installs[-1][1]["detail"]
    assert arm.device.firmware == 1 and arm.device.manifest.version == 1
    assert arm.visor.last_good["firmware"] == 1

    arm.tick(T0 + timedelta(seconds=20))  # the bad version isn't retried
    assert len(cloud.installs) == 4
    assert cloud.checkins["sim-1"][-1]["running"]["firmware"] == 1
    assert cloud.checkins["sim-1"][-1]["health"] == "ok"


def test_new_firmware_with_new_code_rev_installs_code_too(cloud, arm):
    arm.tick(T0)
    cloud.add_firmware(v2_manifest(code_rev="c0de002"))
    cloud.add_code("c0de002", tarball({"device/README.md": b"v2"}))
    cloud.assign("sim-1", firmware=2, harness=1)
    arm.tick(T0 + timedelta(seconds=10))
    assert installs(cloud)[-2:] == [("firmware", 2, "installed"), ("code", "c0de002", "installed")]
    assert arm.device.code_rev == "c0de002"


def test_install_drops_the_active_overlay(cloud, arm):
    arm.tick(T0)
    arm.device.apply_overlay(Overlay(overlay_id="ov-1", diff={}))
    cloud.add_firmware(v2_manifest())
    cloud.assign("sim-1", firmware=2, harness=1)
    arm.tick(T0 + timedelta(seconds=10))
    assert arm.device.overlay is None  # D7: a new version replaces the overlay


def test_drop_overlay_from_checkin(cloud, arm):
    arm.tick(T0)
    arm.device.apply_overlay(Overlay(overlay_id="ov-1", diff={}))
    cloud.drop_overlay.add("sim-1")
    arm.tick(T0 + timedelta(seconds=10))
    assert arm.device.overlay is None
    arm.tick(T0 + timedelta(seconds=20))
    assert cloud.checkins["sim-1"][-1]["overlay_id"] is None


def test_frozen_blocks_new_versions(cloud, arm):
    arm.tick(T0)
    cloud.add_firmware(v2_manifest())
    cloud.assign("sim-1", firmware=2, harness=1)
    cloud.frozen = True
    arm.tick(T0 + timedelta(seconds=10))
    assert arm.device.firmware == 1 and "/v1/firmware/2" not in cloud.paths()

    cloud.frozen = False
    arm.tick(T0 + timedelta(seconds=20))
    assert arm.device.firmware == 2


def test_offline_keeps_running_and_queues_reports(cloud, arm):
    arm.tick(T0)
    cloud.offline = True
    result = arm.tick(T0 + timedelta(seconds=10))
    assert not result.online
    assert arm.device.firmware == 1 and arm.device.health() == "ok"  # D8: never bricked


def test_install_report_queued_while_cloud_drops_mid_tick(cloud, tmp_path):
    # Check-in and pulls succeed, then the install reports fail: they stay in the outbox.
    broken = {"installs": True}

    def handle(request: httpx.Request) -> httpx.Response:
        if broken["installs"] and request.url.path.endswith("/installs"):
            return httpx.Response(503, json={"error": "unavailable", "detail": ""})
        return cloud.handle(request)

    http = httpx.Client(transport=httpx.MockTransport(handle), base_url="http://cloud")
    arm = FakeArm(http, "sim-1", tmp_path)
    arm.tick(T0)
    assert arm.device.firmware == 1
    assert len(arm.outbox) == 3 and cloud.installs == []

    broken["installs"] = False
    arm.tick(T0 + timedelta(seconds=10))
    assert [r for _, _, r in installs(cloud)] == ["installed"] * 3
    assert len(arm.outbox) == 0


# -- review fixes: health baseline, independent harness, crash safety, overlay -------------


class Crash(BaseException):
    """Simulates the process dying: not an Exception, so nothing in the visor catches it."""


def test_harness_installs_while_an_overlay_is_under_watch(cloud, arm):
    arm.tick(T0)
    arm.device.apply_overlay(Overlay(overlay_id="ov-1", diff={}))
    assert arm.device.health() == "degraded"
    cloud.add_harness(2, tarball({"harness.json": b'{"version": 2, "framework": "pi"}'}))
    cloud.assign("sim-1", firmware=1, harness=2)
    arm.tick(T0 + timedelta(seconds=10))
    assert installs(cloud)[-1] == ("harness", 2, "installed")  # degraded -> degraded passes
    assert arm.device.overlay is not None  # a harness doesn't replace the overlay


def test_frozen_out_firmware_does_not_block_the_harness(cloud, arm):
    arm.tick(T0)
    cloud.add_firmware(v2_manifest())
    cloud.add_harness(2, tarball({"harness.json": b'{"version": 2, "framework": "pi"}'}))
    cloud.assign("sim-1", firmware=2, harness=2)
    cloud.corrupt.add("/v1/firmware/2")
    arm.tick(T0 + timedelta(seconds=10))
    assert installs(cloud)[-2:] == [("firmware", 2, "hash_mismatch"), ("harness", 2, "installed")]
    assert (arm.device.firmware, arm.device.harness) == (1, 2)


def test_install_that_raises_rolls_back(cloud, arm, monkeypatch):
    arm.tick(T0)
    cloud.add_harness(2, tarball({"harness.json": b'{"version": 2, "framework": "pi"}'}))
    cloud.assign("sim-1", firmware=1, harness=2)
    real_install = arm.device.install

    def broken(kind, version, payload):
        real_install(kind, version, payload)  # half-done: switched, then failed
        raise OSError("disk full")

    monkeypatch.setattr(arm.device, "install", broken)
    arm.tick(T0 + timedelta(seconds=10))
    assert installs(cloud)[-1] == ("harness", 2, "rolled_back")
    assert "disk full" in cloud.installs[-1][1]["detail"]
    assert arm.device.harness == 1

    restarted = FakeArm(cloud.http(), "sim-1", arm_dir(arm), device=arm.device)
    assert restarted.visor.last_good["harness"] == 1  # never trusted the failed version
    assert restarted.visor.pending is None


def arm_dir(arm: FakeArm):
    return arm.visor.state_path.parent


def crash_during_health_check(cloud, arm, monkeypatch, *, bad: bool):
    """Install firmware 2, then 'lose power' before its health check finishes."""
    arm.tick(T0)
    cloud.add_firmware(v2_manifest())
    cloud.assign("sim-1", firmware=2, harness=1)
    if bad:
        arm.device.bad_versions[2] = "install"

    real_check = arm.device.health_check
    calls = []

    def dies_after_install():
        calls.append(1)
        if len(calls) == 1:
            return real_check()  # the baseline taken before the install
        raise Crash()

    monkeypatch.setattr(arm.device, "health_check", dies_after_install)
    with pytest.raises(Crash):
        arm.tick(T0 + timedelta(seconds=10))
    monkeypatch.undo()
    assert arm.device.firmware == 2  # switched, but never verified
    # Restart: a new process on the same disk (state dir) and the same device.
    return FakeArm(cloud.http(), "sim-1", arm_dir(arm), device=arm.device)


def test_restart_after_crash_rolls_back_an_unhealthy_install(cloud, arm, monkeypatch):
    restarted = crash_during_health_check(cloud, arm, monkeypatch, bad=True)
    assert restarted.visor.pending == {"kind": "firmware", "version": 2, "health_before": "ok"}
    assert restarted.visor.last_good["firmware"] == 1
    restarted.tick(T0 + timedelta(seconds=20))
    assert restarted.device.firmware == 1
    assert installs(cloud)[-1] == ("firmware", 2, "rolled_back")
    assert "restart" in cloud.installs[-1][1]["detail"]
    assert restarted.visor.pending is None
    assert cloud.checkins["sim-1"][-1]["running"]["firmware"] == 1  # recovered before check-in


def test_restart_after_crash_keeps_a_healthy_install(cloud, arm, monkeypatch):
    restarted = crash_during_health_check(cloud, arm, monkeypatch, bad=False)
    restarted.tick(T0 + timedelta(seconds=20))
    assert restarted.device.firmware == 2
    assert restarted.visor.last_good["firmware"] == 2
    assert ("firmware", 2, "installed") in installs(cloud)


def test_last_good_survives_a_restart(cloud, arm):
    arm.tick(T0)
    restarted = FakeArm(cloud.http(), "sim-1", arm_dir(arm), device=arm.device)
    assert restarted.visor.last_good == {"firmware": 1, "code": "c0de001", "harness": 1}


def test_overlay_dropped_only_after_a_healthy_install_and_reported(cloud, arm):
    arm.tick(T0)
    arm.device.apply_overlay(Overlay(overlay_id="ov-1", diff={}))
    cloud.add_firmware(v2_manifest())
    cloud.assign("sim-1", firmware=2, harness=1)
    arm.tick(T0 + timedelta(seconds=10))
    assert arm.device.overlay is None
    device, overlay_id, status = cloud.overlays[-1]
    assert (overlay_id, status["status"], status["cause"]) == ("ov-1", "reverted", "superseded")
    assert "firmware 2" in status["reason"]


def test_overlay_kept_when_the_install_rolls_back(cloud, arm):
    arm.tick(T0)
    arm.device.apply_overlay(Overlay(overlay_id="ov-1", diff={}))
    cloud.add_firmware(v2_manifest())
    cloud.assign("sim-1", firmware=2, harness=1)
    arm.device.bad_versions[2] = "install"
    arm.tick(T0 + timedelta(seconds=10))
    assert installs(cloud)[-1] == ("firmware", 2, "rolled_back")
    assert arm.device.overlay is not None and arm.device.overlay.overlay_id == "ov-1"
    assert cloud.overlays == []
