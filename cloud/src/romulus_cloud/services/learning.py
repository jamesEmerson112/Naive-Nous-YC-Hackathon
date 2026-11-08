"""The Fleet learning page's view-model (docs/recursive-self-improvement/demo.md §4).

`view(session, settings, *, now, case=None)` returns one plain dict, `LearningView` below. It
is the seam between the data lanes and `templates/learning.html`: the template reads only
these keys, and each data source sits behind one small private function, so a lane swaps its
function without touching the page.

Data sources (each a function here):
- **The reviews** (`_reviews`): the `review.*` events R2's service writes
  (`services/review.py`), grouped by `refs.review` (`review:<kind>:<n>`): per case, its latest
  `review.session_opened` (data: case file, session id, kind, recorded) and its latest outcome
  (`review.staged` | `review.accepted` | `review.rejected`; data `{proposal, result{outcome,
  why, failures, touches_safety, targets, lane, owner, staged_patch, probe, scorecard},
  session_id, duration_s, cost_usd, probe_id, case_id, kind, recorded}`, refs `candidate` on an
  accepted skill). The hero is `?case=` or the newest; "Recent reviews" switches between them.
  While the log has none, the recorded session at `settings.review_fixture` (lane A's
  `us_frozen_live.json`) is routed by `domain.review.check`. Neither → the empty state.
- **The mirror** (`_published`): the newest `review.published` for the shown candidate
  (`services/review_publish.py`: skill id, slug, version, url).
- **The probe** (`_probe`): a scorecard JSON in `settings.probe_dir`, matched by its `probe`
  id: `{probe, kind, port{from, to[]}, before{caught, of, false_alarms, healthy}, after{…},
  limits[…], command, rev, generated_at, seconds}`. Every number on the page comes from it.
- **Labelled runs**: `services.runs.load` with `domain.runs.counts` for the data strip, and
  the same runs for the proposal's evidence (verdict word, verifier sentence, SIM).
- **The fleet dataset** (`_fleet_dataset`): the `fleet-runs` dataset and its newest built
  export (`services/datasets.py`), if present.

Honesty rules (demo.md §7) are enforced here, not in the template: numbers are computed,
never typed in; a recorded session says so; staged isn't shipped; a stub replay says "stub";
only installed rollout chips get a tick; the repeat rate is `None` (shown as "—").
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, TypedDict
from urllib.parse import quote
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from romulus_cloud.domain import review as rv
from romulus_cloud.domain import runs as dr
from romulus_cloud.models import (
    Candidate,
    Dataset,
    DatasetVersion,
    Device,
    Event,
    Export,
    RegressionCase,
)
from romulus_cloud.services import runs as runs_service
from romulus_cloud.settings import Settings

# --- the seam: the shape `view()` returns ------------------------------------------------------

StageState = Literal["done", "active", "pending", "failed"]
State = Literal[
    "empty",  # no review yet
    "reviewing",  # a session is open, no outcome yet
    "staged",  # touches safety: waits for its owner (D25)
    "proposed",  # fixture fallback, not safety, no lane wired for it yet
    "rejected",  # the checks failed; nothing ships
    "accepted",  # a skill accepted, candidate not created yet
    "canary",  # its candidate is in replay or on the canary
    "promoted",  # the gate promoted it: every arm
    "rolled_back",  # the canary rolled it back; review paused for the kind
    "stopped",  # its candidate went stale, errored or froze
]


class Head(TypedDict):
    agent: str  # "fleet-doctor"
    mode: str  # "cluster review"
    end_state: str  # "staged for Olir" | "lesson → every arm" | …
    end_first: bool  # promoted: "lesson → every arm · 2 min 40 s · 7¢ · 0 clicks"
    duration_label: str | None  # measured from events (or the recorded session)
    cost_label: str | None  # "7¢"
    clicks_label: str | None  # "0 clicks": human events on the candidate, counted
    recorded: bool  # yellow "recorded" badge
    badge: str | None  # "recorded Naïve session ses_… · 9:31 AM"
    session_id: str | None
    studio_url: str


class Stage(TypedDict):
    label: str
    state: StageState


class Numbers(TypedDict):
    line: str  # "0 → 120 of 120 freezes caught"
    before: int
    after: int
    of: int
    noun: str
    alarms_line: str  # "no new false alarms" | "2 new false alarms"
    new_false_alarms: int
    improved: bool


class ExportView(TypedDict):
    id: str
    format: str
    n: int
    download: str
    sha256: str | None
    sha_short: str
    redact: bool | None
    rows: int | None
    file_label: str  # "trajectories.jsonl" inside the tar


class DataStrip(TypedDict):
    runs_total: int
    labelled: int  # pass + fail
    passed: int
    failed: int
    unverified: int
    excluded: int
    regression_cases: int
    training_rows: int | None  # from the export's manifest; None → "—"
    export: ExportView | None
    dataset: dict[str, Any] | None  # {id, name, versions, url}
    sim_fleet: bool
    fleet_line: str  # "1 real arm · 3 simulated"
    source: str  # where the counts came from


class RecentReview(TypedDict):
    case_id: str
    kind: str  # "us frozen"
    outcome: str  # "staged" | "promoted" | "on the canary" | …
    state: State
    duration_label: str | None
    cost_label: str | None
    url: str  # "/learning?case=review:us_frozen:1"
    current: bool  # the one the hero shows


class LearningView(TypedDict):
    state: State
    origin: Literal["event", "fixture"] | None
    case_id: str | None  # the shown review
    recent: list[RecentReview]  # newest first, at most RECENT_MAX
    head: Head
    stages: list[Stage]
    verdict: str | None  # the largest text: the proposal's pattern (or title)
    subtitle: str | None
    gap_label: str | None  # "missed detection" | "failed heal"
    lane: Literal["rule", "skill"] | None
    touches_safety: bool
    numbers: Numbers | None
    limits: list[str]  # all of the probe's limits, below the fold
    limit_headline: str | None  # the first, up to its colon, above the fold
    routing: str | None  # "safety code → staged for Olir (D25)"
    failures: list[str]
    data: DataStrip
    repeat_rate: float | None  # not built: always None, shown as "—"
    evidence: list[dict[str, Any]]  # {id, url, verdict, verifier, sim, device, here}
    proposal_json: str | None
    probe: dict[str, Any] | None  # {id, command, rev, generated_label, seconds, age, stale, port}
    staged_patch: str | None
    gate: list[dict[str, str]]  # small rows: {label, value}
    rollout: list[dict[str, Any]]  # {device, installed, tag, status}
    published: dict[str, Any] | None  # {slug, version, skill_id, url, title}: Naïve Skills
    partial_url: str  # keeps `?case=` so the 3 s poll stays on the pinned review


# --- constants -------------------------------------------------------------------------------

AGENT = "fleet-doctor"
MODE = "cluster review"
STUDIO_URL = "https://app.usenaive.ai"
DATASET_NAME = "fleet-runs"
REVIEW_KINDS = ("review.session_opened", "review.staged", "review.accepted", "review.rejected")
OUTCOME_KINDS = REVIEW_KINDS[1:]
PROBE_MAX_AGE_S = 24 * 3600  # design §4.5 check 4: a probe is fresh for 24 h
PROBE_MAX_BYTES = 256_000  # a scorecard is a few kB; skip anything big


RULE_STAGES = (
    "one robot knew",
    "Naïve agent found the gap",
    "fix measured",
    "waiting on its engineer",
)
SKILL_STAGES = (
    "one robot knew",
    "Naïve agent found the gap",
    "a sim arm tried it first",
    "lesson → every arm",
)
# A skill that is staged, proposed or rejected never reached the gate, so it never shows
# the gate's stages ("lesson → every arm"): staged means waiting on its owner (D25).
HELD_SKILL_STAGES = (
    "one robot knew",
    "Naïve agent found the gap",
    "checked in code",
    "waiting on its engineer",
)
GATE_STATES = ("accepted", "canary", "promoted", "rolled_back", "stopped")
GAP_LABELS = {"missed_detection": "missed detection", "failed_heal": "failed heal"}
# What the probe's windows are, by a word in its kind or id; else "cases".
NOUNS = (("frozen", "freezes"), ("freeze", "freezes"), ("stall", "stalls"))


# --- small helpers ---------------------------------------------------------------------------


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    """A list field from events, a fixture or a probe: anything else (a number, a string,
    a dict, None) reads as empty, so odd data never 500s the page."""
    return value if isinstance(value, list) else []


def _str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _num(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return int(value)


def _parse_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if not isinstance(value, str) or not value:
        return None
    try:
        at = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return at if at.tzinfo else at.replace(tzinfo=UTC)


def _clock(at: datetime | None, tz: ZoneInfo) -> str | None:
    return at.astimezone(tz).strftime("%-I:%M %p") if at else None


def duration_label(seconds: Any) -> str | None:
    """`31 s`, `2 min 40 s`, `1 h 4 min`."""
    if isinstance(seconds, bool) or not isinstance(seconds, int | float) or seconds < 0:
        return None
    s = int(round(seconds))
    if s < 60:
        return f"{s} s"
    if s < 3600:
        return f"{s // 60} min {s % 60} s" if s % 60 else f"{s // 60} min"
    return f"{s // 3600} h {(s % 3600) // 60} min"


def cost_label(usd: Any) -> str | None:
    """`7¢`, `<1¢`, `$1.20`; None for zero (a recorded session spent nothing)."""
    if isinstance(usd, bool) or not isinstance(usd, int | float) or usd <= 0:
        return None
    if usd >= 1:
        return f"${usd:.2f}"
    cents = round(usd * 100)
    return f"{cents}¢" if cents >= 1 else "<1¢"


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


# --- source: the review (events by case, or the recorded fixture) ----------------------------


def _case_key(e: Event) -> str:
    """The review an event belongs to: `refs.review` (`review:<kind>:<n>`, R2's service),
    else its data's case id or session id, else the event itself."""
    data = _dict(e.data)
    for value in (_dict(e.refs).get("review"), data.get("case_id"), data.get("session_id")):
        if isinstance(value, str) and value:
            return value
    return e.id


def _review_from_group(case_id: str, opened: Event | None, outcome: Event | None) -> dict:
    reviewing = outcome is None or (opened is not None and opened.ts > outcome.ts)
    event = opened if reviewing else outcome
    assert event is not None
    data, odata = _dict(event.data), _dict(opened.data if opened is not None else None)
    proposal = _dict(data.get("proposal"))
    kind = _str(data.get("kind")) or _str(odata.get("kind"))
    if kind is None and case_id.startswith("review:"):
        kind = case_id.split(":")[1] or None
    session_id = data.get("session_id") or odata.get("session_id")
    return {
        "origin": "event",
        "kind": event.kind,
        "fault_kind": kind,
        "reviewing": reviewing,
        "at": event.ts,
        "opened_at": opened.ts if opened is not None and opened.ts <= event.ts else None,
        "proposal": proposal,
        "result": _dict(data.get("result")),
        "session_id": str(session_id) if session_id else None,
        "duration_s": data.get("duration_s"),
        "cost_usd": data.get("cost_usd"),
        "probe_id": _str(data.get("probe_id"))
        or _str(_dict(proposal.get("proposal")).get("probe")),
        "case_id": case_id,
        "case_file": _dict(data.get("case_file")) or _dict(odata.get("case_file")),
        "recorded": bool(data.get("recorded") or odata.get("recorded")),
        "captured_at": data.get("captured_at"),
        "candidate_id": _str(_dict(event.refs).get("candidate")),
        "studio_url": _str(data.get("studio_url")),
    }


def _reviews_from_events(session: Session, fleet_id: str) -> list[dict[str, Any]]:
    """Every review in the event log, newest first: per case, its latest
    `review.session_opened` and its latest outcome (`staged`, `accepted`, `rejected`)."""
    rows = session.scalars(
        select(Event)
        .where(Event.fleet_id == fleet_id, Event.kind.in_(REVIEW_KINDS))
        .order_by(Event.ts.desc(), Event.recorded_at.desc())
        .limit(200)
    ).all()
    groups: dict[str, dict[str, Event | None]] = {}
    for e in reversed(rows):  # oldest first, so the latest of each kind wins
        g = groups.setdefault(_case_key(e), {"opened": None, "outcome": None})
        g["opened" if e.kind == "review.session_opened" else "outcome"] = e
    reviews = [_review_from_group(k, g["opened"], g["outcome"]) for k, g in groups.items()]
    return sorted(reviews, key=lambda r: r["at"], reverse=True)


def _review_from_fixture(settings: Settings) -> dict[str, Any] | None:
    """Lane A's recorded cluster-review session, shown until the event log has a review."""
    path = settings.review_fixture
    if not path:
        return None
    try:
        raw = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    # review_spike.py writes `structured_output`; the brief's shape said `output`.
    proposal = _dict(raw.get("structured_output") or raw.get("output"))
    rule = _dict(proposal.get("proposal"))
    case_file = _dict(raw.get("case_file"))
    return {
        "origin": "fixture",
        "kind": None,
        "fault_kind": _str(case_file.get("kind")),
        "reviewing": False,
        "at": _parse_time(raw.get("captured_at")),
        "opened_at": None,
        "proposal": proposal,
        "result": {},  # routed by domain.review.check in `_route_fixture`
        "session_id": _str(raw.get("session_id")),
        "duration_s": raw.get("duration_s"),
        "cost_usd": raw.get("cost_usd"),
        "probe_id": _str(raw.get("probe_id")) or _str(rule.get("probe")),
        "case_id": _str(raw.get("case_id")) or _str(case_file.get("case")) or "fixture",
        "case_file": case_file,
        "recorded": True,
        "captured_at": raw.get("captured_at"),
        "candidate_id": None,
        "studio_url": _str(raw.get("studio_url")),
        # The spike's own checks in code (G1), shown beside domain.review's.
        "checks": [c for c in _list(_dict(raw.get("g1")).get("checks")) if isinstance(c, dict)],
    }


def _reviews(session: Session, settings: Settings) -> list[dict[str, Any]]:
    """Newest first; the recorded fixture only while the event log has none."""
    reviews = _reviews_from_events(session, settings.fleet_id)
    if not reviews:
        fixture = _review_from_fixture(settings)
        reviews = [fixture] if fixture else []
    return reviews


def _checks_line(checks: list[dict[str, Any]]) -> str | None:
    """`7 of 7 checks passed in code: ✓ the proposal validates · ✓ …`, from the spike's G1."""
    if not checks:
        return None
    ok = sum(1 for c in checks if c.get("ok"))
    marks = " · ".join(f"{'✓' if c.get('ok') else '✕'} {c.get('check')}" for c in checks)
    return f"{ok} of {len(checks)} checks passed in code: {marks}"


def _route_fixture(
    review: dict[str, Any], probe: dict[str, Any] | None, settings: Settings, now: datetime
) -> dict[str, Any]:
    """Route the recorded session with `domain.review.check` (checks 1-6, design §4.5), judged
    at the time it was captured, so the probe's 24 h check means what it meant then. A passing
    rule waits for its owner while the rule lane is T2, as R2's service records it."""
    at = _parse_time(review.get("captured_at")) or now
    res = rv.check(
        review["proposal"] or None,
        review["case_file"],
        [probe] if probe else [],
        now=at,
        safety_paths=settings.safety_paths,
        existing_skill_slugs=(),
    )
    out = asdict(res)
    out["targets"], out["failures"] = list(res.targets), list(res.failures)
    if res.outcome == "accepted" and res.lane == "rule":
        out["outcome"] = "staged"
    out["checks"] = _checks_line(review.get("checks") or [])
    return out


# --- source: the probe -----------------------------------------------------------------------


def _probe(settings: Settings, probe_id: str | None) -> dict[str, Any] | None:
    """The probe scorecard in PROBE_DIR whose `probe` is `probe_id` (any file name, so lane
    A's committed copy and the host's output both match). No id → no probe: a review that
    cites none never borrows another review's numbers."""
    folder = Path(settings.probe_dir)
    if not probe_id or not folder.is_dir():
        return None
    found: list[dict[str, Any]] = []
    for path in sorted(folder.glob("*.json")):
        try:
            if path.stat().st_size > PROBE_MAX_BYTES:  # e.g. a probe's input windows
                continue
            doc = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if not isinstance(doc, dict) or not isinstance(doc.get("before"), dict):
            continue
        if doc.get("probe") == probe_id:
            found.append(doc)
    if not found:
        return None
    return max(found, key=lambda d: str(d.get("generated_at") or ""))


def _noun(probe: dict[str, Any]) -> str:
    words = f"{probe.get('kind') or ''} {probe.get('probe') or ''}".lower()
    return next((noun for key, noun in NOUNS if key in words), "cases")


def _numbers(probe: dict[str, Any] | None) -> Numbers | None:
    """`0 → 120 of 120 freezes caught · no new false alarms`, computed from before/after."""
    if not probe:
        return None
    before, after = _dict(probe.get("before")), _dict(probe.get("after"))
    b, a = _num(before.get("caught")), _num(after.get("caught"))
    of_b, of_a = _num(before.get("of")), _num(after.get("of"))
    if b is None or a is None or of_a is None:
        return None
    noun = _noun(probe)
    if of_b is not None and of_b != of_a:
        line = f"{b} of {of_b} → {a} of {of_a} {noun} caught"
    else:
        line = f"{b} → {a} of {of_a} {noun} caught"
    fa_b, fa_a = _num(before.get("false_alarms")) or 0, _num(after.get("false_alarms")) or 0
    new = max(fa_a - fa_b, 0)
    healthy = _num(after.get("healthy"))
    if new == 0:
        alarms = "no new false alarms" + (f" on {healthy} healthy windows" if healthy else "")
    else:
        alarms = f"{_plural(new, 'new false alarm')}" + (
            f" on {healthy} healthy" if healthy else ""
        )
    return {
        "line": line,
        "before": b,
        "after": a,
        "of": of_a,
        "noun": noun,
        "alarms_line": alarms,
        "new_false_alarms": new,
        "improved": a > b and fa_a <= fa_b,
    }


def _limit_text(item: Any) -> str | None:
    if isinstance(item, str):
        return item.strip() or None
    d = _dict(item)
    if d.get("text"):
        return str(d["text"])
    name = d.get("name") or d.get("case") or d.get("set")
    caught, of = _num(d.get("caught")), _num(d.get("of"))
    if name and caught is not None and of is not None:
        return f"{name}: {caught} of {of} caught"
    return str(name) if name else None


def _probe_view(probe: dict[str, Any] | None, tz: ZoneInfo, now: datetime) -> dict[str, Any] | None:
    if not probe:
        return None
    at = _parse_time(probe.get("generated_at"))
    age = (now - at).total_seconds() if at else None
    port = _dict(probe.get("port"))
    return {
        "id": probe.get("probe"),
        "kind": probe.get("kind"),
        "description": probe.get("description"),
        "definitions": probe.get("definitions"),
        "command": probe.get("command"),
        "rev": probe.get("rev"),
        "generated_label": f"{at.astimezone(tz).strftime('%a %b %-d')} {_clock(at, tz)}"
        if at
        else None,
        "seconds": probe.get("seconds"),
        "stale": age is not None and age > PROBE_MAX_AGE_S,
        "port_from": port.get("from"),
        "port_to": [str(t) for t in _list(port.get("to"))],
    }


# --- source: labelled runs -------------------------------------------------------------------


def _labelled_counts(session: Session, fleet_id: str, runs: list) -> dict[str, Any]:
    """Runs by verdict (`domain.runs.counts` over `services.runs.load`): only a verifier
    labels, never a model."""
    by = dr.counts(runs)
    regression = session.scalar(
        select(func.count()).select_from(RegressionCase).where(RegressionCase.fleet_id == fleet_id)
    )
    return {
        "runs_total": len(runs),
        "passed": by[dr.PASS],
        "failed": by[dr.FAIL],
        "unverified": by[dr.UNVERIFIED],
        "excluded": by[dr.EXCLUDED],
        "regression_cases": int(regression or 0),
        "source": "labelled runs (the verdict table)",
    }


# --- source: the fleet dataset ---------------------------------------------------------------


def _fleet_dataset(session: Session, fleet_id: str) -> tuple[dict | None, ExportView | None]:
    ds = session.scalars(
        select(Dataset).where(Dataset.fleet_id == fleet_id, Dataset.name == DATASET_NAME)
    ).first()
    if ds is None:
        return None, None
    versions = session.scalar(
        select(func.count()).select_from(DatasetVersion).where(DatasetVersion.dataset_id == ds.id)
    )
    dataset = {
        "id": ds.id,
        "name": ds.name,
        "versions": int(versions or 0),
        "url": f"/v1/datasets/{ds.id}",
    }
    built = session.scalars(
        select(Export)
        .where(Export.dataset_id == ds.id, Export.status == "built")
        .order_by(Export.created_at.desc())
    ).all()
    exp = next((e for e in built if e.format == "trajectories"), built[0] if built else None)
    if exp is None:
        return dataset, None
    made = session.scalars(
        select(Event)
        .where(Event.refs.contains({"export": exp.id}), Event.kind == "export.built")
        .order_by(Event.ts.desc())
    ).first()
    manifest = _dict(_dict(made.data if made else None).get("manifest"))
    counts = _dict(manifest.get("counts"))
    rows = next(
        (_num(counts[k]) for k in ("rows", "trajectories", "training_rows") if k in counts), None
    )
    return dataset, {
        "id": exp.id,
        "format": exp.format,
        "n": exp.n,
        "download": f"/v1/exports/{exp.id}/file",
        "sha256": exp.sha256,
        "sha_short": (exp.sha256 or "")[:8],
        "redact": manifest.get("redact"),
        "rows": rows,
        "file_label": "trajectories.jsonl" if exp.format == "trajectories" else f"{exp.format}.tar",
    }


def _fleet_line(session: Session, fleet_id: str) -> tuple[str, bool]:
    kinds = Counter(session.scalars(select(Device.kind).where(Device.fleet_id == fleet_id)).all())
    real, sim = kinds.get("real", 0), kinds.get("sim", 0)
    parts = [f"{real} real arm{'' if real == 1 else 's'}"] if real else []
    if sim:
        parts.append(f"{sim} simulated")
    if kinds.get("test"):
        parts.append(f"{kinds['test']} test")
    return (" · ".join(parts) or "no devices registered"), sim > 0


def _data_strip(session: Session, fleet_id: str, runs: list) -> DataStrip:
    counts = _labelled_counts(session, fleet_id, runs)
    dataset, export = _fleet_dataset(session, fleet_id)
    fleet_line, sim = _fleet_line(session, fleet_id)
    return {
        **counts,  # type: ignore[typeddict-item]
        "labelled": counts["passed"] + counts["failed"],
        "training_rows": export["rows"] if export else None,
        "export": export,
        "dataset": dataset,
        "sim_fleet": sim,
        "fleet_line": fleet_line,
    }


# --- the candidate (skill lane) --------------------------------------------------------------


def _candidate_events(session: Session, cid: str) -> list[Event]:
    return list(
        session.scalars(
            select(Event).where(Event.refs.contains({"candidate": cid})).order_by(Event.ts)
        )
    )


def _rollout(session: Session, cand: Candidate) -> list[dict[str, Any]]:
    """One chip per device of the class; only an installed device gets ✓ (demo §7.2)."""
    chips = []
    devices = session.scalars(
        select(Device).where(
            Device.fleet_id == cand.fleet_id, Device.device_class == cand.device_class
        )
    ).all()
    for d in sorted(devices, key=lambda d: (d.kind != "sim", d.id)):  # sims first, as rolled
        running = getattr(d, f"running_{cand.type}", None)
        assigned = getattr(d, f"assigned_{cand.type}", None)
        version = cand.version
        installed = version is not None and running is not None and running >= version
        if installed:
            status = "installed"
        elif version is not None and assigned is not None and assigned >= version:
            status = "queued"
        else:
            status = "not yet"
        tag = {"sim": "SIM", "real": "REAL", "test": "TEST"}.get(d.kind, d.kind.upper())
        if d.role == "canary":
            tag += "·canary"
        chips.append({"device": d.id, "installed": installed, "tag": tag, "status": status})
    return chips


# --- assembling the view ---------------------------------------------------------------------


def _state(review: dict[str, Any] | None, result: dict[str, Any], cand: Candidate | None) -> State:
    if review is None:
        return "empty"
    if review["reviewing"]:
        return "reviewing"
    if review["origin"] == "fixture":
        outcome = result.get("outcome")
        return (
            "staged" if outcome == "staged" else "rejected" if outcome == "rejected" else "proposed"
        )
    kind = review["kind"]
    if kind == "review.staged":
        return "staged"
    if kind == "review.rejected":
        return "rejected"
    if cand is None:
        return "accepted"
    if cand.status == "promoted":
        return "promoted"
    if cand.status == "rolled_back":
        return "rolled_back"
    if cand.status in ("replaying", "awaiting_canary", "canary", "awaiting_verify"):
        return "canary"
    return "stopped"


def _stages(state: State, lane: str | None, measured: bool) -> list[Stage]:
    """`measured`: a rule's probe improved, or a held skill passed its checks."""
    if lane == "skill":
        labels = SKILL_STAGES if state in GATE_STATES else HELD_SKILL_STAGES
    else:
        labels = RULE_STAGES
    states: tuple[StageState, ...] = {
        "empty": ("pending", "pending", "pending", "pending"),
        "reviewing": ("done", "active", "pending", "pending"),
        "rejected": ("done", "failed", "pending", "pending"),
        "staged": ("done", "done", "done" if measured else "pending", "active"),
        "proposed": ("done", "done", "done" if measured else "pending", "pending"),
        "accepted": ("done", "done", "active", "pending"),
        "canary": ("done", "done", "active", "pending"),
        "promoted": ("done", "done", "done", "done"),
        "rolled_back": ("done", "done", "failed", "pending"),
        "stopped": ("done", "done", "failed", "pending"),
    }[state]
    out: list[Stage] = [{"label": lbl, "state": st} for lbl, st in zip(labels, states, strict=True)]
    if state == "rejected":
        out[1]["label"] = "checks failed"
    if state == "rolled_back":
        out[2]["label"] = "rolled back on the sim arm"
    return out


def _end_state(state: State, result: dict[str, Any], cand: Candidate | None) -> str:
    owner = result.get("owner") or "its owner"
    return {
        "empty": "no cluster review yet",
        "reviewing": "reviewing…",
        "staged": f"staged for {owner}",
        "proposed": "proposed · no rollout lane yet",
        "rejected": "rejected by the checks",
        "accepted": "accepted · building the candidate",
        "canary": "trying it on a sim arm",
        "promoted": "lesson → every arm",
        "rolled_back": "rolled back · review paused for this kind",
        "stopped": f"candidate {cand.status if cand else 'stopped'}",
    }[state]


def _routing(state: State, lane: str | None, result: dict[str, Any]) -> str | None:
    if state in ("empty", "reviewing"):
        return None
    if state == "rejected":
        return "checks failed → nothing shipped; this fault kind cools down"
    owner = result.get("owner") or "its owner"
    if result.get("touches_safety"):
        return f"safety code → staged for {owner} (D25)"
    if state == "staged":
        return f"a rule → staged for {owner} (the rule lane isn't built yet)"
    if lane == "skill":
        return "not safety code → the gate decides (D6): replay, sim canary, promote or roll back"
    return "a rule → the gate's real replay (rule lane not built yet)"


def _duration(
    session: Session, review: dict[str, Any], state: State, cand: Candidate | None, now: datetime
) -> float | None:
    """Measured from events: session opened → staged / promoted (demo §4). A promoted
    change's figure includes the canary."""
    opened = review.get("opened_at")
    if state == "reviewing":
        return (now - opened).total_seconds() if opened else None
    recorded = review.get("duration_s")
    recorded = float(recorded) if isinstance(recorded, int | float) else None
    end = review.get("at")
    if state == "promoted" and cand is not None:
        promoted = [
            e for e in _candidate_events(session, cand.id) if e.kind == "candidate.promoted"
        ]
        if promoted:
            if opened:
                return (promoted[-1].ts - opened).total_seconds()
            if recorded is not None and end is not None:
                return recorded + (promoted[-1].ts - end).total_seconds()
    if opened and end and review["origin"] == "event":
        return (end - opened).total_seconds()
    return recorded


def _run_url(r: dr.LabelledRun) -> str | None:
    if r.source == "agent_run" and r.run_id:
        return f"/agent-runs/{r.device_id}/{r.run_id}"
    return f"/incidents/{r.incident_id}" if r.incident_id else None


def _evidence(review: dict[str, Any], by_id: dict[str, dr.LabelledRun]) -> list[dict[str, Any]]:
    """The proposal's evidence ids as labelled runs: verdict word, verifier sentence, SIM. An
    id this cloud doesn't hold falls back to the case file's copy of the run."""
    from_case = {
        str(r["id"]): r for r in _list(review["case_file"].get("runs")) if isinstance(r, dict)
    }
    out = []
    for raw in _list(review["proposal"].get("evidence")):
        rid = str(raw)
        run = by_id.get(rid)
        if run is not None:
            out.append(
                {
                    "id": rid,
                    "url": _run_url(run),
                    "verdict": run.verdict,
                    "verifier": run.verifier,
                    "sim": run.sim_device,
                    "device": run.device_id,
                    "here": True,
                }
            )
            continue
        c = from_case.get(rid, {})
        out.append(
            {
                "id": rid,
                "url": None,
                "verdict": _str(c.get("verdict")),
                "verifier": _str(c.get("verifier")),
                "sim": c.get("device_kind") == "sim",
                "device": _str(c.get("device")),
                "here": False,
            }
        )
    return out


def _published(session: Session, cand: Candidate | None) -> dict[str, Any] | None:
    """The newest `review.published` for the shown candidate (#81): the Naïve Skills mirror."""
    if cand is None:
        return None
    found = _candidate_events(session, cand.id)
    pub = next((e for e in reversed(found) if e.kind == "review.published"), None)
    if pub is None:
        return None
    data = _dict(pub.data)
    url = _str(data.get("url"))
    return {
        "slug": _str(data.get("slug")),
        "version": data.get("version"),
        "skill_id": _str(data.get("skill_id")),
        "url": url if url and url.startswith("https://") else None,
        "title": _str(data.get("title")),
    }


NO_CANDIDATE = "none: safety code is staged for its owner, never shipped (D25)"
STUB_REPLAY = "stub: a skill is prose, so there are no rules to replay"
EVIDENCE_NOT_CANARY = "the case file's runs say it helps; the canary only said it does no harm"


def _gate(
    state: State,
    lane: str | None,
    result: dict[str, Any],
    cand: Candidate | None,
    settings: Settings,
    published: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    """Small rows below the fold. The evidence (does it help?) and the canary (does it do
    no harm?) stay separate claims (demo §4)."""
    rows: list[tuple[str, str]] = []
    if _str(result.get("why")):
        rows.append(("routing", str(result["why"])))
    if result.get("touches_safety"):
        rows.append(("candidate", NO_CANDIDATE))
    if result.get("checks"):
        rows.append(("checks", str(result["checks"])))
    rows += [("failed check", str(f)) for f in _list(result.get("failures"))]
    if cand is not None:
        status = cand.status.replace("_", " ")
        rows.append(("candidate", f"{cand.id} · {cand.type} v{cand.version} · {status}"))
        stub = "replay" in _list(cand.stub_stages) or bool(_dict(cand.replay).get("stub"))
        if lane == "skill" or stub:
            rows.append(("replay", STUB_REPLAY))
        elif cand.replay:
            rows.append(("replay", str(_dict(cand.replay).get("why") or "ran")))
        window = f"{settings.canary_window_s} s window"
        canary = cand.canary_device or "the canary"
        rows.append(("canary", f"{canary} · {window} · checks it does no harm, not that it helps"))
    if state == "promoted":
        rows.append(("evidence", EVIDENCE_NOT_CANARY))
    if published:
        rows.append(
            (
                "Naïve Skills",
                f"{published['slug']} v{published['version']} · mirrored "
                "after the gate promoted it; a mirror, not a rollout lane",
            )
        )
    return [{"label": label, "value": value} for label, value in rows]


OUTCOME_WORDS = {
    "reviewing": "reviewing",
    "staged": "staged",
    "proposed": "proposed",
    "rejected": "rejected",
    "accepted": "accepted",
    "canary": "on the canary",
    "promoted": "promoted",
    "rolled_back": "rolled back",
    "stopped": "stopped",
}
RECENT_MAX = 6


def _case_query(case_id: str) -> str:
    return "?case=" + quote(case_id, safe=":")


def _recent(
    session: Session, reviews: list[dict[str, Any]], hero: dict[str, Any] | None, now: datetime
) -> list[dict[str, Any]]:
    """The "Recent reviews" switcher: kind · outcome word · duration · cost, newest first."""
    out = []
    for r in reviews[:RECENT_MAX]:
        cand = session.get(Candidate, r["candidate_id"]) if r["candidate_id"] else None
        state = _state(r, r["result"], cand)
        out.append(
            {
                "case_id": r["case_id"],
                "kind": (r.get("fault_kind") or "review").replace("_", " "),
                "outcome": OUTCOME_WORDS[state],
                "state": state,
                "duration_label": duration_label(_duration(session, r, state, cand, now)),
                "cost_label": cost_label(r.get("cost_usd")),
                "url": "/learning" + _case_query(r["case_id"]),
                "current": hero is not None and r["case_id"] == hero["case_id"],
            }
        )
    return out


def view(
    session: Session, settings: Settings, *, now: datetime, case: str | None = None
) -> LearningView:
    """The /learning page's view-model. Reads only; decides nothing. `case` pins the hero
    to one review (`review:<kind>:<n>`); unknown or none → the newest."""
    tz = ZoneInfo(settings.display_tz)
    reviews = _reviews(session, settings)
    pinned = next((r for r in reviews if case and r["case_id"] == case), None)
    review = pinned or (reviews[0] if reviews else None)
    proposal = review["proposal"] if review else {}
    inner = _dict(proposal.get("proposal"))
    probe = _probe(settings, review["probe_id"]) if review else None
    if review and review["origin"] == "fixture":
        review["result"] = _route_fixture(review, probe, settings, now)
    result = review["result"] if review else {}
    cand = (
        session.get(Candidate, review["candidate_id"])
        if review and review["candidate_id"]
        else None
    )
    state = _state(review, result, cand)
    lane = inner.get("kind") if inner.get("kind") in ("rule", "skill") else None
    if lane is None and cand is not None and cand.type == "harness":
        lane = "skill"
    numbers = _numbers(probe) if lane != "skill" else None
    failures = [str(f) for f in _list(result.get("failures"))]
    measured = (not failures) if lane == "skill" else bool(numbers and numbers["improved"])
    runs = runs_service.load(session, now=now, fleet_id=settings.fleet_id)
    published = _published(session, cand)

    pattern = str(proposal.get("pattern") or "").strip() or None
    title = str(proposal.get("title") or "").strip() or None
    verdict = pattern if pattern and len(pattern) <= 140 else (title or pattern)
    subtitle = title if verdict == pattern else pattern

    dur = _duration(session, review, state, cand, now) if review else None
    clicks = None
    if cand is not None and state in ("promoted", "canary", "rolled_back", "stopped"):
        n = sum(1 for e in _candidate_events(session, cand.id) if e.actor_class == "human")
        clicks = _plural(n, "click") if n else "0 clicks"
    session_id = review.get("session_id") if review else None
    recorded = bool(review and review["recorded"])
    badge = None
    if recorded:
        when = _clock(_parse_time(review.get("captured_at")) or review.get("at"), tz)
        badge = " · ".join(str(x) for x in ("recorded Naïve session", session_id, when) if x)

    limits = [t for t in (_limit_text(x) for x in _list((probe or {}).get("limits"))) if t]
    staged_patch = _str(result.get("staged_patch"))
    if not staged_patch and state == "staged" and probe:
        port = _dict(probe.get("port"))
        to = [str(t) for t in _list(port.get("to"))]
        if port.get("from") and to:
            staged_patch = f"Port {port['from']} into {' and '.join(to)}."

    return {
        "state": state,
        "origin": review["origin"] if review else None,
        "case_id": review["case_id"] if review else None,
        "recent": _recent(session, reviews, review, now),
        "head": {
            "agent": AGENT,
            "mode": MODE,
            "end_state": _end_state(state, result, cand),
            "end_first": state == "promoted",
            "duration_label": ("running " if state == "reviewing" and dur is not None else "")
            + (duration_label(dur) or "")
            or None,
            "cost_label": cost_label(review.get("cost_usd")) if review else None,
            "clicks_label": clicks,
            "recorded": recorded,
            "badge": badge,
            "session_id": session_id,
            "studio_url": (review.get("studio_url") if review else None) or STUDIO_URL,
        },
        "stages": _stages(state, lane, measured),
        "verdict": verdict,
        "subtitle": subtitle,
        "gap_label": GAP_LABELS.get(str(proposal.get("gap"))),
        "lane": lane,
        "touches_safety": bool(result.get("touches_safety")),
        "numbers": numbers,
        "limits": limits,
        "limit_headline": limits[0].split(": ", 1)[0] if limits else None,
        "routing": _routing(state, lane, result),
        "failures": failures,
        "data": _data_strip(session, settings.fleet_id, runs),
        "repeat_rate": None,
        "evidence": _evidence(review, {r.id: r for r in runs}) if review else [],
        "proposal_json": json.dumps(proposal, indent=2, ensure_ascii=False) if proposal else None,
        "probe": _probe_view(probe, tz, now),
        "staged_patch": staged_patch,
        "gate": _gate(state, lane, result, cand, settings, published) if review else [],
        "rollout": _rollout(session, cand) if cand is not None else [],
        "published": published,
        "partial_url": "/partials/learning" + (_case_query(pinned["case_id"]) if pinned else ""),
    }
