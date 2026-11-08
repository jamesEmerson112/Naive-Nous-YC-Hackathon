"""services/batch, gate and fleet: the logic behind M3 that the flow tests don't pin down."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from romulus_cloud import worker
from romulus_cloud.adapters.blobstore import build_blobstore
from romulus_cloud.adapters.coder import FakeCoder
from romulus_cloud.adapters.llm import FakeLlm
from romulus_cloud.models import Candidate, Device, Event, FleetState, Incident, Overlay
from romulus_cloud.services import batch, devices, fleet, gate, registry
from romulus_cloud.services.seed import FIRMWARE_V1, seed
from romulus_contracts import CheckIn

T0 = datetime(2026, 11, 7, 22, 0, tzinfo=UTC)
D1_PATCH = """\
--- a/control/hold.py
+++ b/control/hold.py
@@ -1,2 +1,1 @@
-x = 1
 y = 2
"""


@pytest.fixture
def tools(settings):
    return batch.Tools(settings, FakeLlm(), FakeCoder(), build_blobstore(settings.blob_dir))


@pytest.fixture
def db(db_session, settings):
    seed(db_session, build_blobstore(settings.blob_dir))
    db_session.flush()
    return db_session


def _count(session, kind):
    return session.scalar(select(func.count()).select_from(Event).where(Event.kind == kind))


def _held(session, device="sim-1", diff=None, code_patch=None, base=1, signals=None):
    """A held overlay and its incident, as M1 stores them."""
    iid = str(uuid.uuid4())
    session.add(
        Incident(
            id=iid,
            device_id=device,
            occurred_at=T0,
            received_at=T0,
            kind="camera_occluded",
            resolving_action="safe_pose",
            signals=signals or {"brightness": [0.41, 0.18]},
            running={"firmware": base, "harness": 1, "code_rev": "c0de001"},
        )
    )
    ov = Overlay(
        device_id=device,
        id=f"ov-{iid[:6]}",
        incident_id=iid,
        base_firmware=base,
        base_code_rev="c0de001",
        diff=diff if diff is not None else {"rules": {"camera_occluded": {"below": 0.35}}},
        code_patch=code_patch,
        status="held",
        occurred_at=T0,
        received_at=T0,
    )
    session.add(ov)
    session.flush()
    return ov


# --- fleet ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("by", "actor"),
    [
        ("kent", "human:kent"),
        ("human:kent", "human:kent"),
        (None, "human:operator"),
        ("  ", "human:operator"),
    ],
)
def test_human_actor(by, actor):
    assert fleet.human_actor(by) == actor


def test_freeze_and_unfreeze_are_idempotent(db):
    assert fleet.freeze(db, "kent", "why", T0).changed
    again = fleet.freeze(db, "ann", "again", T0)
    assert not again.changed and "already frozen by human:kent: why" in again.why
    assert _count(db, "fleet.frozen") == 1
    assert fleet.unfreeze(db, None, "", T0).changed
    assert not fleet.unfreeze(db, None, "", T0).changed
    assert _count(db, "fleet.unfrozen") == 1
    row = db.get(FleetState, "default")
    assert row.frozen is False and row.frozen_by is None


def test_freeze_pins_to_last_good_and_leaves_devices_without_one(db):
    sim1 = db.get(Device, "sim-1")
    sim1.assigned_firmware, sim1.last_good_firmware, sim1.last_good_harness = 5, 1, 1
    result = fleet.freeze(db, "kent", "test", T0)
    assert sim1.assigned_firmware == 1
    assert [p["device"] for p in result.pinned] == ["sim-1"]
    assert db.get(Device, "arm-1").assigned_firmware == 1  # no last good: unchanged


def test_check_in_records_canary_history_only_for_canaries(db):
    body = CheckIn.model_validate({"running": None, "health": "ok", "contract_version": "0.1"})
    devices.check_in(db, "sim-2", body, {}, T0)
    devices.check_in(db, "sim-1", body, {}, T0)
    from romulus_cloud.models import CanaryCheckin

    rows = db.scalars(select(CanaryCheckin)).all()
    assert [(r.device_id, r.health) for r in rows] == [("sim-2", "ok")]


def test_locked_in_overlay_with_a_candidate_is_not_dropped(db):
    ov = _held(db)
    db.get(Device, "sim-1").overlay_id = ov.id
    ov.status, ov.candidate_id = "locked_in", "cand_x"
    body = CheckIn.model_validate(
        {"running": None, "health": "ok", "contract_version": "0.1", "overlay_id": ov.id}
    )
    assert devices.check_in(db, "sim-1", body, {}, T0).drop_overlay is False
    ov.status = "dropped"
    assert devices.check_in(db, "sim-1", body, {}, T0).drop_overlay is True


# --- batch ---------------------------------------------------------------------------------


def test_batch_is_due_on_first_run_interval_or_request(db):
    assert batch.is_due(db, T0, 120, "default")  # never ran
    fleet.state(db).batch_last_run_at = T0
    assert not batch.is_due(db, T0 + timedelta(seconds=119), 120, "default")
    assert batch.is_due(db, T0 + timedelta(seconds=120), 120, "default")
    fleet.request_batch(db, "kent", T0)
    assert batch.is_due(db, T0 + timedelta(seconds=1), 120, "default")


def test_timer_run_with_nothing_held_writes_no_event(db, tools):
    result = batch.maybe_run(db, T0, tools=tools)
    assert result.ran and not result.candidates
    assert _count(db, "batch.ran") == 0
    fleet.request_batch(db, "kent", T0)
    batch.maybe_run(db, T0 + timedelta(seconds=1), tools=tools)
    assert _count(db, "batch.ran") == 1
    assert fleet.state(db).batch_requested is False
    assert not batch.maybe_run(db, T0 + timedelta(seconds=2), tools=tools).ran


def test_overlay_against_a_moved_base_is_dropped_with_the_conflict(db, tools):
    """A lost update: the class moved to v2, which changed the rule the overlay patches."""
    kw = {"actor": "human:test", "actor_class": "human"}
    v2 = FIRMWARE_V1 | {
        "version": 2,
        "rules": FIRMWARE_V1["rules"]
        | {"camera_occluded": {"signal": "brightness", "below": 0.3, "action": "safe_pose"}},
    }
    registry.create_firmware(db, v2, **kw)
    for d in devices.list_devices(db):
        d.assigned_firmware = 2
    ov = _held(db, base=1)
    result = batch.run(db, T0, tools)
    assert not result.candidates and result.dropped == [("sim-1", ov.id)]
    assert ov.status == "dropped"
    (ev,) = db.scalars(select(Event).where(Event.kind == "overlay.dropped")).all()
    assert "assigned v2 changed rules.camera_occluded since the overlay's base v1" in ev.why
    assert ev.refs["incident"] == ov.incident_id


def test_unmergeable_diff_is_dropped(db, tools):
    ov = _held(db, diff={"rules": {"no_such_rule": None}})
    assert not batch.run(db, T0, tools).candidates
    assert ov.status == "dropped" and "doesn't have it" in ov.status_reason


def test_code_patch_touching_a_safety_path_is_flagged(db, make_settings):
    settings = make_settings(safety_paths=["control"])
    tools = batch.Tools(settings, FakeLlm(), FakeCoder(), build_blobstore(settings.blob_dir))
    _held(db, diff={}, code_patch=D1_PATCH)
    made = {c.type: c for c in batch.run(db, T0, tools).candidates}
    assert made["code"].touches_safety and not made["harness"].touches_safety
    created = db.scalars(
        select(Event).where(
            Event.kind == "candidate.created", Event.refs.contains({"candidate": made["code"].id})
        )
    ).one()
    assert created.refs["touches_safety"] is True and "K17" in created.why
    gate.tick(db, T0, settings=settings)
    waiting = db.scalars(
        select(Event).where(
            Event.kind == "candidate.awaiting_canary",
            Event.refs.contains({"candidate": made["code"].id}),
        )
    ).one()
    assert "[touches safety layer or visor; K17]" in waiting.why


# --- gate ----------------------------------------------------------------------------------


def test_second_firmware_candidate_waits_while_the_canary_is_busy(db, tools, settings):
    _held(db, device="sim-1")
    batch.run(db, T0, tools)
    gate.tick(db, T0, settings=settings)
    _held(db, device="sim-3", diff={"params": {"max_speed": 0.5}})
    batch.run(db, T0 + timedelta(seconds=1), tools)
    gate.tick(db, T0 + timedelta(seconds=1), settings=settings)
    fws = db.scalars(
        select(Candidate).where(Candidate.type == "firmware").order_by(Candidate.created_at)
    ).all()
    assert [c.status for c in fws] == ["canary", "awaiting_canary"]
    assert db.get(Device, "sim-2").assigned_firmware == fws[0].version


def test_replay_that_fails_rolls_back_without_touching_a_device(db, tools, settings):
    # The incident's signals never cross the new threshold: the fix can't fire.
    _held(db, signals={"brightness": [0.9, 0.8]})
    batch.run(db, T0, tools)
    gate.tick(db, T0, settings=settings)
    fw = db.scalars(select(Candidate).where(Candidate.type == "firmware")).one()
    assert fw.status == "rolled_back" and fw.canary_device is None
    assert db.get(Device, "sim-2").assigned_firmware == 1
    hv = db.scalars(select(Candidate).where(Candidate.type == "harness")).one()
    assert hv.status == "rolled_back"  # its sibling went with it


def test_a_waiting_candidate_built_on_an_old_version_goes_stale(db, tools, settings):
    _held(db, device="sim-1")
    batch.run(db, T0, tools)
    gate.tick(db, T0, settings=settings)
    _held(db, device="sim-3", diff={"params": {"max_speed": 0.5}})
    batch.run(db, T0 + timedelta(seconds=1), tools)
    first, second = db.scalars(
        select(Candidate).where(Candidate.type == "firmware").order_by(Candidate.created_at)
    ).all()
    for d in devices.list_devices(db):  # what promoting the first one does
        d.assigned_firmware = first.version
    gate.tick(db, T0 + timedelta(seconds=2), settings=settings)
    assert second.status == "rolled_back"
    assert second.verdict["why"].startswith("stale: built on firmware 1, but the so101 fleet")


def test_worker_runs_the_gate_before_the_batch():
    names = [s.name for s in worker.STEPS]
    assert names.index("gate.tick") < names.index("batch.maybe_run")
