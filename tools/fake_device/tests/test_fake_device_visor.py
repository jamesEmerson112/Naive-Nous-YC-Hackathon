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
    assert installs(cloud) == [("firmware", 1, "hash_mismatch")]
    assert "sha256 mismatch" in cloud.installs[0][1]["detail"]
    assert arm.device.firmware is None and arm.visor.last_good["firmware"] is None

    arm.tick(T0 + timedelta(seconds=10))  # same assignment: not pulled or reported again
    assert len(cloud.installs) == 1
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
    arm.device.bad_versions.add(2)

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
