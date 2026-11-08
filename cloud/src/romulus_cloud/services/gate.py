"""The gate's worker step: move each live candidate one state forward (v0 plan §7.4, D6).

`tick(session, now)` locks `fleet_state` (so a freeze can't land mid-tick), then, in one
savepoint per candidate, loads its inputs, asks `domain.gate.next_state` and applies the
answer with an Event that says why. A candidate that raises is marked `error` with the
reason and the rest go on.

- **stale:** a waiting candidate built on a version its class has since moved past would
  undo that version's fix. It is withdrawn as `stale` (not a failure) and its overlays go
  back to `held`, so the next batch folds them onto the new base.
- **replay** (once per run of `replaying`): firmware candidates replay their rules against
  every folded incident's signals (P0), then against the class's regression corpus
  (`datasets.regression_cases`, P1). The corpus fails a candidate only on a regression: a
  case the class's current release passes and the candidate fails, each one named in the
  why. An empty corpus, or a case the release already fails, is not a regression. The
  release's results are computed once per tick per (class, version). Harness candidates
  and pass-through code candidates get a labeled stub. A code candidate the coding agent
  drafted (M8b) replays as a job off the tick (`code_lockin.gate_replay`: device tests +
  held-out verify scripts in fresh containers); it stays `replaying` until the job is done,
  and the tick never waits for it. With no held-out verify scripts it also stays
  `replaying`, with one `candidate.awaiting_verify` event, and replays once they exist
  (in-process device tests alone can be forged); `CODER_ALLOW_UNVERIFIED=true` lets them
  decide, and each such replay records a `candidate.unverified` warning.
- **canary** (`start_canary`): assign the class's `role = canary` device, remember what it
  had (`canary_prev_version`), set `canary_started_at = now`. Evidence is that device's
  check-ins (`canary_checkins`), install reports and incidents received since then, on the
  server clock. An overlay counts only if it is new since the run started. A
  `touches_safety` candidate only uses a canary whose kind is `sim` or `test` (K17); any
  other kind, including an auto-registered `unknown`, counts as real (fail closed).
- **promote:** assign the candidate to every device of its class.
- **roll back** (a real replay or canary failure): give the canary back its previous
  assignment (not `last_good`, which can already be the bad candidate in F4's late path),
  withdraw live candidates sharing its overlays, and settle the failed candidate's
  overlays so no fix is lost without a sticky why:
  - from one overlay: `lockin_rolled_back`, sticky, never re-batched; the device keeps
    its local fix;
  - folded from several: a bounded bisect. Each goes back to `held` marked `solo` and
    the batch builds it alone; one already tried solo is sticky;
  - an unproven failure (a repeat of unknown version seen on unfreeze): like stale, each
    goes back to `held` for one retry (`retried`); one already retried is sticky.
- **freeze:** while the kill switch is on nothing moves; frozen candidates restart from
  replay on unfreeze, with a fresh canary window (F5). A candidate whose canary repeated
  one of its incidents' kinds on its version since the run started (including while
  frozen, outside any window) is rolled back on unfreeze instead.

No LLM is called here.
"""

import logging
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
    Overlay,
)
from romulus_cloud.services import code_lockin, datasets, fleet, incidents, registry
from romulus_cloud.services.fleet import install_type
from romulus_cloud.settings import Settings, get_settings

log = logging.getLogger(__name__)

ACTOR = "gate"
LIVE = fleet.LIVE
LOCKIN_ROLLED_BACK = "lockin_rolled_back"
LOCKIN_ERROR = "lockin_error"
CORPUS_CACHE = "gate.release_corpus"  # session.info key, set for one tick
CODE_TOOLS = "gate.code_tools"  # session.info key: the tick's code_lockin.ReplayTools
STALE_RETRY_CAP = 3  # stale re-queues per overlay before it is sticky (PR #18 review)
TRANSITION_KINDS = {
    "canary": "candidate.canary_started",
    "awaiting_canary": "candidate.awaiting_canary",
    "promoted": "candidate.promoted",
    "rolled_back": "candidate.rolled_back",
    "frozen": "candidate.frozen",
    "replaying": "candidate.restarted",
}
K17_WAIT = (
    "it touches the safety layer or visor and the only canary is a real arm (or not known "
    "to be simulated); safety patches "
    "go to a simulated canary first (K17)"
)


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


def _overlays(session: Session, c: Candidate) -> list[Overlay]:
    rows = (session.get(Overlay, (r["device_id"], r["overlay_id"])) for r in c.from_overlays or [])
    return [o for o in rows if o is not None]


def _incident_ids(session: Session, c: Candidate) -> list[str]:
    ids = [c.incident_id] if c.incident_id else []
    ids += [o.incident_id for o in _overlays(session, c) if o.incident_id]
    return list(dict.fromkeys(ids))


def _refs(session: Session, c: Candidate, device: str | None = None) -> dict[str, Any]:
    """`incident` is the first incident; `incidents` lists every folded one, so each of
    their stories shows the gate's steps."""
    refs: dict[str, Any] = {"candidate": c.id, install_type(c): c.version}
    ids = _incident_ids(session, c)
    if ids:
        refs["incident"], refs["incidents"] = ids[0], ids
    if c.touches_safety:
        refs["touches_safety"] = True
    if device:
        refs["device"] = device
    return refs


def _record(session: Session, c: Candidate, kind: str, why: str, now: datetime, **kw) -> None:
    events.record(
        session,
        ACTOR,
        "automation",
        kind,
        _refs(session, c, kw.pop("device", None)),
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


SIMULATED_KINDS = ("sim", "test")


def _maybe_real(device: Device) -> bool:
    """Could this device be a real arm? Anything not known to be simulated (`real`, an
    auto-registered `unknown`, a typo) counts as real, so K17 fails closed."""
    return device.kind not in SIMULATED_KINDS


def _canary_busy(session: Session, c: Candidate, device: Device) -> bool:
    """Another candidate of the same install type is already on this canary."""
    others = session.scalars(
        select(Candidate).where(
            Candidate.fleet_id == c.fleet_id,
            Candidate.status == "canary",
            Candidate.canary_device == device.id,
            Candidate.id != c.id,
        )
    )
    return any(install_type(o) == install_type(c) for o in others)


def _incidents(session: Session, c: Candidate) -> list[Incident]:
    rows = (session.get(Incident, i) for i in _incident_ids(session, c))
    return [i for i in rows if i is not None]


def _replay(session: Session, c: Candidate) -> g.ReplayResult | code_lockin.AwaitingVerify | None:
    """The replay; None while a code replay job is still running, `AwaitingVerify` while a
    code candidate has no held-out verify scripts."""
    if c.type == "code":
        code = session.info.get(CODE_TOOLS)
        if code is None:
            return g.replay_code_stub()
        return code_lockin.gate_replay(session, c, code)
    if c.type == "harness":
        return g.replay_stub("a harness skill is prose; there are no rules to replay")
    fw = session.get(FirmwareVersion, (c.fleet_id, c.version))
    if fw is None:
        return g.ReplayResult(False, f"replay failed: firmware v{c.version} does not exist")
    cases = [
        g.ReplayCase(i.id, i.signals or {}, i.resolving_action, i.kind)
        for i in _incidents(session, c)
    ]
    own = g.replay(fw.manifest, cases)
    if not own.passed:
        return own
    corpus = _corpus(session, c, fw.manifest)
    return g.ReplayResult(corpus.passed, f"{own.why}; {corpus.why}", own.cases + corpus.cases)


def _release_corpus(
    session: Session, c: Candidate
) -> tuple[list[g.ReplayCase], set[str] | None, int | None]:
    """The class's regression corpus, the ids of the cases its current firmware release
    passes (None: that release can't be replayed, so every case counts; fail closed), and
    that release. Cached for the tick in `session.info` when `tick` runs."""
    release, _ = registry.default_assignment(session, c.device_class, c.fleet_id)
    cache = session.info.get(CORPUS_CACHE)
    key = (c.fleet_id, c.device_class, release)
    if cache is not None and key in cache:
        return cache[key]
    cases = datasets.regression_cases(session, c.fleet_id, c.device_class)
    fw = session.get(FirmwareVersion, (c.fleet_id, release)) if release is not None else None
    passing: set[str] | None = None
    if fw is not None and cases:
        try:
            base = g.replay(fw.manifest, cases)
        except Exception:  # a broken release manifest: count every case
            base = None
        if base is not None and base.cases:
            passing = {r.case_id for r in base.cases if r.passed}
    found = (cases, passing, release)
    if cache is not None:
        cache[key] = found
    return found


def _corpus(session: Session, c: Candidate, manifest: dict[str, Any]) -> g.ReplayResult:
    """Replay the regression corpus (minus the candidate's own incidents, replayed above);
    fail only on a regression against the current release."""
    corpus, passing, release = _release_corpus(session, c)
    own = set(_incident_ids(session, c))
    cases = [k for k in corpus if k.case_id not in own]
    if not cases:
        return g.ReplayResult(True, "regression corpus: no cases yet, so nothing can regress")
    if passing is None:
        counted, note = cases, f"; firmware {release} could not be replayed, so every case counts"
    else:
        counted = [k for k in cases if k.case_id in passing]
        old = len(cases) - len(counted)
        note = f"; {old} already fail on firmware {release}, so they can't regress" if old else ""
    if not counted:
        return g.ReplayResult(
            True, f"regression corpus: all {len(cases)} cases already fail on firmware {release}"
        )
    result = datasets.replay_corpus(manifest, counted)
    return g.ReplayResult(result.passed, result.why + note, result.cases)


def _version(c: Candidate, firmware: int | None, harness: int | None) -> int | None:
    return harness if install_type(c) == "harness" else firmware


def _already_installed(session: Session, c: Candidate, started: datetime) -> g.InstallObs | None:
    """An install observation at `started` when the canary already runs the candidate.

    After an unfreeze the canary can still run the candidate from its first run: while
    frozen the device only moves to its own last good version, and it may not check in
    between the restart and the new canary. Then the earlier `installed` report counts as
    of `started`; every health check-in must still be fresh (F5).
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


def _overlay_at_start(session: Session, device: str | None, started: datetime) -> str | None:
    """The overlay the canary had when this run started (its last check-in before)."""
    return session.scalar(
        select(CanaryCheckin.overlay_id)
        .where(CanaryCheckin.device_id == device, CanaryCheckin.at < started)
        .order_by(CanaryCheckin.at.desc())
        .limit(1)
    )


def _observations(session: Session, c: Candidate, started: datetime) -> list[g.CanaryObs]:
    """The canary's check-ins, installs and incidents since `started` (server clock)."""
    device = c.canary_device
    # An overlay already there at the start belongs to something else (e.g. its own fix
    # from an earlier incident); only one that appears during the run says the candidate
    # needed patching.
    before = _overlay_at_start(session, device, started)
    obs: list[g.CanaryObs] = []
    for ck in session.scalars(
        select(CanaryCheckin).where(CanaryCheckin.device_id == device, CanaryCheckin.at >= started)
    ):
        obs.append(
            g.CheckinObs(
                at=ck.at,
                health=ck.health,
                version=_version(c, ck.firmware, ck.harness),
                overlay_id=None if ck.overlay_id == before else ck.overlay_id,
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
) -> tuple[g.GateInputs, bool]:
    """The domain's inputs, and whether K17 is what keeps the candidate off the canary."""
    canary = _canary(session, c)
    available = canary is not None
    k17 = bool(canary and c.touches_safety and _maybe_real(canary) and c.status != "canary")
    if k17 or (canary is not None and c.status != "canary" and _canary_busy(session, c, canary)):
        available = False
    started = c.canary_started_at
    inputs = g.GateInputs(
        frozen=frozen,
        kind=c.type,
        replay=replay,
        canary_available=available,
        # True unless the coding agent drafted it (M8b); fails closed.
        passthrough=c.type != "code" or code_lockin.is_passthrough(session, c),
        # Unlocks code promotion, so only a canary known to be real counts (fail closed).
        real_code_canary=bool(canary and canary.kind == "real"),
        allow_passthrough_code_promotion=settings.allow_passthrough_code_promotion,
        candidate_version=c.version,
        install_type=install_type(c),
        canary_observations=tuple(_observations(session, c, started)) if started else (),
        # In `frozen` too: on unfreeze a repeat since the run started fails it (F5).
        canary_started_at=started if c.status in ("canary", "frozen") else None,
        canary_window_s=settings.canary_window_s,
        repeat_kinds=frozenset(i.kind for i in _incidents(session, c)),
        touches_safety=c.touches_safety,
    )
    return inputs, k17


# --- applying a transition -----------------------------------------------------------


def _assigned(d: Device, c: Candidate) -> int | None:
    return getattr(d, f"assigned_{install_type(c)}")


def _assign(d: Device, c: Candidate, version: int | None) -> None:
    setattr(d, f"assigned_{install_type(c)}", version)


def _start_canary(session: Session, c: Candidate, now: datetime) -> Device:
    canary = _canary(session, c)
    assert canary is not None  # next_state only starts a canary when one is available
    current = _assigned(canary, c)
    if current != c.version:
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


def _with_overlay(session: Session, ref: dict, statuses) -> list[Candidate]:
    return list(
        session.scalars(
            select(Candidate).where(
                Candidate.from_overlays.contains([ref]), Candidate.status.in_(statuses)
            )
        )
    )


def _siblings(session: Session, c: Candidate) -> list[Candidate]:
    """Live candidates locked in from any of the same overlays."""
    found = {s.id: s for ref in c.from_overlays or [] for s in _with_overlay(session, ref, LIVE)}
    found.pop(c.id, None)
    return list(found.values())


def _close(session: Session, c: Candidate, status: str, kind: str, why: str, now) -> None:
    where = _restore_canary(session, c)
    c.status, c.updated_at = status, now
    c.verdict = {"status": "fail" if status == "rolled_back" else status, "why": why}
    verb = {"rolled_back": "rolled back", "stale": "withdrawn as stale", "error": "stopped"}
    _record(
        session,
        c,
        kind,
        f"{label(c).capitalize()} {verb[status]}: {why}; {where}.",
        now,
        device=c.canary_device,
        restored=c.canary_prev_version,
    )


Key = tuple[str, str]  # (device_id, overlay_id)


def _keys(c: Candidate) -> list[Key]:
    return [(r["device_id"], r["overlay_id"]) for r in c.from_overlays or []]


def _marked(session: Session, keys: list[Key], mark: str) -> set[Key]:
    """The overlays among `keys` already carrying a re-queue `mark` (`solo`, `retried`)."""
    found = set()
    for key in keys:
        ov = session.get(Overlay, key)
        if ov is not None and incidents.requeue_marks(session, ov).get(mark):
            found.add(key)
    return found


def _settle_overlays(
    session: Session,
    closed: list[Candidate],
    now: datetime,
    *,
    failed: set[Key],
    failed_status: str,
    cause: str,
    failed_cause: str | None = None,
    marks: dict[Key, dict[str, bool]] | None = None,
) -> None:
    """After `closed` candidates end without promotion: overlays in `failed` get the sticky
    `failed_status` (with `failed_cause`, default `cause`); the others go back to `held`
    (re-batched) unless another live or promoted candidate still carries them. A re-queue
    event carries the overlay's `marks` (`solo`: batch it alone; `retried`: its one retry
    is used), read back by `incidents.requeue_marks`."""
    closing = {c.id for c in closed}
    seen: set[Key] = set()
    marks = marks or {}
    for c in closed:
        for ref in c.from_overlays or []:
            key = (ref["device_id"], ref["overlay_id"])
            ov = session.get(Overlay, key)
            if key in seen or ov is None or ov.status != "locked_in":
                continue
            seen.add(key)
            refs: dict[str, Any] = {"device": ov.device_id, "overlay": ov.id, "candidate": c.id}
            if ov.incident_id:
                refs["incident"] = ov.incident_id
            if key in failed:
                why = failed_cause or cause
                ov.status, ov.status_reason, ov.updated_at = failed_status, why, now
                what = "lock-in rolled back" if failed_status == LOCKIN_ROLLED_BACK else "error"
                events.record(
                    session,
                    ACTOR,
                    "automation",
                    f"overlay.{failed_status}",
                    refs,
                    f"Overlay {ov.id} on {ov.device_id}: {what} ({why}). It is never "
                    "re-batched; the device keeps its local fix.",
                    ts=now,
                    fleet_id=ov.fleet_id,
                )
                continue
            others = [
                o for o in _with_overlay(session, ref, (*LIVE, "promoted")) if o.id not in closing
            ]
            if others:
                continue
            mark = marks.get(key, {})
            ov.status, ov.candidate_id, ov.updated_at = "held", None, now
            ov.status_reason = f"re-queued: {cause}"
            note = " It is batched alone next (bisect)." if mark.get("solo") else ""
            if mark.get("retried"):
                note += " This is its one retry."
            if mark.get("stale"):
                note += f" Stale re-queue {mark['stale']} of {STALE_RETRY_CAP}."
            events.record(
                session,
                ACTOR,
                "automation",
                "overlay.requeued",
                refs,
                f"Overlay {ov.id} on {ov.device_id} is held again for the next batch: "
                f"{cause}.{note}",
                data={"marks": mark} if mark else None,
                ts=now,
                fleet_id=ov.fleet_id,
            )
    session.flush()


def _close_with_siblings(session: Session, c: Candidate, why: str, now) -> list[Candidate]:
    _close(session, c, "rolled_back", "candidate.rolled_back", why, now)
    siblings = _siblings(session, c)
    for s in siblings:
        _close(
            session,
            s,
            "rolled_back",
            "candidate.rolled_back",
            f"withdrawn: {label(c)} from the same overlay(s) was rolled back",
            now,
        )
    return siblings


def _fail(session: Session, c: Candidate, why: str, now: datetime) -> None:
    """A real replay, corpus or canary failure.

    A candidate from one overlay: that overlay is sticky (`lockin_rolled_back`). A
    candidate folded from several: we can't tell which one broke it, so each goes back to
    `held` marked `solo` and the batch builds it alone (a bisect). An overlay already
    tried solo is sticky instead, so each is tried solo at most once.
    """
    siblings = _close_with_siblings(session, c, why, now)
    keys = _keys(c)
    cause = f"{label(c)} was rolled back: {why}"
    if len(keys) <= 1:
        failed, solo = set(keys), set()
    else:
        failed = _marked(session, keys, "solo")
        solo = set(keys) - failed
        if solo:
            cause += f"; it folded {len(keys)} overlays, so each is retried alone once"
    _settle_overlays(
        session,
        [c, *siblings],
        now,
        failed=failed,
        failed_status=LOCKIN_ROLLED_BACK,
        cause=cause,
        failed_cause=f"{label(c)} was rolled back: {why}",
        marks={k: {"solo": True} for k in solo},
    )


def _fail_unproven(session: Session, c: Candidate, why: str, now: datetime) -> None:
    """Rolled back on evidence that may not be the candidate's (a repeat of unknown
    version seen on unfreeze). Like stale for its overlays: each goes back to `held` for
    one retry; one already retried this way is sticky."""
    siblings = _close_with_siblings(session, c, why, now)
    keys = _keys(c)
    failed = _marked(session, keys, "retried")
    _settle_overlays(
        session,
        [c, *siblings],
        now,
        failed=failed,
        failed_status=LOCKIN_ROLLED_BACK,
        cause=f"{label(c)} was rolled back on an unproven cause ({why}); retried once",
        failed_cause=f"{label(c)} was rolled back: {why}; its one retry is already used",
        marks={k: {"retried": True} for k in set(keys) - failed},
    )


def _go_stale(session: Session, c: Candidate, why: str, now: datetime) -> None:
    """Not a failure: the base moved. Its overlays re-batch onto the new base."""
    _close(session, c, "stale", "candidate.stale", why, now)
    siblings = _siblings(session, c)
    for s in siblings:
        _close(
            session,
            s,
            "stale",
            "candidate.stale",
            f"withdrawn with {label(c)}: its overlays re-batch onto the new base",
            now,
        )
    # Belt and braces: a fix whose candidates keep going stale stops re-batching after
    # STALE_RETRY_CAP rounds, with a sticky why, so nothing is rebuilt all night.
    keys = list(dict.fromkeys(k for x in (c, *siblings) for k in _keys(x)))
    rounds = {k: _stale_rounds(session, k) for k in keys}
    capped = {k for k, n in rounds.items() if n >= STALE_RETRY_CAP}
    _settle_overlays(
        session,
        [c, *siblings],
        now,
        failed=capped,
        failed_status=LOCKIN_ERROR,
        cause=f"{label(c)} went stale ({why})",
        failed_cause=f"{label(c)} went stale ({why}); its candidates went stale "
        f"{STALE_RETRY_CAP} times already, so it stops re-batching (stale cap)",
        marks={k: {"stale": n + 1} for k, n in rounds.items() if k not in capped},
    )


def _stale_rounds(session: Session, key: Key) -> int:
    ov = session.get(Overlay, key)
    return int(incidents.requeue_marks(session, ov).get("stale") or 0) if ov else 0


def _apply(session: Session, c: Candidate, t: g.Transition, now: datetime) -> None:
    old = c.status
    if t.state == "rolled_back":
        (_fail_unproven if t.unproven else _fail)(session, c, t.why, now)
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
        # Off the canary: the next run starts fresh, and a freeze in between doesn't count
        # the canary's incidents against it (`repeat_since`).
        c.canary_started_at = None
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
        f"runs {kind} {current}"
    )


def _step(session: Session, c: Candidate, settings: Settings, frozen: bool, now) -> bool:
    """Move one candidate one step. Returns whether its state changed."""
    if c.status in ("replaying", "awaiting_canary") and not frozen:
        stale = _stale(session, c)
        if stale:
            _go_stale(session, c, stale, now)
            return True
    replay = g.ReplayResult.from_json(c.replay)
    if c.status == "replaying" and replay is None and not frozen:
        replay = _replay(session, c)
        if isinstance(replay, code_lockin.AwaitingVerify):
            _await_verify(session, c, replay.why, now)
            replay = None
        if replay is not None:  # None: a code replay job still running; check next tick
            c.replay = replay.to_json()
            if replay.stub and "replay" not in (c.stub_stages or []):
                c.stub_stages = [*(c.stub_stages or []), "replay"]
            code = session.info.get(CODE_TOOLS)
            output = code_lockin.gate_replay_applied(c, code) if code and c.type == "code" else {}
            _record(session, c, "candidate.replayed", replay.why, now, replay=c.replay, **output)
            if output.get("replay_unverified") and replay.passed:
                _record(
                    session,
                    c,
                    "candidate.unverified",
                    f"WARNING {code_lockin.UNVERIFIED}: {label(c)} passed its replay with no "
                    "held-out verify scripts, because CODER_ALLOW_UNVERIFIED=true; code under "
                    "test can forge in-process results, so run `make device-verify`",
                    now,
                    level="warning",
                )
    inputs, k17 = _inputs(session, c, settings, frozen, replay)
    t = g.next_state(c.status, inputs, now)
    if t.state == c.status:
        return False
    if k17 and t.state == "awaiting_canary":
        t = t._replace(why=t.why.replace("no canary device is available", K17_WAIT))
    _apply(session, c, t, now)
    return True


def _await_verify(session: Session, c: Candidate, why: str, now: datetime) -> None:
    """The candidate waits in `replaying` for held-out verify scripts: one event per
    candidate (not one per tick), no failure, no retry count."""
    if not events.find(session, {"candidate": c.id}, kinds=["candidate.awaiting_verify"]):
        _record(
            session, c, "candidate.awaiting_verify", f"{label(c).capitalize()} waits: {why}", now
        )


def _error(session: Session, candidate_id: str, exc: Exception, now: datetime) -> None:
    """Stop a candidate whose step raised, with the reason; its overlays get `lockin_error`."""
    try:
        with session.begin_nested():
            c = session.get(Candidate, candidate_id)
            if c is None or c.status not in LIVE:
                return
            why = f"gate error: {exc!r}"
            _close(session, c, "error", "candidate.error", why, now)
            failed = {(r["device_id"], r["overlay_id"]) for r in c.from_overlays or []}
            _settle_overlays(
                session, [c], now, failed=failed, failed_status=LOCKIN_ERROR, cause=why
            )
    except Exception:  # the row itself is broken; leave it for a person, keep going
        log.exception("gate: could not mark candidate %s as error", candidate_id)


def tick(
    session: Session,
    now: datetime,
    *,
    settings: Settings | None = None,
    code: code_lockin.ReplayTools | None = None,
) -> list[str]:
    """The worker step. Returns the ids of candidates that changed state. `code` runs code
    replays as jobs (default: from settings)."""
    settings = settings or get_settings()
    frozen = fleet.state(session, settings.fleet_id, lock=True).frozen
    stmt = select(Candidate.id).where(
        Candidate.fleet_id == settings.fleet_id, Candidate.status.in_(LIVE)
    )
    changed = []
    session.info[CORPUS_CACHE] = {}
    session.info[CODE_TOOLS] = code or code_lockin.ReplayTools.from_settings(settings)
    try:
        for cid in session.scalars(stmt.order_by(Candidate.created_at, Candidate.type)).all():
            c = session.get(Candidate, cid)
            if c is None or c.status not in LIVE:  # closed with a sibling earlier in this tick
                continue
            try:
                with session.begin_nested():
                    if _step(session, c, settings, frozen, now):
                        changed.append(cid)
            except Exception as exc:  # one bad candidate must not stall the others
                _error(session, cid, exc, now)
                changed.append(cid)
    finally:
        session.info.pop(CORPUS_CACHE, None)
        session.info.pop(CODE_TOOLS, None)
    session.flush()
    return changed
