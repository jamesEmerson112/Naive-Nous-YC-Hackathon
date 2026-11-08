"""Gate follow-ups from PR #12's last review, plus the regression corpus in the gate.

1. A re-queued overlay survives a device's `superseded`.
2. Unfreeze respects a manual reassignment made during the freeze (and before it).
3. Freeze pins a canary with no previous version to the promoted release.
4. A repeat incident received during a freeze fails the candidate on unfreeze.
5. Firmware candidates replay the regression corpus; only regressions fail them.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from romulus_cloud import events
from romulus_cloud.adapters.blobstore import build_blobstore
from romulus_cloud.adapters.coder import FakeCoder
from romulus_cloud.adapters.llm import FakeLlm
from romulus_cloud.models import (
    Candidate,
    Device,
    Event,
    FirmwareVersion,
    Incident,
    Overlay,
    RegressionCase,
)
from romulus_cloud.services import batch, datasets, devices, fleet, gate, incidents, registry
from romulus_cloud.services.seed import FIRMWARE_V1, seed
from romulus_contracts import OverlayStatus

T0 = datetime(2026, 11, 7, 22, 0, tzinfo=UTC)
S = timedelta(seconds=1)
KW = {"actor": "human:test", "actor_class": "human"}


@pytest.fixture
def tools(settings):
    return batch.Tools(settings, FakeLlm(), FakeCoder(), build_blobstore(settings.blob_dir))


@pytest.fixture
def db(db_session, settings):
    seed(db_session, build_blobstore(settings.blob_dir))
    db_session.flush()
    return db_session


def _incident(session, device="sim-1", kind="camera_occluded", signals=None, action="safe_pose"):
    iid = str(uuid.uuid4())
    session.add(
        Incident(
            id=iid,
            device_id=device,
            occurred_at=T0,
            received_at=T0,
            kind=kind,
            resolved=True,
            resolving_action=action,
            signals=signals or {"brightness": [0.41, 0.18]},
            running={"firmware": 1, "harness": 1, "code_rev": "c0de001"},
        )
    )
    session.flush()
    return iid


def _held(session, device="sim-1", diff=None, **incident):
    """A held overlay and its incident, as M1 stores them."""
    iid = _incident(session, device, **incident)
    ov = Overlay(
        device_id=device,
        id=f"ov-{iid[:6]}",
        incident_id=iid,
        base_firmware=1,
        base_code_rev="c0de001",
        diff=diff if diff is not None else {"rules": {"camera_occluded": {"below": 0.35}}},
        status="held",
        occurred_at=T0,
        received_at=T0,
    )
    session.add(ov)
    session.flush()
    return ov


def _case(session, device="sim-3", kind="gripper_stall", signals=None, action="backoff_retry"):
    """An old resolved incident in the regression corpus (v1 passes it by default)."""
    iid = _incident(
        session,
        device,
        kind=kind,
        signals=signals or {"gripper_current_ma": [950, 980]},
        action=action,
    )
    session.add(
        RegressionCase(
            incident_id=iid,
            fleet_id="default",
            kind=kind,
            signals=signals or {"gripper_current_ma": [950, 980]},
            expected_action=action,
        )
    )
    session.flush()
    return iid


def _firmware(session, tools, settings, at=T0) -> Candidate:
    made = batch.run(session, at, tools).candidates
    return next(c for c in made if c.type == "firmware")


def _events(session, kind, refs):
    return list(session.scalars(events.find_query(refs, kinds=[kind])))


def _promoted(session, version: int, kind="firmware") -> Candidate:
    """A promoted release row, as the gate leaves it."""
    c = Candidate(
        id=f"cand_rel{version}{kind[0]}",
        type=kind,
        version=version,
        status="promoted",
        created_at=T0,
        updated_at=T0,
    )
    session.add(c)
    session.flush()
    return c


def _v(session, version: int) -> None:
    registry.create_firmware(session, FIRMWARE_V1 | {"version": version}, **KW)


# --- 1. a re-queued overlay survives `superseded` ---------------------------------------------


def test_requeued_overlay_stays_held_when_its_device_reports_superseded(db, tools, settings):
    """Review finding 1 / nit A: sim-3's fix went stale and was re-queued; sim-3 then
    installed the promoted v2 (which doesn't contain it) and reported `superseded`."""
    _held(db, device="sim-1")
    first = _firmware(db, tools, settings)
    gate.tick(db, T0, settings=settings)
    ov = _held(db, device="sim-3", diff={"params": {"max_speed": 0.5}})
    second = _firmware(db, tools, settings, T0 + S)
    for d in devices.list_devices(db):  # what promoting the first one does
        d.assigned_firmware = first.version
    gate.tick(db, T0 + 2 * S, settings=settings)
    assert second.status == "stale" and ov.status == "held" and ov.candidate_id is None

    gone = OverlayStatus(status="superseded", reason=f"replaced by firmware {first.version}")
    assert incidents.set_overlay_status(db, "sim-3", ov.id, gone, {"status": "superseded"}, T0)
    assert ov.status == "held"  # its diff lives in the cloud; the fix isn't lost
    (ev,) = _events(db, "overlay.superseded", {"device": "sim-3", "overlay": ov.id})
    assert "stays held for the next batch" in ev.why and ev.data["after"] == "held"
    assert ev.refs["incident"] == ov.incident_id
    # A resend changes nothing, and the next batch folds it onto the new base.
    assert not incidents.set_overlay_status(db, "sim-3", ov.id, gone, {}, T0 + S)
    again = _firmware(db, tools, settings, T0 + 3 * S)
    assert {(r["device_id"], r["overlay_id"]) for r in again.from_overlays} == {("sim-3", ov.id)}
    manifest = db.get(FirmwareVersion, ("default", again.version)).manifest
    assert manifest["params"]["max_speed"] == 0.5
    assert db.get(FirmwareVersion, ("default", again.version)).parent_version == first.version


def test_a_held_overlay_never_batched_is_still_terminal_on_superseded(db):
    """Only a re-queued overlay is kept: plain `held` → `superseded` stays the D7 contract."""
    ov = _held(db)
    gone = OverlayStatus(status="superseded", reason="replaced by firmware 2")
    assert incidents.set_overlay_status(db, "sim-1", ov.id, gone, {}, T0)
    assert ov.status == "superseded"


# --- 2. unfreeze respects manual reassignments ---------------------------------------------


def _on_v2(db):
    """v2 promoted and assigned everywhere; sim-3 hasn't installed it (last good v1)."""
    _v(db, 2)
    _promoted(db, 2)
    for d in devices.list_devices(db):
        d.assigned_firmware, d.last_good_firmware, d.last_good_harness = 2, 2, 1
    db.get(Device, "sim-3").last_good_firmware = 1


def test_unfreeze_keeps_a_rollback_a_person_made_during_the_freeze(db):
    _on_v2(db)
    fleet.freeze(db, "kent", "v2 looks off", T0)
    assert db.get(Device, "sim-3").assigned_firmware == 1  # pinned to its last good
    devices.update(db, "sim-1", firmware=1, why="v2 is bad on sim-1", **KW)

    result = fleet.unfreeze(db, "kent", "done", T0 + S)
    assert db.get(Device, "sim-1").assigned_firmware == 1  # the person's choice stands
    assert db.get(Device, "sim-3").assigned_firmware == 2  # back where it was before
    assert db.get(Device, "arm-1").assigned_firmware == 2
    assert "A person reassigned these while frozen, so they stay: sim-1 on firmware 1" in (
        result.why
    )
    (ev,) = db.scalars(select(Event).where(Event.kind == "fleet.unfrozen")).all()
    assert ev.data["kept"] == ["sim-1 on firmware 1"]


def test_unfreeze_keeps_a_rollback_a_person_made_before_the_freeze(db):
    """Review finding 3: a release backed out before the freeze stays backed out."""
    _on_v2(db)
    for d in devices.list_devices(db):
        devices.update(db, d.id, firmware=1, why="v2 is bad", **KW)
    fleet.freeze(db, "kent", "x", T0)
    fleet.unfreeze(db, "kent", "x", T0 + S)
    assert {d.assigned_firmware for d in devices.list_devices(db)} == {1}


def test_unfreeze_without_a_snapshot_falls_back_to_the_promoted_release(db):
    """A freeze recorded before snapshots existed (or a device registered while frozen)."""
    _on_v2(db)
    fleet.freeze(db, "kent", "x", T0)
    ev = db.scalars(select(Event).where(Event.kind == "fleet.frozen")).one()
    ev.data = {k: v for k, v in ev.data.items() if k != "snapshot"}
    db.flush()
    result = fleet.unfreeze(db, "kent", "x", T0 + S)
    assert db.get(Device, "sim-3").assigned_firmware == 2
    assert "Back on the promoted release" in result.why


# --- 3. freeze when the canary has no previous version --------------------------------------


def _canary_on(db, version: int, status="canary") -> Candidate:
    """sim-2 runs an in-flight candidate with no `canary_prev_version` on record."""
    _v(db, version)
    c = Candidate(
        id=f"cand_live{version}",
        type="firmware",
        version=version,
        status=status,
        canary_device="sim-2",
        canary_started_at=T0,
        canary_prev_version=None,
        created_at=T0,
    )
    db.add(c)
    sim2 = db.get(Device, "sim-2")
    sim2.assigned_firmware = sim2.last_good_firmware = version  # installed: last good is it
    db.flush()
    return c


def test_freeze_pins_a_canary_without_a_previous_version_to_the_promoted_release(db):
    _on_v2(db)
    _canary_on(db, 3)
    result = fleet.freeze(db, "kent", "x", T0 + S)
    assert db.get(Device, "sim-2").assigned_firmware == 2  # not last_good (the candidate)
    assert (
        "sim-2 had no firmware version on record from before firmware v3, so it is pinned "
        "to firmware 2, the so101 class's promoted release" in result.why
    )
    fleet.unfreeze(db, "kent", "x", T0 + 2 * S)
    assert db.get(Device, "sim-2").assigned_firmware == 2  # never back onto the candidate


def test_freeze_with_nothing_promoted_pins_to_what_the_class_runs(db):
    _canary_on(db, 2)
    result = fleet.freeze(db, "kent", "x", T0)
    assert db.get(Device, "sim-2").assigned_firmware == 1
    assert "pinned to firmware 1, what the so101 class's devices run" in result.why


# --- 4. a repeat incident during a freeze -------------------------------------------------


def _repeat(db, firmware: int, at: datetime) -> None:
    db.add(
        Incident(
            id=str(uuid.uuid4()),
            device_id="sim-2",
            occurred_at=at,
            received_at=at,
            kind="camera_occluded",
            running={"firmware": firmware, "harness": 1},
        )
    )
    db.flush()


@pytest.mark.parametrize(("running", "rolled_back"), [(None, True), (1, False)])
def test_a_repeat_incident_received_while_frozen_fails_the_candidate_on_unfreeze(
    db, tools, settings, running, rolled_back
):
    _held(db)
    fw = _firmware(db, tools, settings)
    gate.tick(db, T0, settings=settings)
    assert fw.status == "canary"
    fleet.freeze(db, "kent", "x", T0 + S)
    gate.tick(db, T0 + 2 * S, settings=settings)
    assert fw.status == "frozen"
    # The canary repeats the fault while frozen: on the candidate (or an unknown version,
    # fail closed), or on its previous version, which it was pinned back to.
    _repeat(db, fw.version if running is None else running, T0 + 10 * S)
    fleet.unfreeze(db, "kent", "x", T0 + 500 * S)
    gate.tick(db, T0 + 501 * S, settings=settings)
    if rolled_back:
        assert fw.status == "rolled_back"
        assert fw.verdict["why"].startswith("canary repeated camera_occluded 10 s after")
        assert "seen on unfreeze" in fw.verdict["why"]
        assert db.get(Device, "sim-2").assigned_firmware == 1
    else:
        assert fw.status == "replaying"


# --- 5. the regression corpus in the gate -------------------------------------------------

BREAKS_GRIPPER = {"rules": {"gripper_stall": {"above": 2000}}}
OWN = {
    "kind": "gripper_stall",
    "signals": {"gripper_current_ma": [2100]},
    "action": "backoff_retry",
}


def test_a_candidate_that_breaks_an_old_incident_is_rolled_back_naming_it(db, tools, settings):
    old = _case(db)
    ov = _held(db, diff=BREAKS_GRIPPER, **OWN)
    fw = _firmware(db, tools, settings)
    gate.tick(db, T0, settings=settings)
    assert fw.status == "rolled_back" and fw.canary_device is None
    why = fw.verdict["why"]
    assert "replayed 1 incident; 1 pass" in why  # its own incident still passes
    assert "regression corpus: replayed 1 incidents; 1 regress" in why and old in why
    assert db.get(Device, "sim-2").assigned_firmware == 1
    assert ov.status == "lockin_rolled_back" and old in ov.status_reason


def test_a_case_the_release_already_fails_is_not_a_regression(db, tools, settings):
    # v1 can't fire `park` on this case either, so the candidate can't make it worse.
    _case(db, signals={"gripper_current_ma": [100]}, action="park")
    kept = _case(db, kind="camera_occluded", signals={"brightness": [0.1]}, action="safe_pose")
    _held(db)
    fw = _firmware(db, tools, settings)
    gate.tick(db, T0, settings=settings)
    assert fw.status == "canary", fw.verdict
    assert "1 pass; 1 already fail on firmware 1, so they can't regress" in fw.replay["why"]
    assert kept in {case["case_id"] for case in fw.replay["cases"]}


def test_an_empty_corpus_passes_and_says_so(db, tools, settings):
    _held(db)
    fw = _firmware(db, tools, settings)
    gate.tick(db, T0, settings=settings)
    assert fw.status == "canary"
    assert fw.replay["why"].endswith("regression corpus: no cases yet, so nothing can regress")


def test_all_cases_already_failing_on_the_release_pass(db, tools, settings):
    _case(db, signals={"gripper_current_ma": [100]}, action="park")
    _held(db)
    fw = _firmware(db, tools, settings)
    gate.tick(db, T0, settings=settings)
    assert fw.status == "canary"
    assert "regression corpus: all 1 cases already fail on firmware 1" in fw.replay["why"]


def test_malformed_candidate_rules_fail_with_a_clear_why(db, tools, settings):
    _case(db)
    _held(db)
    fw = _firmware(db, tools, settings)
    row = db.get(FirmwareVersion, ("default", fw.version))
    result = gate._corpus(db, fw, row.manifest | {"rules": ["not", "a", "map"]})
    assert not result.passed and "regression corpus: candidate rules are malformed" in result.why
    # Through the gate: rules missing their threshold fail replay, never raise.
    bad = row.manifest["rules"] | {"gripper_stall": {"signal": "gripper_current_ma"}}
    row.manifest = row.manifest | {"rules": bad}
    db.flush()
    gate.tick(db, T0, settings=settings)
    assert fw.status == "rolled_back" and "malformed" in fw.verdict["why"]


def test_a_broken_release_counts_every_case(db, tools, settings):
    """Fail closed: if the release can't be replayed, every corpus case counts."""
    old = _case(db)
    _held(db, diff=BREAKS_GRIPPER, **OWN)
    fw = _firmware(db, tools, settings)
    v1 = db.get(FirmwareVersion, ("default", 1))
    v1.manifest = v1.manifest | {"rules": "broken"}
    db.flush()
    gate.tick(db, T0, settings=settings)
    assert fw.status == "rolled_back" and old in fw.verdict["why"]
    assert "firmware 1 could not be replayed, so every case counts" in fw.verdict["why"]


def test_the_release_is_replayed_once_per_tick_per_class(db, tools, settings, monkeypatch):
    _case(db)
    _held(db, device="sim-1")
    batch.run(db, T0, tools)
    _held(db, device="sim-3", diff={"params": {"max_speed": 0.5}})
    batch.run(db, T0 + S, tools)
    calls = []
    real = datasets.regression_cases

    def counting(session, fleet_id, device_class):
        calls.append(device_class)
        return real(session, fleet_id, device_class)

    monkeypatch.setattr(datasets, "regression_cases", counting)
    gate.tick(db, T0 + 2 * S, settings=settings)
    fws = db.scalars(select(Candidate).where(Candidate.type == "firmware")).all()
    assert all(c.replay is not None for c in fws) and len(fws) == 2
    assert calls == ["so101"]  # one corpus load (and release replay) for both candidates
    assert gate.CORPUS_CACHE not in db.info  # the cache lives for one tick only


# --- K17 fails closed on a canary of unknown kind (PR #17: auto-registered → `unknown`) ------


@pytest.mark.parametrize(
    ("kind", "status"),
    [
        ("unknown", "awaiting_canary"),
        ("real", "awaiting_canary"),
        ("test", "canary"),
        ("sim", "canary"),
    ],
)
def test_a_safety_candidate_only_canaries_on_a_device_known_to_be_simulated(
    db, make_settings, kind, status
):
    settings = make_settings(safety_paths=["camera_occluded"])
    tools = batch.Tools(settings, FakeLlm(), FakeCoder(), build_blobstore(settings.blob_dir))
    db.get(Device, "sim-2").kind = kind
    _held(db)
    fw = _firmware(db, tools, settings)
    assert fw.touches_safety
    gate.tick(db, T0, settings=settings)
    assert fw.status == status
    if status == "awaiting_canary":
        assert db.get(Device, "sim-2").assigned_firmware == 1
        (ev,) = _events(db, "candidate.awaiting_canary", {"candidate": fw.id})
        assert "not known to be simulated" in ev.why and "K17" in ev.why


def test_an_unknown_canary_never_counts_as_a_real_code_canary(db, settings):
    """`real_code_canary` unlocks code promotion (B7), so there `unknown` is not real."""
    db.get(Device, "sim-2").kind = "unknown"
    c = Candidate(id="cand_code", type="code", version=2, code_rev="c0de002", created_at=T0)
    db.add(c)
    db.flush()
    inputs, k17 = gate._inputs(db, c, settings, False, None)
    assert inputs.real_code_canary is False and k17 is False  # not touches_safety
