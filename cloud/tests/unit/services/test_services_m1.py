"""services/registry, devices, incidents and seed: the logic behind the M1 routes."""

import json

import pytest
from sqlalchemy import func, select

from romulus_cloud import events
from romulus_cloud.adapters.blobstore import build_blobstore
from romulus_cloud.errors import ApiError
from romulus_cloud.models import Device, Event, FleetState, Overlay
from romulus_cloud.services import devices, incidents, registry
from romulus_cloud.services.seed import FIRMWARE_V1, seed
from romulus_contracts import CheckIn, IncidentReport, OverlayStatus

HUMAN = {"actor": "human:test", "actor_class": "human"}


@pytest.fixture
def store(settings):
    return build_blobstore(settings.blob_dir)


@pytest.fixture
def fleet(db_session, store):
    seed(db_session, store)
    db_session.flush()
    return db_session


def _checkin(**over):
    body = {"running": None, "health": "ok", "contract_version": "0.1", **over}
    return CheckIn.model_validate(body), body


def _event_count(session, kind=None):
    stmt = select(func.count()).select_from(Event)
    if kind:
        stmt = stmt.where(Event.kind == kind)
    return session.scalar(stmt)


# --- registry ------------------------------------------------------------------------------


def test_build_tarball_is_deterministic():
    files = {"b.txt": b"2", "a.txt": b"1"}
    assert registry.build_tarball(files) == registry.build_tarball(dict(reversed(files.items())))


def test_read_harness_meta_errors():
    with pytest.raises(ApiError, match="no harness.json"):
        registry.read_harness_meta(registry.build_tarball({"x.md": b""}))
    with pytest.raises(ApiError, match="not valid JSON"):
        registry.read_harness_meta(registry.build_tarball({"harness.json": b"{"}))
    with pytest.raises(ApiError, match="not a readable"):
        registry.read_harness_meta(b"not a tarball")
    bad_dir = json.dumps({"version": 3, "framework": "x", "knowledge_dir": "../up"}).encode()
    with pytest.raises(ApiError, match="harness.json rejected"):
        registry.read_harness_meta(registry.build_tarball({"harness.json": bad_dir}))
    ok = json.dumps({"version": 3, "framework": "pi"}).encode()
    assert registry.read_harness_meta(registry.build_tarball({"./harness.json": ok})).version == 3


def test_create_firmware_refuses_duplicates_unknown_code_and_bad_rules(fleet):
    with pytest.raises(ApiError) as dup:
        registry.create_firmware(fleet, FIRMWARE_V1, **HUMAN)
    assert dup.value.status == 409
    with pytest.raises(ApiError) as code:
        registry.create_firmware(fleet, FIRMWARE_V1 | {"version": 2, "code_rev": "zzz"}, **HUMAN)
    assert code.value.error == "unknown_code_rev"
    bad_rules = FIRMWARE_V1 | {"version": 2, "rules": {"x": {"signal": "s", "action": "a"}}}
    with pytest.raises(ApiError) as bad:
        registry.create_firmware(fleet, bad_rules, **HUMAN)
    assert bad.value.status == 422


def test_create_code_same_bytes_is_a_noop_other_bytes_conflict(fleet, store):
    data = registry.build_tarball({"device/a.py": b"1"})
    row, made = registry.create_code(fleet, store, data, rev=None, **HUMAN)
    assert made and row.rev == row.sha256[:7] and store.exists(row.sha256)
    assert registry.create_code(fleet, store, data, rev=row.rev, **HUMAN) == (row, False)
    other = registry.build_tarball({"device/a.py": b"2"})
    with pytest.raises(ApiError) as exc:
        registry.create_code(fleet, store, other, rev=row.rev, **HUMAN)
    assert exc.value.status == 409


def test_default_assignment_follows_the_class_not_the_newest_version(fleet):
    """A candidate (v2, assigned only to the canary) must not go to a new device."""
    registry.create_firmware(fleet, FIRMWARE_V1 | {"version": 2}, **HUMAN)
    devices.update(fleet, "sim-2", firmware=2, **HUMAN)
    assert registry.default_assignment(fleet, "so101") == (1, 1)


def test_default_assignment_with_no_devices_is_the_newest(db_session, store):
    assert registry.default_assignment(db_session, "so101") == (None, None)
    registry.create_code(db_session, store, registry.build_tarball({}), rev="c0de001", **HUMAN)
    registry.create_firmware(db_session, FIRMWARE_V1, **HUMAN)
    registry.create_firmware(db_session, FIRMWARE_V1 | {"version": 4}, **HUMAN)
    assert registry.default_assignment(db_session, "so101") == (4, None)


def test_retired_versions_cannot_be_assigned(fleet):
    registry.get_firmware(fleet, 1).retired = True
    with pytest.raises(ApiError) as exc:
        devices.update(fleet, "arm-1", firmware=1, **HUMAN)
    assert exc.value.error == "retired"


# --- seed ----------------------------------------------------------------------------------


def test_seed_is_idempotent(db_session, store):
    first = seed(db_session, store)
    assert first.created == [
        "code c0de001",
        "harness v1",
        "firmware v1",
        "arm-1",
        "sim-1",
        "sim-2",
        "sim-3",
    ]
    n = _event_count(db_session)
    second = seed(db_session, store)
    assert second.created == [] and len(second.existing) == 7
    assert _event_count(db_session) == n
    canary = db_session.get(Device, "sim-2")
    assert canary.role == "canary" and canary.kind == "sim"
    assert (canary.assigned_firmware, canary.assigned_harness) == (1, 1)
    assert all(e.why and e.actor_class == "human" for e in db_session.scalars(select(Event)))


# --- devices -------------------------------------------------------------------------------


def test_check_in_reports_frozen_and_drop_overlay(fleet, now):
    fleet.add(FleetState(fleet_id="default", frozen=True))
    fleet.add(
        Overlay(
            device_id="arm-1", id="ov-1", fleet_id="default", status="locked_in", received_at=now
        )
    )
    body, raw = _checkin(overlay_id="ov-1")
    resp = devices.check_in(fleet, "arm-1", body, raw, now)
    assert resp.frozen is True and resp.drop_overlay is True
    body, raw = _checkin(overlay_id=None)
    assert devices.check_in(fleet, "arm-1", body, raw, now).drop_overlay is False


def test_check_in_assigns_a_device_that_has_nothing(fleet, now):
    fleet.add(Device(id="bare", fleet_id="default", last_seen_at=now))
    fleet.flush()
    resp = devices.check_in(fleet, "bare", *_checkin(), now)
    assert (resp.assigned.firmware, resp.assigned.harness) == (1, 1)
    (ev,) = events.find(fleet, {"device": "bare"}, kinds=["device.assigned"])
    assert ev.actor == "registry" and "class default" in ev.why


def test_check_in_records_only_changes(fleet, now):
    devices.check_in(fleet, "arm-1", *_checkin(), now)  # first sight
    n = _event_count(fleet)
    devices.check_in(fleet, "arm-1", *_checkin(), now)
    assert _event_count(fleet) == n
    running = {"firmware": 1, "harness": 1, "code_rev": "c0de001"}
    devices.check_in(fleet, "arm-1", *_checkin(running=running, health="failing"), now)
    assert _event_count(fleet, "device.running_changed") == 1
    (health,) = events.find(fleet, {"device": "arm-1"}, kinds=["device.health_changed"])
    assert health.why == "arm-1 health went from ok to failing."


def test_register_rejects_bad_values_and_duplicates(fleet):
    with pytest.raises(ApiError):
        devices.register(fleet, "x", kind="robot", **HUMAN)
    with pytest.raises(ApiError):
        devices.register(fleet, "x", role="boss", **HUMAN)
    with pytest.raises(ApiError) as exc:
        devices.register(fleet, "arm-1", **HUMAN)
    assert exc.value.status == 409


def test_update_role_records_an_event(fleet):
    devices.update(fleet, "sim-1", role="canary", why="Second canary for tonight.", **HUMAN)
    (ev,) = events.find(fleet, {"device": "sim-1"}, kinds=["device.updated"])
    assert ev.why == "human:test set sim-1 role to canary. Second canary for tonight."
    assert devices.update(fleet, "sim-1", role="canary", **HUMAN).role == "canary"
    assert len(events.find(fleet, {"device": "sim-1"}, kinds=["device.updated"])) == 1


# --- incidents and overlays ----------------------------------------------------------------


def _incident(**over):
    raw = {
        "incident_id": "11111111-2222-4333-8444-555555555555",
        "occurred_at": "2026-11-07T22:00:00Z",
        "kind": "camera_occluded",
        "overlay": {"overlay_id": "ov-1", "diff": {"params": {"max_speed": 0.4}}},
        **over,
    }
    return IncidentReport.model_validate(raw), raw


def test_incident_without_agent_or_outcome_is_unresolved_unknown(fleet, now):
    incident, created = incidents.report(fleet, "arm-1", *_incident(), now, safety_paths=[])
    assert created and incident.resolved is False and incident.suspected_cause == "unknown"
    _, story = incidents.story(fleet, incident.id)
    assert [step.kind for step in story.steps] == ["incident.reported", "overlay.applied"]


def test_bad_code_patch_fails_closed_to_touches_safety(fleet, now):
    overlay = {"overlay_id": "ov-1", "code_patch": "not a diff at all"}
    incidents.report(fleet, "arm-1", *_incident(overlay=overlay), now, safety_paths=["visor"])
    assert fleet.get(Overlay, ("arm-1", "ov-1")).touches_safety is True


def test_late_held_never_undoes_a_cloud_status(fleet, now):
    incidents.report(fleet, "arm-1", *_incident(), now, safety_paths=[])
    fleet.get(Overlay, ("arm-1", "ov-1")).status = "locked_in"
    held = OverlayStatus(status="held", reason="quiet")
    assert incidents.set_overlay_status(fleet, "arm-1", "ov-1", "held", held, {}, now) is False
    assert fleet.get(Overlay, ("arm-1", "ov-1")).status == "locked_in"
    reverted = OverlayStatus(status="reverted", reason="recurred")
    assert (
        incidents.set_overlay_status(fleet, "arm-1", "ov-1", "reverted", reverted, {}, now) is True
    )
    (ev,) = events.find(fleet, {"device": "arm-1", "overlay": "ov-1"}, kinds=["overlay.reverted"])
    assert ev.refs["incident"] == "11111111-2222-4333-8444-555555555555"
    assert ev.why == "arm-1 reverted overlay ov-1: recurred. It was locked_in before."


def test_superseded_is_terminal(fleet, now):
    incidents.report(fleet, "arm-1", *_incident(), now, safety_paths=[])
    gone = OverlayStatus(status="reverted", reason="replaced by firmware 2")
    assert incidents.set_overlay_status(fleet, "arm-1", "ov-1", "superseded", gone, {}, now)
    for status in ("held", "reverted", "superseded"):
        body = OverlayStatus(status="held" if status == "held" else "reverted")
        assert not incidents.set_overlay_status(fleet, "arm-1", "ov-1", status, body, {}, now)
    assert fleet.get(Overlay, ("arm-1", "ov-1")).status == "superseded"
    kinds = [e.kind for e in events.find(fleet, {"device": "arm-1", "overlay": "ov-1"})]
    assert kinds.count("overlay.superseded") == 1 and "overlay.reverted" not in kinds
