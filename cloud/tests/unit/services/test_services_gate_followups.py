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


def test_a_held_overlay_never_batched_survives_superseded_and_rebatches(db, tools, settings):
    """Orchestrator decision 2: v2 (from another overlay) didn't contain sim-3's held fix."""
    _held(db, device="sim-1")
    first = _firmware(db, tools, settings)
    for d in devices.list_devices(db):  # v2 promoted and installed everywhere
        d.assigned_firmware = first.version
    ov = _held(db, device="sim-3", diff={"params": {"max_speed": 0.5}})  # never batched
    gone = OverlayStatus(status="superseded", reason=f"replaced by firmware {first.version}")
    assert incidents.set_overlay_status(db, "sim-3", ov.id, gone, {"status": "superseded"}, T0)
    assert ov.status == "held"
    (ev,) = _events(db, "overlay.superseded", {"device": "sim-3", "overlay": ov.id})
    assert "it was never locked in" in ev.why and ev.data["after"] == "held"
    # A late `reverted` after `superseded` changes nothing: the device no longer runs it.
    # A later device status is recorded (review nit) but moves nothing; a resend is a no-op.
    late = OverlayStatus(status="reverted", reason="late")
    body = {"status": "reverted", "reason": "late"}
    assert incidents.set_overlay_status(db, "sim-3", ov.id, late, body, T0 + S)
    assert ov.status == "held" and ov.raw["ignored_status"] == body
    (ev,) = _events(db, "overlay.reverted", {"device": "sim-3", "overlay": ov.id})
    assert "after reporting it superseded. Ignored" in ev.why and ev.data["after"] == "held"
    assert not incidents.set_overlay_status(db, "sim-3", ov.id, late, body, T0 + 2 * S)
    again = _firmware(db, tools, settings, T0 + 2 * S)
    assert {(r["device_id"], r["overlay_id"]) for r in again.from_overlays} == {("sim-3", ov.id)}
    assert db.get(FirmwareVersion, ("default", again.version)).parent_version == first.version


def test_superseded_while_active_or_locked_in_keeps_the_old_rules(db):
    """`active` (not kept yet) ends `superseded`; `locked_in` (the version has it) stays."""
    active = _held(db)
    active.status = "active"
    gone = OverlayStatus(status="superseded", reason="replaced by firmware 2")
    assert incidents.set_overlay_status(db, "sim-1", active.id, gone, {}, T0)
    assert active.status == "superseded"
    locked = _held(db, device="sim-3")
    locked.status = "locked_in"
    assert incidents.set_overlay_status(db, "sim-3", locked.id, gone, {}, T0)
    assert locked.status == "locked_in"


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


def _repeat(db, firmware: int | None, at: datetime) -> None:
    db.add(
        Incident(
            id=str(uuid.uuid4()),
            device_id="sim-2",
            occurred_at=at,
            received_at=at,
            kind="camera_occluded",
            running={"harness": 1} if firmware is None else {"firmware": firmware, "harness": 1},
        )
    )
    db.flush()


def _frozen_canary(db, tools, settings) -> tuple[Candidate, Overlay]:
    ov = _held(db)
    fw = _firmware(db, tools, settings)
    gate.tick(db, T0, settings=settings)
    assert fw.status == "canary"
    fleet.freeze(db, "kent", "x", T0 + S)
    gate.tick(db, T0 + 2 * S, settings=settings)
    assert fw.status == "frozen"
    return fw, ov


def _unfreeze_and_tick(db, settings, at: datetime) -> None:
    fleet.unfreeze(db, "kent", "x", at)
    gate.tick(db, at + S, settings=settings)


def test_a_repeat_on_the_candidates_version_while_frozen_fails_it_for_good(db, tools, settings):
    fw, ov = _frozen_canary(db, tools, settings)
    _repeat(db, fw.version, T0 + 10 * S)
    _unfreeze_and_tick(db, settings, T0 + 500 * S)
    assert fw.status == "rolled_back"
    assert fw.verdict["why"].startswith("canary repeated camera_occluded 10 s after")
    assert "seen on unfreeze" in fw.verdict["why"] and "unknown" not in fw.verdict["why"]
    assert db.get(Device, "sim-2").assigned_firmware == 1
    assert ov.status == "lockin_rolled_back"  # proven: sticky


def test_a_repeat_on_the_previous_version_while_frozen_restarts_it(db, tools, settings):
    fw, _ = _frozen_canary(db, tools, settings)
    _repeat(db, 1, T0 + 10 * S)  # the canary was pinned back to v1
    _unfreeze_and_tick(db, settings, T0 + 500 * S)
    assert fw.status == "replaying"


def test_an_unknown_version_repeat_rolls_back_but_retries_the_overlay_once(db, tools, settings):
    """Orchestrator decision 3: unproven, so like stale: held again, one retry."""
    fw, ov = _frozen_canary(db, tools, settings)
    _repeat(db, None, T0 + 10 * S)
    _unfreeze_and_tick(db, settings, T0 + 500 * S)
    assert fw.status == "rolled_back" and "on an unknown version" in fw.verdict["why"]
    assert ov.status == "held" and ov.candidate_id is None
    assert incidents.requeue_marks(db, ov) == {"retried": True}
    (ev,) = _events(db, "overlay.requeued", {"device": "sim-1", "overlay": ov.id})
    assert "unproven" in ev.why and "This is its one retry." in ev.why

    # The retry: rebuilt, canaried, frozen, and the same unproven repeat → sticky now.
    retry = _firmware(db, tools, settings, T0 + 600 * S)
    gate.tick(db, T0 + 601 * S, settings=settings)
    assert retry.status == "canary"
    fleet.freeze(db, "kent", "x", T0 + 602 * S)
    gate.tick(db, T0 + 603 * S, settings=settings)
    _repeat(db, None, T0 + 610 * S)
    _unfreeze_and_tick(db, settings, T0 + 700 * S)
    assert retry.status == "rolled_back"
    assert ov.status == "lockin_rolled_back" and "one retry is already used" in ov.status_reason


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


# --- bisect: a failed fold retries each overlay alone, once (orchestrator decision 1) ---


def test_a_failed_fold_bisects_and_only_the_bad_overlay_ends_sticky(db, tools, settings):
    old = _case(db)
    good = _held(db, device="sim-1")
    bad = _held(db, device="sim-3", diff=BREAKS_GRIPPER, **OWN)
    fold = _firmware(db, tools, settings)
    assert len(fold.from_overlays) == 2
    gate.tick(db, T0, settings=settings)
    assert fold.status == "rolled_back" and old in fold.verdict["why"]
    # Neither is blamed yet: both go back to held, marked solo, with the reason.
    for ov in (good, bad):
        assert ov.status == "held" and ov.candidate_id is None
        assert incidents.requeue_marks(db, ov) == {"solo": True}
        assert "retried alone once" in ov.status_reason
        (ev,) = _events(db, "overlay.requeued", {"device": ov.device_id, "overlay": ov.id})
        assert "batched alone next (bisect)" in ev.why

    # The batch builds each alone; the gate pins the failure on the bad one.
    made = batch.run(db, T0 + S, tools).candidates
    solo = {c.from_overlays[0]["device_id"]: c for c in made if c.type == "firmware"}
    assert set(solo) == {"sim-1", "sim-3"}
    assert all(len(c.from_overlays) == 1 for c in made)
    gate.tick(db, T0 + 2 * S, settings=settings)
    assert solo["sim-1"].status == "canary"
    assert solo["sim-3"].status == "rolled_back" and old in solo["sim-3"].verdict["why"]
    assert good.status == "locked_in"
    assert bad.status == "lockin_rolled_back" and old in bad.status_reason


def test_an_overlay_already_tried_solo_is_sticky_if_a_fold_fails_again(db, tools, settings):
    """Bounded: each overlay is tried solo at most once, even if it lands in a fold."""
    _case(db)
    good = _held(db, device="sim-1")
    bad = _held(db, device="sim-3", diff=BREAKS_GRIPPER, **OWN)
    fold = _firmware(db, tools, settings)
    events.record(  # as if sim-3's overlay had been bisected out of an earlier fold
        db,
        "gate",
        "automation",
        "overlay.requeued",
        {"device": "sim-3", "overlay": bad.id},
        "test: earlier bisect",
        data={"marks": {"solo": True}},
        ts=T0,
    )
    gate.tick(db, T0, settings=settings)
    assert fold.status == "rolled_back"
    assert bad.status == "lockin_rolled_back"  # its solo try is used up
    assert good.status == "held" and incidents.requeue_marks(db, good) == {"solo": True}


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


# --- PR #18 review follow-ups ----------------------------------------------------------------


def _only_the_canary(db) -> None:
    """A class whose only device is the canary: no normal device has an assignment."""
    for d in ("sim-1", "sim-3", "arm-1"):
        db.get(Device, d).device_class = "other"
    db.flush()


def test_a_class_with_only_a_canary_does_not_rebuild_candidates_forever(db, tools, settings):
    """Review finding 1: the fallback picked the newest version, i.e. the batch's own
    candidates, so every harness candidate went stale and was rebuilt (v2…v7)."""
    _only_the_canary(db)
    _held(db, device="sim-2", diff={})
    t = T0
    for _ in range(6):
        batch.run(db, t, tools)
        gate.tick(db, t + S, settings=settings)
        t += 10 * S
    cands = db.scalars(select(Candidate)).all()
    assert len(cands) <= 2, [(c.type, c.version, c.status) for c in cands]
    assert not [c for c in cands if c.status == "stale"]


def test_the_fallback_and_corpus_baseline_skip_unpromoted_batch_versions(db, tools, settings):
    _only_the_canary(db)
    _held(db, device="sim-2")
    fw = _firmware(db, tools, settings)
    assert db.get(FirmwareVersion, ("default", fw.version)).source == "batch"
    assert registry.default_assignment(db, "so101") == (1, 1)  # the seed, not the candidate
    assert gate._release_corpus(db, fw)[2] == 1
    fw.status = "promoted"  # once promoted, it is the release
    db.flush()
    assert registry.default_assignment(db, "so101")[0] == fw.version


def test_an_overlay_whose_candidates_keep_going_stale_is_capped(db, tools, settings):
    """Belt and braces: after STALE_RETRY_CAP stale re-queues, sticky with a why."""
    ov = _held(db)
    for i in range(gate.STALE_RETRY_CAP + 1):
        fw = _firmware(db, tools, settings, T0 + 10 * i * S)
        manual = 100 * (i + 1)  # clear of the batch's own version numbers
        _v(db, manual)  # a person moves the class to a new version before the gate runs
        for d in ("sim-1", "sim-3", "arm-1"):
            devices.update(db, d, firmware=manual, **KW)
        gate.tick(db, T0 + (10 * i + 1) * S, settings=settings)
        assert fw.status == "stale"
        if i < gate.STALE_RETRY_CAP:
            assert ov.status == "held"
            assert incidents.requeue_marks(db, ov)["stale"] == i + 1
    assert ov.status == "lockin_error" and "(stale cap)" in ov.status_reason
    assert not batch.run(db, T0 + 100 * S, tools).candidates


def test_unfreeze_does_not_mistake_the_registrys_fill_for_a_person(db):
    """Review finding 3: the registry's automatic fill is `automation`, not a choice."""
    d = db.get(Device, "sim-3")
    d.assigned_firmware = None
    db.flush()
    fleet.freeze(db, "kent", "x", T0)
    devices._fill_assignment(db, d, T0 + S, quiet=False)
    result = fleet.unfreeze(db, "kent", "x", T0 + 2 * S)
    assert "A person reassigned" not in result.why
    assert d.assigned_firmware == 1


def test_unfreeze_keeps_a_person_changing_back_to_the_pin(db):
    """A person moved sim-3 off its pin and back: the pin is their choice, kept."""
    _on_v2(db)
    fleet.freeze(db, "kent", "x", T0)
    assert db.get(Device, "sim-3").assigned_firmware == 1
    devices.update(db, "sim-3", firmware=2, **KW)
    devices.update(db, "sim-3", firmware=1, why="no, keep it on v1", **KW)
    result = fleet.unfreeze(db, "kent", "x", T0 + S)
    assert db.get(Device, "sim-3").assigned_firmware == 1
    assert "sim-3 on firmware 1" in result.why


def test_leaving_the_canary_for_awaiting_canary_clears_the_run_start(db, tools, settings):
    """Review nit: the next canary run starts fresh, and a freeze in between can't count
    the canary's incidents from when the candidate wasn't on it."""
    _held(db)
    fw = _firmware(db, tools, settings)
    gate.tick(db, T0, settings=settings)
    assert fw.status == "canary" and fw.canary_started_at == T0
    gate._apply(db, fw, gate.g.Transition("awaiting_canary", "canary passed; blocked"), T0 + S)
    assert fw.status == "awaiting_canary" and fw.canary_started_at is None
    assert db.get(Device, "sim-2").assigned_firmware == 1  # given back
