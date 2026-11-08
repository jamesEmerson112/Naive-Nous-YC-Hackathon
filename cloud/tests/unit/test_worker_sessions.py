"""Flow tests mix requests and worker ticks; both must share the test's transaction."""

from sqlalchemy import select

from romulus_cloud.api.deps import SessionDep
from romulus_cloud.models import Device
from romulus_cloud.worker import Step, run_tick


def test_worker_step_sees_rows_written_through_the_api(app, client, sessions, now):
    @app.post("/test-only/devices/{device_id}")
    def _add(device_id: str, session: SessionDep):
        session.add(Device(id=device_id, kind="sim"))
        session.commit()
        return {"ok": True}

    assert client.post("/test-only/devices/sim-7").status_code == 200

    seen = []

    def read_devices(session, now):
        seen.extend(session.scalars(select(Device.id).where(Device.id == "sim-7")))
        session.add(Device(id="sim-8", kind="sim"))  # a worker write, committed by run_tick

    assert run_tick([Step("read", read_devices)], sessions, now, {}) == {"read": "ok"}
    assert seen == ["sim-7"]
    with sessions() as s:
        assert s.get(Device, "sim-8") is not None


def test_nothing_from_the_previous_test_was_committed(db_session):
    assert db_session.get(Device, "sim-7") is None
    assert db_session.get(Device, "sim-8") is None
