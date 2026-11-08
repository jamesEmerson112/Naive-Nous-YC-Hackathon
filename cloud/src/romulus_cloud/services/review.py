"""Fleet learning, cluster review: a Fleet Doctor mode that runs as one Naïve session
(docs/recursive-self-improvement/design.md §4, §5; draft D31).

`tick(session, now)` runs from the `learning.tick` worker step when LEARNING_MODE is
`fixture` (a recorded session per kind) or `live` (HttpNaive):
1. Fleet frozen (D11) → nothing.
2. A review session is open (a `review.session_opened` with no outcome yet) → poll it. When
   it ends, `domain.review.check` decides and the outcome lands in one transaction:
   - `review.staged`: anything safety-touching, and every passing rule (D31: the rule lane
     is T2 and not built);
   - `review.accepted`: a skill that names no safety path. It waits while any harness
     candidate is live (one per canary, `gate._canary_busy`), then becomes harness
     v(N+1) = the class's current default + `skills/<slug>.md` and a
     `Candidate(type="harness", from_overlays=[])`. The gate takes it from there: stub
     replay, canary on the class's canary, promote to that class only;
   - `review.rejected`: a failed check, or a session that ended without a proposal. The
     kind cools down for REVIEW_COOLDOWN_S, or until a newer run of it arrives.
3. Otherwise: labelled runs → `qualify` (transfer or generalize) → the first cluster within
   the caps → case file → one session, with `metadata.romulus_case` = the idempotency key =
   `review:<kind>:<n>`, found again with `find_session` after a crash.

Pending needs no table: `review.session_opened` carries the session id, the frozen request
and the case file. A kind with a staged or accepted proposal waits for a newer run; a kind
whose review-born candidate was rolled back is paused until `clear` (D31). Every write is
actor `agent:fleet-doctor`, class `agent`. Nothing here commits; the worker step does.
"""

import json
from collections.abc import Callable, Iterable
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from romulus_cloud import events
from romulus_cloud.adapters import naive as naive_adapter
from romulus_cloud.adapters.blobstore import build_blobstore
from romulus_cloud.adapters.naive import NaiveError
from romulus_cloud.domain import datasets as dd
from romulus_cloud.domain import review as rv
from romulus_cloud.domain import technician as tech_rules
from romulus_cloud.ids import new_id, utcnow
from romulus_cloud.log import log_line
from romulus_cloud.models import Candidate, Device, Event, FleetState, HarnessVersion
from romulus_cloud.services import doctor_spend, fleet, registry
from romulus_cloud.services import runs as labelled
from romulus_cloud.services.batch import _read_tarball
from romulus_cloud.settings import Settings, get_settings

ACTOR = "agent:fleet-doctor"
OPENED = "review.session_opened"
OUTCOMES = ("review.staged", "review.accepted", "review.rejected")
OUTCOME_NAMES = ("staged", "accepted", "rejected")  # ReviewResult.outcome, in OUTCOMES order
CLEARED = "review.cleared"
KINDS = (OPENED, *OUTCOMES, CLEARED)
MODES = ("fixture", "live")
HARNESS_CLASSES = ("so101",)  # lessons install on the arms; pods install nothing (§5)
DEFAULT_AGENT = "fleet-doctor"
TIMEOUT_S = 600.0  # a session with no proposal by then is rejected
NAIVE_TIMEOUT_S = 5.0
RULE_LANE_NOTE = "the rule lane isn't built yet (T2)"
FIXTURES = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "review"
RESULT_FIELDS = (
    "outcome",
    "why",
    "failures",
    "touches_safety",
    "targets",
    "lane",
    "owner",
    "staged_patch",
    "probe",
    "scorecard",
)

_clients: dict[tuple[Any, ...], Any] = {}

LoadRuns = Callable[[Session, datetime], list[Any]]


# --- reading state -----------------------------------------------------------------------------


def _frozen(session: Session, fleet_id: str) -> bool:
    state = session.get(FleetState, fleet_id)
    return bool(state and state.frozen)


def review_events(session: Session, fleet_id: str) -> list[Event]:
    """Every `review.*` event in the fleet, oldest first."""
    stmt = (
        select(Event)
        .where(Event.fleet_id == fleet_id, Event.kind.in_(KINDS))
        .order_by(Event.ts, Event.recorded_at, Event.id)
    )
    return list(session.scalars(stmt))


def open_session(evs: Iterable[Event]) -> Event | None:
    """The newest `review.session_opened` with no outcome for its case yet."""
    evs = list(evs)
    done = {e.refs.get("review") for e in evs if e.kind in OUTCOMES}
    opened = [e for e in evs if e.kind == OPENED and e.refs.get("review") not in done]
    return opened[-1] if opened else None


def load_probes(settings: Settings) -> list[dict[str, Any]]:
    """The probe registry: every probe JSON in PROBE_DIR (design §6), by its `probe` id."""
    folder = Path(settings.probe_dir)
    found: list[dict[str, Any]] = []
    if not folder.is_dir():
        return found
    for path in sorted(folder.glob("*.json")):
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and data.get("probe") and isinstance(data.get("port"), dict):
            found.append(data)
    return found


def handled_kinds(probes: Iterable[dict[str, Any]]) -> dict[str, str]:
    """kind → the detector that already names it, from the probes' port specs."""
    out: dict[str, str] = {}
    for p in probes:
        src = (p.get("port") or {}).get("from")
        if p.get("kind") and src:
            out.setdefault(str(p["kind"]), f"the detector at {src}")
    return out


def _current_harness(session: Session, device_class: str, fleet_id: str) -> HarnessVersion | None:
    _, version = registry.default_assignment(session, device_class, fleet_id)
    return session.get(HarnessVersion, (fleet_id, version)) if version is not None else None


def skill_slugs(session: Session, settings: Settings, evs: Iterable[Event] = ()) -> list[str]:
    """Skill names in the arms' current harness (`skills/<slug>.md`), plus any skill an
    earlier review already accepted, so a proposal can't reuse one."""
    slugs: set[str] = set()
    for cls in HARNESS_CLASSES:
        hv = _current_harness(session, cls, settings.fleet_id)
        if hv is None:
            continue
        try:
            files = _read_tarball(build_blobstore(settings.blob_dir), hv.sha256)
        except Exception as exc:  # noqa: BLE001 - a missing blob shouldn't stop review
            log_line("review.harness_unreadable", version=hv.version, error=repr(exc))
            continue
        meta = json.loads(files.get("harness.json", b"{}") or b"{}")
        folder = (meta.get("knowledge_dir") or "skills/").rstrip("/") + "/"
        for name in files:
            if name.startswith(folder) and name.endswith(".md") and "/" not in name[len(folder) :]:
                slugs.add(name[len(folder) : -3])
    for e in evs:
        if e.kind == "review.accepted":
            prop = ((e.data or {}).get("proposal") or {}).get("proposal") or {}
            if prop.get("slug"):
                slugs.add(str(prop["slug"]))
    return sorted(slugs)


def _device_classes(session: Session, fleet_id: str) -> list[str]:
    stmt = select(Device.device_class).where(Device.fleet_id == fleet_id).distinct()
    return sorted(c for c in session.scalars(stmt) if c)


def skip_sets(
    session: Session, evs: Iterable[Event], runs: Iterable[Any], now: datetime, settings: Settings
) -> tuple[set[str], set[str]]:
    """(cooling, paused) kinds.

    - cooling: a `review.rejected` less than REVIEW_COOLDOWN_S ago, or a staged or accepted
      proposal (it waits for its owner or the gate); either way, until a newer run arrives.
    - paused: a review-born candidate was rolled back, and no person has cleared the kind
      since (`review.cleared`, D31).
    """
    newest: dict[str, datetime] = {}
    for r in runs:
        if r.verdict != "excluded" and r.kind:
            newest[r.kind] = max(newest.get(r.kind, r.started_at), r.started_at)
    evs = list(evs)
    cooling: set[str] = set()
    paused: set[str] = set()
    for e in evs:
        kind = str((e.data or {}).get("kind") or "")
        if e.kind not in OUTCOMES or not kind:
            continue
        if kind in newest and newest[kind] > e.ts:
            continue
        if e.kind != "review.rejected" or (now - e.ts).total_seconds() < settings.review_cooldown_s:
            cooling.add(kind)
    for e in evs:
        cand_id = e.refs.get("candidate")
        if e.kind != "review.accepted" or not cand_id:
            continue
        cand = session.get(Candidate, cand_id)
        if cand is None or cand.status != "rolled_back":
            continue
        kind = str((e.data or {}).get("kind") or "")
        since = cand.updated_at or e.ts
        if not any(
            c.kind == CLEARED and (c.data or {}).get("kind") == kind and c.ts >= since for c in evs
        ):
            paused.add(kind)
    return cooling, paused


def _harness_live(session: Session, fleet_id: str) -> Candidate | None:
    stmt = select(Candidate).where(
        Candidate.fleet_id == fleet_id,
        Candidate.type == "harness",
        Candidate.status.in_(fleet.LIVE),
    )
    return session.scalars(stmt.order_by(Candidate.created_at)).first()


def spent_today(session: Session, now: datetime, settings: Settings) -> float:
    """Live review spend today (the outcomes' `cost_usd`), recorded sessions excluded."""
    start = now - timedelta(hours=24)
    total = 0.0
    for e in session.scalars(
        select(Event).where(
            Event.fleet_id == settings.fleet_id, Event.kind.in_(OUTCOMES), Event.ts >= start
        )
    ):
        data = e.data or {}
        cost = data.get("cost_usd")
        if not data.get("recorded") and isinstance(cost, int | float) and cost > 0:
            total += float(cost)
    return total


# --- the Naïve client --------------------------------------------------------------------------


def fixture_for(kind: str, settings: Settings) -> Path | None:
    """REVIEW_FIXTURE when set; else the bundled recording for this kind, if any."""
    if settings.review_fixture:
        return Path(settings.review_fixture)
    path = FIXTURES / f"{kind}.json"
    return path if path.is_file() else None


def naive_for(mode: str, kind: str, settings: Settings) -> Any:
    """The client for `mode`, kept for the process (a fixture remembers its sessions); None
    in fixture mode when there's no recording for the kind."""
    if mode == "live":
        key: tuple[Any, ...] = (
            "live",
            settings.naive_api_key.get_secret_value(),
            settings.naive_base_url,
            settings.naive_version,
        )
    else:
        path = fixture_for(kind, settings)
        if path is None or not path.is_file():
            return None
        key = ("fixture", str(path))
    if key not in _clients:
        if mode == "live":
            _clients[key] = naive_adapter.HttpNaive(
                settings.naive_api_key.get_secret_value(),
                settings.naive_board_id,
                base_url=settings.naive_base_url,
                version=settings.naive_version,
                timeout_s=NAIVE_TIMEOUT_S,
            )
        else:
            _clients[key] = naive_adapter.FixtureNaive(Path(key[1]), now=utcnow, play_seconds=0)
    return _clients[key]


# --- opening a session -------------------------------------------------------------------------


def _request(case_file: dict[str, Any], settings: Settings) -> dict[str, Any]:
    probe_ids = [p["id"] for p in case_file.get("probes") or ()]
    return {
        "agent_id": settings.naive_review_agent or DEFAULT_AGENT,
        "message": rv.message_for(case_file),
        "output_schema": rv.proposal_schema(probe_ids),
        "structured_output_required": True,
        "budget_micro_usd": int(round(settings.review_budget_usd * 1_000_000)),
        "metadata": {"romulus_case": case_file["case_id"], "kind": case_file["kind"]},
    }


def _open(
    session: Session,
    cluster: rv.Cluster,
    runs: list[Any],
    evs: list[Event],
    now: datetime,
    settings: Settings,
    client: Any,
    mode: str,
) -> dict[str, Any]:
    n = 1 + sum(1 for e in evs if e.kind == OPENED and (e.data or {}).get("kind") == cluster.kind)
    probes = load_probes(settings)
    probe = next((p for p in probes if p.get("kind") == cluster.kind), None)
    detail = None
    if probe is not None and cluster.handler_run is None:
        detail = {"file": (probe.get("port") or {}).get("from"), "probe": rv.probe_id(probe)}
    case_file = rv.build_case_file(
        cluster,
        runs,
        n=n,
        handler_detail=detail,
        probes=probes,
        skills=skill_slugs(session, settings, evs),
        device_classes=_device_classes(session, settings.fleet_id),
        redact=dd.redact_text,
    )
    case_id = case_file["case_id"]
    body = _request(case_file, settings)
    try:
        state = None
        find = getattr(client, "find_session", None)
        if callable(find):
            state = find(body["agent_id"], case_id)  # after a crash, or a POST held open
        if state is None:
            state = client.create_session(body, idempotency_key=case_id)
    except NaiveError as exc:
        if exc.pending or exc.retryable:
            return {"case": case_id, "status": f"Naïve is busy; retrying next tick ({exc})"}
        raise
    by_id = {r.id: r for r in runs}
    incidents = {
        r["id"]: by_id[r["id"]].incident_id
        for r in case_file["runs"]
        if r["id"] in by_id and getattr(by_id[r["id"]], "incident_id", None)
    }
    refs: dict[str, Any] = {"review": case_id, "naive_session": state.id}
    first = next((incidents[i] for i in cluster.failing if i in incidents), None)
    if first:
        refs["incident"] = first
    recorded = mode == "fixture"
    events.record(
        session,
        ACTOR,
        "agent",
        OPENED,
        refs,
        f"Cluster review {case_id}: {cluster.why} Opened "
        f"{'a recorded' if recorded else 'a live'} Naïve session {state.id}.",
        data={
            "case_id": case_id,
            "kind": cluster.kind,
            "reason": cluster.reason,
            "mode": mode,
            "recorded": recorded,
            "session_id": state.id,
            "agent_id": body["agent_id"],
            "request": body,
            "case_file": case_file,
            "incidents": incidents,
            "opened_at": now.isoformat(),
        },
        ts=now,
        fleet_id=settings.fleet_id,
    )
    return {"case": case_id, "status": "opened", "session_id": state.id}


# --- the outcome ---------------------------------------------------------------------------------


def _result_json(result: rv.ReviewResult) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in RESULT_FIELDS:
        value = getattr(result, key)
        out[key] = list(value) if isinstance(value, tuple) else value
    return out


def _first_incident(output: Any, data: dict[str, Any]) -> str | None:
    incidents = data.get("incidents") or {}
    evidence = (output or {}).get("evidence") if isinstance(output, dict) else None
    for run_id in list(evidence or []) + list(incidents):
        if isinstance(run_id, str) and incidents.get(run_id):
            return str(incidents[run_id])
    return None


def _record_outcome(
    session: Session,
    opened: Event,
    output: Any,
    result: rv.ReviewResult,
    state: Any,
    now: datetime,
    settings: Settings,
    why: str,
    candidate: Candidate | None = None,
) -> Event:
    data = opened.data or {}
    case_id = str(opened.refs.get("review"))
    refs: dict[str, Any] = {"review": case_id, "naive_session": data.get("session_id")}
    incident = _first_incident(output, data)
    if incident:
        refs["incident"] = incident
    if candidate is not None:
        refs["candidate"] = candidate.id
    cost = getattr(state, "cost_usd", None) if state is not None else None
    return events.record(
        session,
        ACTOR,
        "agent",
        OUTCOMES[OUTCOME_NAMES.index(result.outcome)],
        refs,
        why,
        data={
            "proposal": output if isinstance(output, dict) else None,
            "result": _result_json(result),
            "session_id": data.get("session_id"),
            "duration_s": max(0.0, (now - opened.ts).total_seconds()),
            "cost_usd": float(cost) if isinstance(cost, int | float) else None,
            "probe_id": result.probe,
            "case_id": case_id,
            "kind": data.get("kind"),
            "recorded": bool(data.get("recorded")),
        },
        ts=now,
        fleet_id=settings.fleet_id,
    )


def _skill_candidate(
    session: Session,
    output: dict[str, Any],
    result: rv.ReviewResult,
    opened: Event,
    now: datetime,
    settings: Settings,
) -> tuple[Candidate | None, str]:
    """Harness v(N+1) = the class's current default + `skills/<slug>.md`, and its candidate.
    → (candidate or None, a sentence for the outcome's why)."""
    data = opened.data or {}
    case_id = str(opened.refs.get("review"))
    classes = [c for c in output.get("applies_to") or [] if c in HARNESS_CLASSES]
    if not classes:
        return None, (
            f"no candidate: it applies to {', '.join(output.get('applies_to') or [])}, and only "
            f"{', '.join(HARNESS_CLASSES)} devices install a harness (pods install nothing)"
        )
    device_class = classes[0]
    current = _current_harness(session, device_class, settings.fleet_id)
    if current is None:
        return None, f"no candidate: no harness is assigned to class {device_class}"
    store = build_blobstore(settings.blob_dir)
    files = _read_tarball(store, current.sha256)
    meta = json.loads(files.get("harness.json", b"{}") or b"{}")
    top = session.scalar(
        select(func.max(HarnessVersion.version)).where(HarnessVersion.fleet_id == settings.fleet_id)
    )
    version = (top or 0) + 1
    skill = output["proposal"]
    name = (meta.get("knowledge_dir") or "skills/").rstrip("/") + f"/{skill['slug']}.md"
    files[name] = str(skill["markdown"]).encode()
    files["harness.json"] = json.dumps({**meta, "version": version}, indent=2).encode() + b"\n"
    session_id = data.get("session_id")
    registry.create_harness(
        session,
        store,
        registry.build_tarball(files),
        actor=ACTOR,
        actor_class="agent",
        why=f"Harness v{version} = v{current.version} + {name}, proposed by cluster review "
        f"{case_id} (Naïve session {session_id}).",
        source="review",
        parent_version=current.version,
        fleet_id=settings.fleet_id,
    )
    incident = _first_incident(output, data)
    cand = Candidate(
        id=new_id("cand"),
        fleet_id=settings.fleet_id,
        type="harness",
        version=version,
        code_rev=None,
        device_class=device_class,
        from_overlays=[],
        status="replaying",
        why=f"v{current.version} + skill {skill['slug']} from cluster review {case_id}: "
        f"{output.get('title', '')}",
        touches_safety=False,
        stub_stages=[],
        incident_id=incident,
        created_at=now,
        updated_at=now,
    )
    session.add(cand)
    session.flush()
    refs: dict[str, Any] = {"candidate": cand.id, "review": case_id, "harness": version}
    if incident:
        refs["incident"] = incident
    events.record(
        session,
        ACTOR,
        "agent",
        "candidate.created",
        refs,
        f"Candidate harness v{version} for {device_class}: v{current.version} + {name}, a skill "
        f"from cluster review {case_id}. The gate replays it (a stub: a skill is prose), then "
        f"canaries it before every {device_class} device gets it.",
        data={
            "type": "harness",
            "version": version,
            "code_rev": None,
            "stub_stages": [],
            "overlays": [],
            "parent_version": current.version,
            "skills": [name],
            "source": "review",
            "case_id": case_id,
            "proposal": output,
            "skill": {"slug": skill["slug"], "markdown": skill["markdown"]},
            "scorecard": result.scorecard,
        },
        ts=now,
        fleet_id=settings.fleet_id,
    )
    return cand, f"harness v{version} candidate {cand.id} goes to the gate for {device_class}"


def _poll(
    session: Session, opened: Event, now: datetime, settings: Settings, client: Any
) -> dict[str, Any]:
    data = opened.data or {}
    case_id = str(opened.refs.get("review"))
    session_id = str(data.get("session_id"))
    try:
        state = client.get_session(session_id)
    except NaiveError as exc:
        if exc.status != 404 or data.get("mode") != "fixture":
            raise
        # A restarted worker lost the recording's sessions: play it again (same key and body).
        state = client.create_session(data["request"], idempotency_key=case_id)
    verdict, problem = tech_rules.session_outcome(
        state=state.state,
        stop_reason=state.stop_reason,
        has_output=isinstance(state.structured_output, dict),
        elapsed_s=(now - opened.ts).total_seconds(),
        timeout_s=TIMEOUT_S,
    )
    if verdict == "wait":
        return {"case": case_id, "status": "waiting for the session"}
    if verdict == "fail":
        if state.state in ("running", "awaiting"):  # timed out: stop it spending
            try:
                client.cancel_session(session_id)
            except Exception as exc:  # noqa: BLE001 - already gone; the outcome still lands
                log_line("review.cancel_failed", case=case_id, error=repr(exc))
        sentence = f"The session ended without a proposal: {problem}."
        result = rv.ReviewResult("rejected", f"Rejected. {sentence}", failures=(sentence,))
        _record_outcome(
            session,
            opened,
            None,
            result,
            state,
            now,
            settings,
            f"Cluster review {case_id}: {sentence} The kind cools down.",
        )
        return {"case": case_id, "status": "rejected"}

    output = state.structured_output
    evs = review_events(session, settings.fleet_id)
    result = rv.check(
        output,
        data.get("case_file") or {},
        load_probes(settings),
        now=now,
        safety_paths=settings.safety_paths,
        existing_skill_slugs=skill_slugs(session, settings, evs),
    )
    title = output.get("title") if isinstance(output, dict) else None
    head = f"Cluster review {case_id}" + (f" ({title})" if title else "")
    if result.outcome == "accepted" and result.lane == "rule":
        why = (
            f"{result.why.removesuffix('.')}; {RULE_LANE_NOTE}, so it's staged for "
            f"{result.owner} with the probe's numbers."
        )
        result = replace(result, outcome="staged", why=why)
    if result.outcome == "accepted" and result.lane == "skill":
        busy = _harness_live(session, settings.fleet_id)
        if busy is not None:
            return {
                "case": case_id,
                "status": f"accepted skill waits: harness candidate {busy.id} is live",
            }
        cand, note = _skill_candidate(session, output, result, opened, now, settings)
        _record_outcome(
            session,
            opened,
            output,
            result,
            state,
            now,
            settings,
            f"{head}: {result.why} {note[0].upper()}{note[1:]}.",
            candidate=cand,
        )
        return {"case": case_id, "status": "accepted", "candidate": cand.id if cand else None}
    if result.outcome == "staged":
        why = f"{head}: {result.why} Patch: {result.staged_patch or 'see the proposal'}"
    else:
        why = f"{head}: {result.why} The kind cools down."
    _record_outcome(session, opened, output, result, state, now, settings, why)
    return {"case": case_id, "status": result.outcome}


# --- the tick ------------------------------------------------------------------------------------


def tick(
    session: Session,
    now: datetime,
    *,
    settings: Settings | None = None,
    naive: Any = None,
    load_runs: LoadRuns | None = None,
    mode: str | None = None,
) -> dict[str, Any] | None:
    """One cluster-review step (see the module docstring). `naive` is one client for every
    case (tests); by default each case gets `naive_for(mode, kind)`. Returns what it did,
    or None when there was nothing to do."""
    settings = settings or get_settings()
    mode = mode or settings.learning_mode
    if mode not in MODES or _frozen(session, settings.fleet_id):
        return None
    evs = review_events(session, settings.fleet_id)
    opened = open_session(evs)
    if opened is not None:
        kind = str((opened.data or {}).get("kind") or "")
        client = naive or naive_for(str((opened.data or {}).get("mode") or mode), kind, settings)
        if client is None:
            return {"case": opened.refs.get("review"), "status": "no client for this session"}
        return _poll(session, opened, now, settings, client)

    hour_ago = now - timedelta(hours=1)
    recent = sum(1 for e in evs if e.kind == OPENED and e.ts > hour_ago)
    if recent >= settings.review_max_sessions_per_hour:
        return {"status": f"cap: {recent} review sessions in the last hour"}
    if mode == "live":
        spent = doctor_spend.spent_today(session, now, settings=settings)
        spent += spent_today(session, now, settings)
        if spent + settings.review_budget_usd > settings.doctor_daily_usd_cap:
            return {"status": f"daily cap: ${spent:.2f} spent by the Doctor and reviews today"}

    if load_runs is not None:
        runs = load_runs(session, now)
    else:
        runs = labelled.load(session, now=now, fleet_id=settings.fleet_id)
    cooling, paused = skip_sets(session, evs, runs, now, settings)
    clusters = rv.qualify(
        runs,
        handled_kinds=handled_kinds(load_probes(settings)),
        cooling=cooling,
        paused=paused,
    )
    for cluster in clusters:
        client = naive or naive_for(mode, cluster.kind, settings)
        if client is None:
            continue  # fixture mode with no recording for this kind
        return _open(session, cluster, runs, evs, now, settings, client, mode)
    return None


def clear(session: Session, kind: str, *, by: str, now: datetime, settings: Settings) -> Event:
    """A person clears a kind paused by a rolled-back proposal (D31)."""
    return events.record(
        session,
        f"human:{by}",
        "human",
        CLEARED,
        {},
        f"{by} cleared {kind} for cluster review again after its proposal was rolled back.",
        data={"kind": kind},
        ts=now,
        fleet_id=settings.fleet_id,
    )
