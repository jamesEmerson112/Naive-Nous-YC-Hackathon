"""The gate's worker step: move each live candidate one state forward (v0 plan §7.4, D6).

`tick(session, now)` loads each candidate's inputs, asks `domain.gate.next_state`, and
applies the answer with an Event that says why:

- **replay** (computed once per run of `replaying`): firmware candidates replay their rules
  against the triggering incident's signals (P0); harness and code candidates get a stub
  replay that says so.
- **canary** (`start_canary`): assign the candidate to the class's `role = canary` device,
  remember what that device was assigned before (`canary_prev_version`), and set
  `canary_started_at = now`. Observations are that device's check-ins
  (`canary_checkins`), install reports and incidents received at or after that instant,
  all on the server's clock.
- **promote:** assign the candidate to every device of its class.
- **roll back:** give the canary back its previous assignment (not `last_good`, which can
  already be the bad candidate in F4's late path). Candidates locked in from the same
  overlay roll back with it: a skill describing a fix the fleet refused is not news.
- **freeze:** while the kill switch is on nothing moves; frozen candidates restart from
  replay on unfreeze, with a fresh canary window (F5).

No LLM is called here. Each step is idempotent: everything it needs is in the DB.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from romulus_cloud import events
from romulus_cloud.domain import gate as g
from romulus_cloud.models import (
    CanaryCheckin,
    Candidate,
    Device,
    FirmwareVersion,
    HarnessVersion,
    Incident,
    InstallReport,
)
from romulus_cloud.services import fleet, registry
from romulus_cloud.settings import Settings, get_settings

ACTOR = "gate"
LIVE = ("replaying", "awaiting_canary", "canary", "frozen")
TRANSITION_KINDS = {
    "canary": "candidate.canary_started",
    "awaiting_canary": "candidate.awaiting_canary",
    "promoted": "candidate.promoted",
    "rolled_back": "candidate.rolled_back",
    "frozen": "candidate.frozen",
    "replaying": "candidate.restarted",
}


def install_type(c: Candidate) -> str:
    """What the canary installs: a code candidate arrives as a firmware manifest."""
    return "harness" if c.type == "harness" else "firmware"


def label(c: Candidate) -> str:
    if c.type == "code":
        return f"code {c.code_rev} (firmware v{c.version})"
    return f"{c.type} v{c.version}"


def view(c: Candidate) -> dict[str, Any]:
    return {
        "id": c.id,
        "type": c.type,
        "version": c.version,
        "code_rev": c.code_rev,
        "label": label(c),
        "device_class": c.device_class,
        "status": c.status,
        "incident_id": c.incident_id,
        "from_overlays": c.from_overlays,
        "canary_device": c.canary_device,
        "canary_started_at": c.canary_started_at.isoformat() if c.canary_started_at else None,
        "canary_prev_version": c.canary_prev_version,
        "replay": c.replay,
        "verdict": c.verdict,
        "why": c.why,
        "touches_safety": c.touches_safety,
        "stub_stages": c.stub_stages,
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "updated_at": c.updated_at.isoformat() if c.updated_at else None,
    }


def list_candidates(session: Session, fleet_id: str = "default") -> list[Candidate]:
    stmt = select(Candidate).where(Candidate.fleet_id == fleet_id)
    return list(session.scalars(stmt.order_by(Candidate.created_at.desc(), Candidate.type)))


def _refs(c: Candidate, **extra: Any) -> dict[str, Any]:
    refs: dict[str, Any] = {"candidate": c.id, install_type(c): c.version}
    if c.incident_id:
        refs["incident"] = c.incident_id
    if c.touches_safety:
        refs["touches_safety"] = True
    refs.update({k: v for k, v in extra.items() if v is not None})
    return refs


def _record(session: Session, c: Candidate, kind: str, why: str, now: datetime, **kw) -> None:
    events.record(
        session,
        ACTOR,
        "automation",
        kind,
        _refs(c, device=kw.pop("device", None)),
        why,
        data={"type": c.type, "version": c.version, "code_rev": c.code_rev, **kw},
        ts=now,
        fleet_id=c.fleet_id,
    )


# --- inputs -----------------------------------------------------------------------------


def _canary(session: Session, c: Candidate) -> Device | None:
    """The class's canary device (always simulated in v0, v0 plan §7.4)."""
    if c.canary_device:
        return session.get(Device, c.canary_device)
    stmt = select(Device).where(
        Device.fleet_id == c.fleet_id,
        Device.device_class == c.device_class,
        Device.role == "canary",
    )
    return session.scalars(stmt.order_by(Device.id)).first()


def _canary_busy(session: Session, c: Candidate, device: Device) -> str | None:
    """Another candidate of the same install type already on this canary, if any."""
    others = session.scalars(
        select(Candidate).where(
            Candidate.fleet_id == c.fleet_id,
            Candidate.status == "canary",
            Candidate.canary_device == device.id,
            Candidate.id != c.id,
        )
    )
    for other in others:
        if install_type(other) == install_type(c):
            return label(other)
    return None


def _incidents(session: Session, c: Candidate) -> list[Incident]:
    ids = {c.incident_id} if c.incident_id else set()
    return [i for i in (session.get(Incident, x) for x in ids) if i is not None]


def _replay(session: Session, c: Candidate) -> g.ReplayResult:
    if c.type == "code":
        return g.replay_code_stub()
    if c.type == "harness":
        return g.replay_stub("a harness skill is prose; there are no rules to replay")
    fw = session.get(FirmwareVersion, (c.fleet_id, c.version))
    if fw is None:
        return g.ReplayResult(False, f"replay failed: firmware v{c.version} does not exist")
    cases = [
        g.ReplayCase(i.id, i.signals or {}, i.resolving_action, i.kind)
        for i in _incidents(session, c)
    ]
    return g.replay(fw.manifest, cases)


def _version(c: Candidate, firmware: int | None, harness: int | None) -> int | None:
    return harness if install_type(c) == "harness" else firmware


def _already_installed(session: Session, c: Candidate, started: datetime) -> g.InstallObs | None:
    """An install observation at `started` when the canary already runs the candidate.

    After a freeze pins the canary to its last good version, that can be the candidate
    itself (it installed it during the first run). The re-run's fresh window then sees no
    new install, so the earlier `installed` report counts as of `started`; the health
    check-ins must still all be fresh (F5).
    """
    device = session.get(Device, c.canary_device) if c.canary_device else None
    kind = install_type(c)
    if device is None or getattr(device, f"running_{kind}") != c.version:
        return None
    last = session.scalars(
        select(InstallReport)
        .where(InstallReport.device_id == device.id, InstallReport.type == kind)
        .order_by(InstallReport.received_at.desc())
    ).first()
    if last is None or last.result != "installed" or last.version != str(c.version):
        return None
    if last.received_at >= started:
        return None  # a fresh install; already observed
    detail = f"already running it since {last.received_at.isoformat()}"
    return g.InstallObs(started, "installed", c.version, kind, detail)


def _observations(session: Session, c: Candidate, started: datetime) -> list[g.CanaryObs]:
    """The canary's check-ins, installs and incidents since `started` (server clock)."""
    device = c.canary_device
    obs: list[g.CanaryObs] = []
    for ck in session.scalars(
        select(CanaryCheckin).where(CanaryCheckin.device_id == device, CanaryCheckin.at >= started)
    ):
        obs.append(
            g.CheckinObs(
                at=ck.at,
                health=ck.health,
                version=_version(c, ck.firmware, ck.harness),
                overlay_id=ck.overlay_id,
                device_id=device or "",
            )
        )
    for r in session.scalars(
        select(InstallReport).where(
            InstallReport.device_id == device, InstallReport.received_at >= started
        )
    ):
        version: int | str = int(r.version) if r.type in ("firmware", "harness") else r.version
        obs.append(g.InstallObs(r.received_at, r.result, version, r.type, r.detail or ""))
    earlier = _already_installed(session, c, started)
    if earlier is not None:
        obs.append(earlier)
    for i in session.scalars(
        select(Incident).where(Incident.device_id == device, Incident.received_at >= started)
    ):
        running = i.running or {}
        obs.append(
            g.IncidentObs(
                i.received_at, i.kind, _version(c, running.get("firmware"), running.get("harness"))
            )
        )
    return obs


def _inputs(
    session: Session, c: Candidate, settings: Settings, frozen: bool, replay: g.ReplayResult | None
) -> g.GateInputs:
    canary = _canary(session, c)
    available = canary is not None
    if canary is not None and c.status != "canary" and _canary_busy(session, c, canary):
        available = False
    started = c.canary_started_at
    return g.GateInputs(
        frozen=frozen,
        kind=c.type,
        replay=replay,
        canary_available=available,
        passthrough=True,  # FakeCoder only: the coding agent (M8) will set False
        real_code_canary=bool(canary and canary.kind == "real"),
        allow_passthrough_code_promotion=settings.allow_passthrough_code_promotion,
        candidate_version=c.version,
        install_type=install_type(c),
        canary_observations=tuple(_observations(session, c, started)) if started else (),
        canary_started_at=started if c.status == "canary" else None,
        canary_window_s=settings.canary_window_s,
        repeat_kinds=frozenset(i.kind for i in _incidents(session, c)),
        touches_safety=c.touches_safety,
    )


# --- applying a transition -----------------------------------------------------------


def _assigned(d: Device, c: Candidate) -> int | None:
    return getattr(d, f"assigned_{install_type(c)}")


def _assign(d: Device, c: Candidate, version: int | None) -> None:
    setattr(d, f"assigned_{install_type(c)}", version)


def _start_canary(session: Session, c: Candidate, now: datetime) -> Device:
    canary = _canary(session, c)
    assert canary is not None  # next_state only starts a canary when one is available
    current = _assigned(canary, c)
    if current != c.version:  # after a freeze the canary may already be pinned to it
        c.canary_prev_version = current
    _assign(canary, c, c.version)
    c.canary_device, c.canary_started_at = canary.id, now
    return canary


def _restore_canary(session: Session, c: Candidate) -> str:
    """Give the canary back what it had before this candidate. Returns a phrase for the why."""
    if not c.canary_device:
        return "it was never assigned to a device"
    canary = session.get(Device, c.canary_device)
    if canary is None or _assigned(canary, c) != c.version:
        return f"{c.canary_device} is no longer assigned it"
    _assign(canary, c, c.canary_prev_version)
    return f"{canary.id} is assigned {install_type(c)} {c.canary_prev_version} again"


def _promote(session: Session, c: Candidate) -> list[str]:
    moved = []
    stmt = select(Device).where(
        Device.fleet_id == c.fleet_id, Device.device_class == c.device_class
    )
    for d in session.scalars(stmt.order_by(Device.id)):
        if _assigned(d, c) != c.version:
            _assign(d, c, c.version)
            moved.append(d.id)
    return moved


def _siblings(session: Session, c: Candidate) -> list[Candidate]:
    """Live candidates locked in from any of the same overlays."""
    found: dict[str, Candidate] = {}
    for ref in c.from_overlays or []:
        stmt = select(Candidate).where(
            Candidate.from_overlays.contains([ref]),
            Candidate.status.in_(LIVE),
            Candidate.id != c.id,
        )
        found.update({s.id: s for s in session.scalars(stmt)})
    return list(found.values())


def _roll_back(session: Session, c: Candidate, why: str, now: datetime) -> None:
    where = _restore_canary(session, c)
    c.status, c.updated_at = "rolled_back", now
    c.verdict = {"status": "fail", "why": why}
    _record(
        session,
        c,
        "candidate.rolled_back",
        f"{label(c).capitalize()} rolled back: {why}; {where}.",
        now,
        device=c.canary_device,
        restored=c.canary_prev_version,
    )


def _apply(session: Session, c: Candidate, t: g.Transition, now: datetime) -> None:
    old = c.status
    if t.state == "rolled_back":
        _roll_back(session, c, t.why, now)
        for s in _siblings(session, c):
            _roll_back(session, s, f"{label(c)} from the same overlay was rolled back", now)
        return
    c.status, c.updated_at = t.state, now
    kind = TRANSITION_KINDS[t.state]
    if t.start_canary:
        canary = _start_canary(session, c, now)
        _record(
            session,
            c,
            kind,
            f"{t.why}. {label(c).capitalize()} assigned to canary {canary.id} "
            f"(was {install_type(c)} {c.canary_prev_version}); nothing else changes until it "
            "passes.",
            now,
            device=canary.id,
            previous=c.canary_prev_version,
        )
        return
    if t.state == "promoted":
        moved = _promote(session, c)
        c.verdict = {"status": "pass", "why": t.why}
        _record(
            session,
            c,
            kind,
            f"{label(c).capitalize()} → fleet: {t.why}. Assigned to "
            f"{', '.join(moved) if moved else 'no device (all had it already)'}.",
            now,
            assigned=moved,
        )
        return
    if old == "canary" and t.state == "awaiting_canary":  # passed, but now blocked
        t = t._replace(why=f"{t.why}; {_restore_canary(session, c)}")
    if t.state == "replaying":  # unfrozen: a fresh replay, then a fresh canary (F5)
        c.replay, c.canary_started_at = None, None
    _record(session, c, kind, t.why, now, device=c.canary_device, before=old)


def _stale(session: Session, c: Candidate) -> str | None:
    """Why `c` is out of date, if its class moved on to another version since the batch
    built it: promoting it would undo that version's fix (a lost update)."""
    kind = install_type(c)
    row_type = HarnessVersion if kind == "harness" else FirmwareVersion
    row = session.get(row_type, (c.fleet_id, c.version))
    firmware, harness = registry.default_assignment(session, c.device_class, c.fleet_id)
    current = harness if kind == "harness" else firmware
    if row is None or row.parent_version is None or current in (None, row.parent_version):
        return None
    return (
        f"stale: built on {kind} {row.parent_version}, but the {c.device_class} fleet now "
        f"runs {kind} {current}; promoting it would undo that version's fix"
    )


def tick(session: Session, now: datetime, *, settings: Settings | None = None) -> list[str]:
    """The worker step. Returns the ids of candidates that changed state."""
    settings = settings or get_settings()
    frozen = fleet.is_frozen(session, settings.fleet_id)
    stmt = select(Candidate).where(
        Candidate.fleet_id == settings.fleet_id, Candidate.status.in_(LIVE)
    )
    changed = []
    for c in session.scalars(stmt.order_by(Candidate.created_at, Candidate.type)).all():
        if c.status not in LIVE:  # a sibling rolled back earlier in this tick
            continue
        stale = _stale(session, c) if c.status in ("replaying", "awaiting_canary") else None
        if stale and not frozen:
            _apply(session, c, g.Transition("rolled_back", stale), now)
            changed.append(c.id)
            continue
        replay = g.ReplayResult.from_json(c.replay)
        if c.status == "replaying" and replay is None and not frozen:
            replay = _replay(session, c)
            c.replay = replay.to_json()
            if replay.stub and "replay" not in (c.stub_stages or []):
                c.stub_stages = [*(c.stub_stages or []), "replay"]
            _record(session, c, "candidate.replayed", replay.why, now, replay=c.replay)
        t = g.next_state(c.status, _inputs(session, c, settings, frozen, replay), now)
        if t.state != c.status:
            _apply(session, c, t, now)
            changed.append(c.id)
    session.flush()
    return changed
