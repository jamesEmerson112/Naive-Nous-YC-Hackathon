"""Two copies of a first check-in, or of one incident, racing in on two connections.

These commit for real (two transactions can't share the test's rolled-back one), so each
test uses its own ids and deletes its rows afterwards.
"""

import threading
import time
import uuid

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from romulus_cloud.models import Device, Event, Incident
from romulus_cloud.services import devices, incidents
from romulus_contracts import CheckIn, IncidentReport


def _race(engine, first, second):
    """Run `first` and hold its transaction open; start `second`, which must block on the
    same row; then commit `first`. Returns the second call's result or exception."""
    out: dict = {}
    sa, sb = Session(engine), Session(engine)
    try:
        first(sa)

        def run_second():
            try:
                out["value"] = second(sb)
                sb.commit()
            except Exception as exc:  # noqa: BLE001 (the test inspects it)
                out["error"] = exc
                sb.rollback()

        t = threading.Thread(target=run_second)
        t.start()
        time.sleep(0.3)  # let the second insert reach the row lock
        sa.commit()
        t.join(timeout=10)
        assert not t.is_alive()
        return out
    finally:
        sa.close()
        sb.close()


def _cleanup(engine, device_id):
    with Session(engine) as s:
        s.execute(delete(Event).where(Event.refs.contains({"device": device_id})))
        s.execute(delete(Incident).where(Incident.device_id == device_id))
        s.execute(delete(Device).where(Device.id == device_id))
        s.commit()


def test_two_first_checkins_race_without_error(engine, now):
    device_id = f"race-{uuid.uuid4().hex[:8]}"
    body = CheckIn(running=None, health="ok", contract_version="0.1")
    try:
        out = _race(
            engine,
            lambda s: devices.check_in(s, device_id, body, {}, now),
            lambda s: devices.check_in(s, device_id, body, {}, now),
        )
        assert "error" not in out, out.get("error")
        with Session(engine) as s:
            assert s.get(Device, device_id) is not None
            first_seen = select(func.count()).where(
                Event.kind == "device.first_seen", Event.refs.contains({"device": device_id})
            )
            assert s.scalar(first_seen) == 1
    finally:
        _cleanup(engine, device_id)


def test_two_copies_of_one_incident_race_without_error(engine, now):
    device_id = f"race-{uuid.uuid4().hex[:8]}"
    with Session(engine) as s:
        s.add(Device(id=device_id))
        s.commit()
    report = IncidentReport(
        incident_id=uuid.uuid4(), occurred_at=now, kind="camera_occluded", summary="dark"
    )
    try:
        out = _race(
            engine,
            lambda s: incidents.report(s, device_id, report, {}, now, safety_paths=[]),
            lambda s: incidents.report(s, device_id, report, {}, now, safety_paths=[])[1],
        )
        assert "error" not in out, out.get("error")
        assert out["value"] is False  # the second copy is answered as a duplicate
        with Session(engine) as s:
            reported = select(func.count()).where(
                Event.kind == "incident.reported", Event.refs.contains({"device": device_id})
            )
            assert s.scalar(reported) == 1
    finally:
        _cleanup(engine, device_id)
