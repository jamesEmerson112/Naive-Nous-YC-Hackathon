import json

import pytest
from sqlalchemy.exc import IntegrityError

from romulus_cloud.models import Device, Overlay, WorkOrder


def _devices(session, *ids):
    for device_id in ids:
        session.add(Device(id=device_id, kind="sim"))
    session.flush()


def test_two_devices_can_both_send_ov_1(db_session, now):
    """Overlay ids are per-device counters, so the key is (device_id, id)."""
    _devices(db_session, "sim-1", "sim-2")
    db_session.add(Overlay(device_id="sim-1", id="ov-1", received_at=now, diff={"a": 1}))
    db_session.add(Overlay(device_id="sim-2", id="ov-1", received_at=now, diff={"b": 2}))
    db_session.flush()
    db_session.expire_all()
    assert db_session.get(Overlay, ("sim-1", "ov-1")).diff == {"a": 1}
    assert db_session.get(Overlay, ("sim-2", "ov-1")).diff == {"b": 2}


def test_same_device_same_overlay_id_is_rejected(db_session, now):
    _devices(db_session, "sim-1")
    db_session.add(Overlay(device_id="sim-1", id="ov-1", received_at=now))
    db_session.flush()
    nested = db_session.begin_nested()
    db_session.add(Overlay(device_id="sim-1", id="ov-1", received_at=now))
    with pytest.raises(IntegrityError):
        db_session.flush()
    nested.rollback()


def test_max_usd_reads_back_as_a_json_safe_float(db_session):
    db_session.add(
        WorkOrder(id="wo_1", incident_id="i-1", device_id="arm-1", part="servo", max_usd=40.5)
    )
    db_session.flush()
    db_session.expire_all()
    wo = db_session.get(WorkOrder, "wo_1")
    assert isinstance(wo.max_usd, float) and wo.max_usd == 40.5
    assert json.loads(json.dumps({"max_usd": wo.max_usd})) == {"max_usd": 40.5}


def test_one_work_order_per_incident(db_session):
    db_session.add(
        WorkOrder(id="wo_1", incident_id="i-1", device_id="arm-1", part="servo", max_usd=40)
    )
    db_session.flush()
    nested = db_session.begin_nested()
    db_session.add(
        WorkOrder(id="wo_2", incident_id="i-1", device_id="arm-1", part="servo", max_usd=40)
    )
    with pytest.raises(IntegrityError):
        db_session.flush()
    nested.rollback()
