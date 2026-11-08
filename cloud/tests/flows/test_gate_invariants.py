"""The gate's invariants over an overnight-style run (from PR #12's review stress test).

Four fake arms (sim-1..3 plus arm-1, all simulated here) run a fixed schedule of faults
for ~53 simulated minutes, a worker tick after every visor cycle: three rules fixes (two
of them folded into one batch), a `bad` and a `bad-late` candidate on the canary, a code
fix, and a freeze/unfreeze in the middle. Deterministic: the schedule is fixed and the
fake arms are seeded.

Checked after every tick:
- every device is assigned a firmware and harness version that exists;
- no device but the canary is ever assigned a version that wasn't promoted;
- at most one candidate per install type is on the canary.
Checked at the end:
- no live candidate is left without a why, and the fleet converged on the release;
- every rollback has a real cause: a canary failure of a version that is bad on the
  canary, an overlay the canary's healer applied during the run (the `bad-late` fault can
  land while another candidate is on the canary), or a sibling withdrawn with one. An
  overlay already on the canary when its run started is not a cause. Stale candidates are
  not rollbacks.
- no fix is lost without a sticky why: each overlay is waiting, carried by a live or
  promoted candidate, or ended with a reason; a kept (`held`) fix never ends `superseded`.
"""

import re
from collections import Counter
from datetime import datetime, timedelta

from sqlalchemy import select

from fake_device.arm import FakeArm
from fake_device.scenarios import SCENARIOS
from romulus_cloud.models import (
    Candidate,
    Device,
    Event,
    FirmwareVersion,
    HarnessVersion,
    Overlay,
)

S = timedelta(seconds=1)
LIVE = {"replaying", "awaiting_canary", "canary", "frozen"}
STICKY = {"lockin_rolled_back", "lockin_error", "dropped", "reverted"}
HOLD_S = 60
SCHEDULE = [  # (at s, device, scenario)
    (20, "sim-1", "rules"),
    (30, "sim-3", "params"),  # folds with sim-1's fix into one candidate
    (350, "sim-2", "bad"),
    (550, "sim-1", "code"),
    (900, "sim-2", "bad-late"),
    (1100, "arm-1", "rules"),
    (1500, "sim-3", "rules"),
]
FREEZE_AT, UNFREEZE_AT, END = 1200, 1350, 2200
REAL_CAUSES = ("canary install rolled_back", "canary reported health failing", "canary repeated")


def _params_patch(arm, hold_s):
    """A rules-scenario incident whose overlay changes a param instead (so it folds)."""
    steps = SCENARIOS["rules"](arm, hold_s)
    fault = steps[0].run

    def run(now):
        real_apply = arm.device.apply_overlay

        def apply(overlay):
            overlay.diff = {"params": {"max_speed": 0.5}}
            real_apply(overlay)

        arm.device.apply_overlay = apply
        try:
            fault(now)
        finally:
            arm.device.apply_overlay = real_apply

    return [steps[0].__class__(steps[0].at_s, steps[0].name, run), *steps[1:]]


def _real(db, c: dict, bad: set[int]) -> bool:
    """A rollback with a real cause: a canary failure signal, on a version that is bad on
    the canary, or (a harness on the canary at the time) its failing health or repeated
    fault; or an overlay the canary's healer applied *during* this run."""
    why = (c["verdict"] or {}).get("why", "")
    if any(r in why for r in REAL_CAUSES):
        return _kind(c) == "harness" or c["version"] in bad
    found = re.search(r"canary needed a local overlay \(([^)]+)\)", why)
    if found:
        overlay = db.get(Overlay, (c["canary_device"], found.group(1)))
        started = datetime.fromisoformat(c["canary_started_at"])
        return overlay is not None and overlay.occurred_at >= started
    return False


def _kind(c):
    return "harness" if c["type"] == "harness" else "firmware"


def _check(db, promoted: dict[str, set], t: int) -> None:
    """The per-tick invariants, straight from the DB (cheap enough for every tick)."""
    db.expire_all()
    rows = db.execute(select(Candidate.type, Candidate.version, Candidate.status)).all()
    for type_, version, status in rows:
        if status == "promoted":
            promoted["harness" if type_ == "harness" else "firmware"].add(version)
    on_canary = Counter(
        "harness" if r[0] == "harness" else "firmware" for r in rows if r[2] == "canary"
    )
    assert all(n <= 1 for n in on_canary.values()), (t, on_canary)
    firmware = set(db.scalars(select(FirmwareVersion.version)))
    harness = set(db.scalars(select(HarnessVersion.version)))
    for d in db.scalars(select(Device)):
        assigned = {"firmware": d.assigned_firmware, "harness": d.assigned_harness}
        assert assigned["firmware"] in firmware and assigned["harness"] in harness, (t, d.id)
        if d.role != "canary":
            for k in ("firmware", "harness"):
                assert assigned[k] in promoted[k], (t, d.id, k, assigned)


def test_gate_invariants_over_a_mixed_overnight_run(
    client, arms, clock, work, now, db_session, device_http, tmp_path
):
    fleet = {**arms, "arm-1": FakeArm(device_http, "arm-1", tmp_path / "arm-1")}
    scenarios = {**SCENARIOS, "params": _params_patch}
    promoted = {"firmware": {1}, "harness": {1}}
    pending = []
    for t in range(0, END, 10):
        at = now + t * S
        for start, name, scenario in SCHEDULE:
            if start == t:
                pending += [(t + int(s.at_s), s) for s in scenarios[scenario](fleet[name], HOLD_S)]
        for item in [p for p in pending if p[0] <= t]:
            item[1].run(now + item[0] * S)
            pending.remove(item)
        if t == FREEZE_AT:
            assert client.post("/v1/fleet/freeze", json={"by": "ci", "why": "stress"}).is_success
        if t == UNFREEZE_AT:
            assert client.post("/v1/fleet/unfreeze", json={"by": "ci", "why": "stress"}).is_success
        clock.at(at)
        for arm in fleet.values():
            arm.tick(at)
        work(at + S)
        _check(db_session, promoted, t)

    cands = client.get("/v1/candidates").json()
    statuses = Counter(c["status"] for c in cands)
    assert statuses["promoted"] >= 4, statuses
    for c in cands:
        if c["status"] in LIVE:
            assert c["why"], c

    # Every rollback has a real cause.
    bad = set(fleet["sim-2"].device.bad_versions)
    assert bad, "the run should have exercised the bad-candidate modes"
    real = {c["id"] for c in cands if c["status"] == "rolled_back" and _real(db_session, c, bad)}
    assert real, statuses
    labels = {c["label"] for c in cands if c["id"] in real}
    for c in cands:
        if c["status"] != "rolled_back" or c["id"] in real:
            continue
        why = (c["verdict"] or {}).get("why", "")
        assert why.startswith("withdrawn:") and any(lb in why for lb in labels), (c["label"], why)

    # No fix is lost without a sticky why (PR #18): every overlay the cloud knows is still
    # waiting (`active`/`held`), carried by a live or promoted candidate (`locked_in`), or
    # ended with a reason. `superseded` only ends an overlay the device never kept.
    carried: dict[tuple[str, str], set[str]] = {}
    for c in cands:
        for r in c["from_overlays"]:
            carried.setdefault((r["device_id"], r["overlay_id"]), set()).add(c["status"])
    db_session.expire_all()
    overlays = db_session.scalars(select(Overlay).where(Overlay.incident_id.is_not(None))).all()
    assert overlays
    for ov in overlays:
        key = (ov.device_id, ov.id)
        if ov.status in ("active", "held"):
            continue
        if ov.status == "locked_in":
            assert carried.get(key, set()) & (LIVE | {"promoted"}), (key, carried.get(key))
        elif ov.status == "superseded":
            held = db_session.scalars(
                select(Event).where(
                    Event.kind == "overlay.held",
                    Event.refs.contains({"device": ov.device_id, "overlay": ov.id}),
                )
            ).first()
            assert held is None, (key, "a kept fix ended superseded")
        else:
            assert ov.status in STICKY and ov.status_reason, (key, ov.status, ov.status_reason)

    # The fleet converged on the release; the canary too, once nothing is in flight.
    release = {k: max(v for v in promoted[k] if v is not None) for k in ("firmware", "harness")}
    if not [c for c in cands if c["status"] == "canary"]:
        for d in client.get("/v1/devices").json():
            assert d["assigned"] == release, d
            assert d["running"]["firmware"] == release["firmware"], d
