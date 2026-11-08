"""services/seed.seed_bench: the real bench is arm-1 plus exactly one simulated canary, on the
device/ code registered from the repo (B1)."""

import pytest
from sqlalchemy import func, select

from romulus_cloud.adapters.blobstore import build_blobstore
from romulus_cloud.errors import ApiError
from romulus_cloud.models import Device, Event, FirmwareVersion
from romulus_cloud.services import devices, registry
from romulus_cloud.services.seed import CODE_SOURCE_REPO, seed, seed_bench

HUMAN = {"actor": "human:test", "actor_class": "human"}


@pytest.fixture
def store(settings):
    return build_blobstore(settings.blob_dir)


def _register(session, store, rev, body=b"x\n"):
    data = registry.build_tarball({"device/robot/app.py": body})
    registry.create_code(session, store, data, rev=rev, source=CODE_SOURCE_REPO, **HUMAN)
    session.flush()


def _events(session):
    return session.scalar(select(func.count()).select_from(Event))


def test_seeds_arm_1_and_one_sim_canary_on_the_registered_code(db_session, store):
    _register(db_session, store, "abc1234")
    result = seed_bench(db_session, store, None)
    db_session.flush()

    assert result.created == ["harness v1", "firmware v1 on code abc1234", "arm-1", "sim-1"]
    fw = db_session.get(FirmwareVersion, ("default", 1))
    assert fw.manifest["code_rev"] == "abc1234"
    rows = db_session.scalars(select(Device).order_by(Device.id)).all()
    assert [(d.id, d.kind, d.role, d.assigned_firmware, d.assigned_harness) for d in rows] == [
        ("arm-1", "real", "normal", 1, 1),
        ("sim-1", "sim", "canary", 1, 1),
    ]
    whys = db_session.scalars(select(Event.why).where(Event.kind == "device.registered")).all()
    assert all("make seed-bench" in why for why in whys)


def test_is_idempotent(db_session, store):
    _register(db_session, store, "abc1234")
    seed_bench(db_session, store, None)
    db_session.flush()
    before = _events(db_session)

    again = seed_bench(db_session, store, None)
    db_session.flush()

    assert again.created == [] and again.updated == []
    assert again.existing == ["harness v1", "firmware v1 on code abc1234", "arm-1", "sim-1"]
    assert _events(db_session) == before


def test_new_device_code_gets_a_new_firmware_version_and_moves_both_devices(db_session, store):
    _register(db_session, store, "abc1234")
    seed_bench(db_session, store, None)
    _register(db_session, store, "def5678", body=b"y\n")

    result = seed_bench(db_session, store, "def5678")
    db_session.flush()

    assert result.created == ["firmware v2 on code def5678"]
    assert result.updated == ["arm-1", "sim-1"]
    assert {d.assigned_firmware for d in db_session.scalars(select(Device))} == {2}


def test_a_device_that_checked_in_first_gets_the_bench_kind_and_role(db_session, store):
    _register(db_session, store, "abc1234")
    devices.register(db_session, "arm-1", kind="unknown", **HUMAN)
    db_session.flush()

    result = seed_bench(db_session, store, "abc1234")
    db_session.flush()

    assert result.updated == ["arm-1"] and result.created[-1] == "sim-1"
    arm = db_session.get(Device, "arm-1")
    assert (arm.kind, arm.role, arm.assigned_firmware) == ("real", "normal", 1)


def test_refuses_without_registered_device_code(db_session, store):
    with pytest.raises(ApiError) as missing:
        seed_bench(db_session, store, None)
    assert missing.value.error == "no_device_code"
    with pytest.raises(ApiError) as unknown:
        seed_bench(db_session, store, "nope123")
    assert unknown.value.error == "unknown_code_rev"
    assert db_session.scalar(select(func.count()).select_from(Device)) == 0


def test_refuses_a_second_canary(db_session, store):
    seed(db_session, store)  # the dev fleet: sim-2 is its canary
    _register(db_session, store, "abc1234")
    with pytest.raises(ApiError) as exc:
        seed_bench(db_session, store, None)
    assert exc.value.error == "other_canary"
    assert "sim-2" in exc.value.detail
