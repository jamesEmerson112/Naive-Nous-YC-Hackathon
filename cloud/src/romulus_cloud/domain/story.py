"""An incident's story: its events as a readable, ordered chain (D10, v0 plan §6). Pure.

`build_story` is generic over event kinds. Each step shows who acted, the kind, a
plain-English line and the event's `why`. Later milestones extend a story by recording
more events with the incident in their refs (candidate, gate, work order, evidence), and,
if they want a nicer line or chain label than the default, by adding an entry to
`LINES` / `LABELS` (and `STAGE` for tie order), and, if the kind changes where an
incident stands, to `STATUS`. Nothing here needs rewriting. Work-order events also carry
`refs.status`, which picks the badge from `WORK_ORDER_STATUS` (M5).

Evidence (M4): `recording.*` events carry an `evidence` dict in their data (kind, format,
size, download link, whether the bytes are stored). Their steps carry it too, and
`Story.evidence` lists each recording once, with its latest state.

Fleet Doctor (PR 6): `doctor.*` steps carry their event's data as `doctor`, and
`Story.ledger` lists the hypotheses from `doctor.advised` (an L1 ask) and
`doctor.diagnosed` (an L2 consult), newest first. A closed consult picks its badge from
`data.outcome` (`CONSULT_OUTCOME_STATUS`; `held` sets none), and no Doctor status covers
one that needs a person (`NEEDS_PERSON`). All doctor kinds share one `STAGE`, so on a
tied `ts` they keep the order they were written in; so does a Doctor fix's `overlay.applied`
(`stage`), which therefore reads after its `doctor.proposal_result`.

Order is by when things happened (`ts`, the device's `occurred_at` for device events),
never by when the server received them (K9). Events with the same `ts` are ordered by
their stage in the loop (`STAGE`), then by the order they were given in.
"""

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol


class EventLike(Protocol):
    """What a story needs from an event (a `models.Event` row fits)."""

    ts: datetime
    actor: str
    actor_class: str
    kind: str
    refs: Mapping[str, Any]
    why: str
    data: Mapping[str, Any] | None


@dataclass(frozen=True)
class Step:
    at: datetime
    actor: str
    actor_class: str
    kind: str
    line: str
    why: str
    label: str
    refs: dict[str, Any] = field(default_factory=dict)
    evidence: dict[str, Any] | None = None  # recording steps only (M4)
    doctor: dict[str, Any] | None = None  # doctor.* steps only: the event's data (PR 6)


@dataclass(frozen=True)
class Story:
    incident_id: str
    steps: tuple[Step, ...]

    @property
    def links(self) -> tuple[tuple[str, str], ...]:
        """The chain as (label, raw kind) pairs; pages show the label and keep the kind
        in a tooltip.

        Steps with an empty label (follow-ups and acknowledgments, not news) are left out,
        and a label that repeats the one before it is shown once (two candidates promoted
        together read "→ fleet", not "→ fleet → fleet")."""
        links: list[tuple[str, str]] = []
        for s in self.steps:
            if s.label and (not links or links[-1][0] != s.label):
                links.append((s.label, s.kind))
        return tuple(links)

    @property
    def chain(self) -> str:
        """One line for the timeline: `camera_occluded → agent acted → overlay ov-12 → held`."""
        return " → ".join(label for label, _ in self.links)

    @property
    def evidence(self) -> tuple[dict[str, Any], ...]:
        """Each recording once, in order of first appearance, with its latest state."""
        latest: dict[str, dict[str, Any]] = {}
        for s in self.steps:
            if s.evidence and s.evidence.get("recording"):
                rid = str(s.evidence["recording"])
                latest[rid] = {**latest.get(rid, {}), **s.evidence}
        return tuple(latest.values())

    @property
    def ledger(self) -> list[dict[str, Any]]:
        """The Fleet Doctor's hypotheses, newest first: from `doctor.advised` (source `ask`)
        and `doctor.diagnosed` (source `consult`). Each is `{at, source, claim,
        how_to_check, ask | consult, model, stub}`; one event's hypotheses keep their rank.
        `data.hypotheses` that isn't a list adds nothing, and an item without a claim is
        skipped."""
        out: list[dict[str, Any]] = []
        for s in reversed(self.steps):
            source = LEDGER_SOURCES.get(s.kind)
            if source is None or s.doctor is None:
                continue
            for claim, how in _hypotheses(s.doctor):
                out.append(
                    {
                        "at": s.at,
                        "source": source,
                        "claim": claim,
                        "how_to_check": how,
                        source: s.refs.get(source),
                        "model": _model(s.doctor),
                        "stub": bool(s.doctor.get("stub")),
                    }
                )
        return out

    @property
    def status(self) -> tuple[str, str]:
        """Where the incident stands, from the latest step whose kind is in `STATUS`:
        (words, tone), e.g. ("overlay held", "good"). Steps that don't move it (evidence,
        notes) are skipped.

        A Fleet Doctor status never covers one that needs a person (`NEEDS_PERSON`), and a
        consult that closed `held` doesn't move it (`overlay.held` already did, and lock-in
        may have moved on since)."""
        for i in range(len(self.steps) - 1, -1, -1):
            s = self.steps[i]
            if s.kind.startswith("doctor."):
                found = _doctor_status(s)
                if found is None:
                    continue
                before = _settled(self.steps[:i])
                return before if before in NEEDS_PERSON else found
            if s.kind.startswith("workorder.") and s.refs.get("status") in WORK_ORDER_STATUS:
                return WORK_ORDER_STATUS[s.refs["status"]]
            if s.kind in STATUS and s.label:
                return STATUS[s.kind]
        return ("no events", "info") if not self.steps else ("open", "bad")

    def to_dict(self) -> dict[str, Any]:
        steps = []
        for s in self.steps:
            step = {
                "at": s.at.isoformat(),
                "actor": s.actor,
                "actor_class": s.actor_class,
                "kind": s.kind,
                "line": s.line,
                "why": s.why,
                "label": s.label,
                "refs": s.refs,
            }
            if s.evidence is not None:
                step["evidence"] = s.evidence
            steps.append(step)
        return {
            "incident_id": self.incident_id,
            "chain": self.chain,
            "links": [{"label": label, "kind": kind} for label, kind in self.links],
            "steps": steps,
            "evidence": list(self.evidence),
            "ledger": [{**h, "at": h["at"].isoformat()} for h in self.ledger],
        }


def _data(e: EventLike) -> Mapping[str, Any]:
    return e.data or {}


def _device(e: EventLike) -> str:
    return str(e.refs.get("device") or e.actor)


def _overlay_line(verb: str) -> Callable[[EventLike], str]:
    return lambda e: f"Overlay {e.refs.get('overlay')} {verb} on {_device(e)}."


def _actions(e: EventLike) -> str:
    acts = [a if isinstance(a, str) else str(a) for a in _data(e).get("actions") or []]
    return ", ".join(acts) if acts else "no recorded actions"


def _agent_line(e: EventLike) -> str:
    d = _data(e)
    outcome = "resolved it" if d.get("resolved") else "did not resolve it"
    by = f" with {d['resolving_action']}" if d.get("resolving_action") else ""
    cause = d.get("suspected_cause") or "unknown"
    return (
        f"The agent on {_device(e)} did: {_actions(e)}. It {outcome}{by}; suspected cause: {cause}."
    )


def _applied_line(e: EventLike) -> str:
    d = _data(e)
    parts = []
    if d.get("rules"):
        parts.append("rules " + ", ".join(d["rules"]))
    if d.get("params"):
        parts.append("params " + ", ".join(d["params"]))
    if d.get("code_paths"):
        parts.append("code " + ", ".join(d["code_paths"]))
    what = "; ".join(parts) if parts else "no changes listed"
    safety = " It touches the safety layer or visor." if e.refs.get("touches_safety") else ""
    overlay = e.refs.get("overlay")
    return f"The agent patched {_device(e)} locally as overlay {overlay}: {what}.{safety}"


def _evidence(e: EventLike) -> dict[str, Any] | None:
    ev = _data(e).get("evidence")
    return dict(ev) if isinstance(ev, Mapping) else None


def _size(n: Any) -> str:
    if not isinstance(n, int) or isinstance(n, bool):
        return "size unknown"
    for unit, scale in (("GB", 1 << 30), ("MB", 1 << 20), ("KB", 1 << 10)):
        if n >= scale:
            return f"{n / scale:.1f} {unit}"
    return f"{n} B"


def _evidence_line(e: EventLike) -> str:
    ev = _evidence(e) or {}
    what = f"{ev.get('kind', 'recording')} ({ev.get('format', '?')}, {_size(ev.get('size_bytes'))})"
    link = f" Download: {ev.get('download')}" if ev.get("stored") else ""
    return f"Evidence from {_device(e)}: {what}.{link}"


def _stored_line(e: EventLike) -> str:
    ev = _evidence(e) or {}
    return (
        f"The {ev.get('kind', 'recording')} bytes arrived and matched their sha256. "
        f"Download: {ev.get('download')}"
    )


def _linked_line(e: EventLike) -> str:
    ev = _evidence(e) or {}
    added = _data(e).get("labels_added") or []
    labels = f"; labels added: {', '.join(map(str, added))}" if added else ""
    return f"The {ev.get('kind', 'recording')} recording was linked to this incident{labels}."


def _wo(e: EventLike) -> str:
    return f"work order {e.refs.get('work_order')}"


def _wo_created_line(e: EventLike) -> str:
    d = _data(e)
    what = f"{d.get('quantity')} x {d['part_name']}" if d.get("part_name") else "no catalogued part"
    status = e.refs.get("status") or "pending"
    return f"The platform opened {_wo(e)} for {_device(e)} ({what}); status {status}."


def _wo_changed_line(e: EventLike) -> str:
    note = _data(e).get("note")
    said = f" Card note: {note}" if note else ""
    return f"Work order {e.refs.get('work_order')} went from {e.refs.get('from')} to " + (
        f"{e.refs.get('to')}.{said}"
    )


def _words(s: Any) -> str:
    """`log_window` → `log window`: a raw identifier as words for the chain."""
    return str(s).replace("_", " ")


def _wo_changed_label(e: EventLike) -> str:
    to = str(e.refs.get("to") or "updated")
    note = _data(e).get("note")
    return (
        f"{_words(to)}: {note}" if note and to in ("done", "stopped", "needs_human") else _words(to)
    )


def _evidence_label(e: EventLike) -> str:
    return f"evidence: {_words((_evidence(e) or {}).get('kind', 'recording'))}"


# Plain-English line per kind. Unknown kinds fall back to `_default_line`.
LINES: dict[str, Callable[[EventLike], str]] = {
    "incident.reported": lambda e: (
        f"{_device(e)} reported {_data(e).get('kind', 'an incident')}: "
        f"{_data(e).get('summary') or 'no summary'}"
    ),
    "incident.agent_acted": _agent_line,
    "overlay.applied": _applied_line,
    "overlay.held": _overlay_line("held"),
    "overlay.reverted": _overlay_line("was reverted"),
    "overlay.locked_in": _overlay_line("was locked into a fleet version"),
    "overlay.dropped": _overlay_line("was dropped"),
    "overlay.superseded": _overlay_line("was superseded by a new version"),
    "recording.received": _evidence_line,
    "recording.blob_stored": _stored_line,
    "recording.linked": _linked_line,
    "workorder.created": _wo_created_line,
    "workorder.card_posted": lambda e: (
        f"Work order {e.refs.get('work_order')} was posted to Naïve as card "
        f"{_data(e).get('card_id')}, assigned to {_data(e).get('assignee') or 'nobody'}."
    ),
    "workorder.post_retried": lambda e: f"Posting {_wo(e)} to Naïve failed; it stays queued.",
    "workorder.post_held": lambda e: f"Posting {_wo(e)} is held: the fleet is frozen.",
    "workorder.status_changed": _wo_changed_line,
    "workorder.card_updated": lambda e: f"The card for {_wo(e)} changed on Naïve.",
}

# Short label per kind for the one-line chain. Every kind that can carry `refs.incident`
# needs one (a test checks the APIs doc §5 list and the kinds the services record); an
# unknown kind falls back to its words (`_default_label`), never the dotted raw kind.
LABELS: dict[str, Callable[[EventLike], str]] = {
    "incident.reported": lambda e: str(_data(e).get("kind", "incident")),
    "incident.agent_acted": lambda e: "agent acted",
    "overlay.applied": lambda e: f"overlay {e.refs.get('overlay')}",
    "overlay.held": lambda e: "held",
    "overlay.reverted": lambda e: "reverted",
    "overlay.locked_in": lambda e: "locked in",
    "overlay.dropped": lambda e: "dropped",
    "overlay.superseded": lambda e: "" if _acknowledged(e) else "superseded",
    "recording.received": _evidence_label,
    "recording.blob_stored": lambda e: "",  # a follow-up; kept out of the chain
    # A recording the incident listed but that didn't name it: its `received` step isn't in
    # this story, so the link is where it first shows.
    "recording.linked": lambda e: _evidence_label(e) if _data(e).get("linked") else "",
    "workorder.created": lambda e: (
        _wo(e)
        if e.refs.get("status") in (None, "pending")
        else f"{_wo(e)} ({_words(e.refs['status'])})"
    ),
    "workorder.card_posted": lambda e: "card posted",
    "workorder.post_retried": lambda e: "post retried",
    "workorder.post_held": lambda e: "held: fleet frozen",
    "workorder.status_changed": _wo_changed_label,
    "workorder.card_updated": lambda e: "card updated",
}

# --- M3: batch, gate and kill switch -----------------------------------------------------


def _acknowledged(e: EventLike) -> bool:
    """`superseded` after the cloud locked the overlay in (or dropped it) is the device
    acknowledging the fleet version, not news: no chain label, no status change."""
    return _data(e).get("before") in ("locked_in", "dropped")


def _cand(e: EventLike) -> str:
    d = _data(e)
    if d.get("type") == "code":
        return f"code {d.get('code_rev')} (firmware v{d.get('version')})"
    return f"{d.get('type', 'candidate')} v{d.get('version')}"


def _replay_label(e: EventLike) -> str:
    """A stub replay checked nothing, so it stays out of the chain (its step still says so)."""
    replay = _data(e).get("replay") or {}
    if replay.get("stub"):
        return ""
    return "replay ✓" if replay.get("passed") else "replay ✗"


LINES.update(
    {
        "candidate.created": lambda e: f"The batch locked the fix in as candidate {_cand(e)}.",
        "candidate.replayed": lambda e: f"The gate replayed {_cand(e)}.",
        "candidate.canary_started": lambda e: (
            f"The gate put {_cand(e)} on canary {e.refs.get('device')}."
        ),
        "candidate.awaiting_canary": lambda e: f"{_cand(e).capitalize()} waits for a canary.",
        "candidate.awaiting_verify": lambda e: (
            f"{_cand(e).capitalize()} waits for held-out verify scripts."
        ),
        "candidate.unverified": lambda e: (
            f"Warning: {_cand(e)} passed on in-process tests only (no held-out verify)."
        ),
        "candidate.frozen": lambda e: f"{_cand(e).capitalize()} froze with the fleet.",
        "candidate.restarted": lambda e: f"{_cand(e).capitalize()} restarts from replay.",
        "candidate.promoted": lambda e: f"{_cand(e).capitalize()} went to the fleet.",
        "candidate.rolled_back": lambda e: f"{_cand(e).capitalize()} was rolled back.",
        "candidate.stale": lambda e: (
            f"{_cand(e).capitalize()} went stale; its overlays wait for the next batch."
        ),
        "candidate.error": lambda e: f"The gate stopped {_cand(e)} after an error.",
        "overlay.lockin_rolled_back": lambda e: (
            f"Overlay {e.refs.get('overlay')} stays on {_device(e)} only: its fleet candidate "
            "was rolled back, and it is never re-batched."
        ),
        "overlay.lockin_error": _overlay_line("could not be locked in (error)"),
        "overlay.requeued": _overlay_line("is held again for the next batch"),
        "overlay.deferred": _overlay_line("waits for the next batch"),
        "batch.error": _overlay_line("could not be locked in (batch error)"),
    }
)
LABELS.update(
    {
        "candidate.created": _cand,
        "candidate.replayed": _replay_label,
        "candidate.canary_started": lambda e: f"canary {e.refs.get('device')}",
        "candidate.awaiting_canary": lambda e: "awaiting canary",
        "candidate.awaiting_verify": lambda e: "awaiting verify",
        "candidate.unverified": lambda e: "unverified",
        "candidate.frozen": lambda e: "frozen",
        "candidate.restarted": lambda e: "unfrozen",
        "candidate.promoted": lambda e: "fleet",
        "candidate.rolled_back": lambda e: "rolled back",
        "candidate.stale": lambda e: "re-queued",
        "candidate.error": lambda e: "gate error",
        # Follow-ups of the candidate step just before them: steps, not chain links.
        "overlay.lockin_rolled_back": lambda e: "",
        "overlay.lockin_error": lambda e: "",
        "overlay.requeued": lambda e: "",
        "overlay.deferred": lambda e: "deferred",
        "batch.error": lambda e: "batch error",
    }
)

# --- M6 data, D21 coding agent, and kinds that never reach a chain ----------------------


def _labels_changed(e: EventLike) -> str:
    d = _data(e)
    parts = [f"+{x}" for x in d.get("added") or []] + [f"-{x}" for x in d.get("removed") or []]
    return "labels " + " ".join(map(str, parts)) if parts else "labels changed"


def _code_rev(e: EventLike) -> str:
    return str(e.refs.get("code_rev") or _data(e).get("code_rev") or "?")


LINES.update(
    {
        "regression.case_added": lambda e: (
            f"The incident joined the regression corpus: every future candidate must fire "
            f"{_data(e).get('expected_action', 'the same action')} on its signals."
        ),
        "label.changed": lambda e: f"Labels on recording {e.refs.get('recording')} changed.",
        "coder.started": lambda e: "The coding agent started on a code fix.",
        "coder.drafted": lambda e: "The coding agent drafted a code patch.",
        "coder.tests_passed": lambda e: "The coding agent's patch passed the device tests.",
        "coder.tests_failed": lambda e: "The coding agent's patch failed the device tests.",
        "code.version_created": lambda e: f"Code version {_code_rev(e)} was created.",
    }
)
LABELS.update(
    {
        "regression.case_added": lambda e: "added to regression corpus",
        "label.changed": _labels_changed,
        "coder.started": lambda e: "coder started",
        "coder.drafted": lambda e: "coder drafted patch",
        "coder.tests_passed": lambda e: "coder tests ✓",
        "coder.tests_failed": lambda e: "coder tests ✗",
        "code.version_created": lambda e: f"code {_code_rev(e)}",
    }
)

# --- Fleet Doctor (docs/fleet-doctor/interfaces.md §8). LINES here; STATUS, STAGE below.


def _stub(e: EventLike, label: str) -> str:
    return f"{label} (stub)" if _data(e).get("stub") else label


def _checked_label(e: EventLike) -> str:
    d = _data(e)
    if d.get("stub"):
        return "check: stub"
    return "check ✓" if d.get("passed") else "check ✗"


LABELS.update(
    {
        "doctor.asked": lambda e: "asked the Fleet Doctor",
        "doctor.advised": lambda e: "Fleet Doctor advised",
        "doctor.blocked_over_cap": lambda e: "Fleet Doctor: over cap",
        "doctor.declined": lambda e: "Fleet Doctor declined",
        "doctor.consult_opened": lambda e: "Fleet Doctor consult",
        "doctor.diagnosed": lambda e: _stub(e, "diagnosed"),
        "doctor.drafted": lambda e: _stub(e, "fix drafted"),
        "doctor.checked": _checked_label,
        "doctor.proposal_discarded": lambda e: "fix discarded",
        "doctor.proposal_offered": lambda e: "fix offered",
        "doctor.proposal_result": lambda e: f"fix {_words(_data(e).get('result') or 'reported')}",
        "doctor.proposal_expired": lambda e: "fix expired",
        "doctor.consult_closed": lambda e: (
            f"consult closed: {_words(_data(e)['outcome'])}"
            if _data(e).get("outcome")
            else "consult closed"
        ),
        "doctor.gave_up": lambda e: "Fleet Doctor gave up",
    }
)


def _doctor(e: EventLike) -> dict[str, Any] | None:
    """A doctor step's data, for the ledger and the consult badge."""
    if not e.kind.startswith("doctor."):
        return None
    return dict(e.data) if isinstance(e.data, Mapping) else {}


# The longest claim or how_to_check the ledger shows; the event keeps the full text.
LEDGER_TEXT_MAX = 500


def _clip(s: str, limit: int = LEDGER_TEXT_MAX) -> str:
    return s if len(s) <= limit else s[: limit - 1].rstrip() + "…"


def _hypotheses(d: Mapping[str, Any]) -> list[tuple[str, str]]:
    """(claim, how_to_check) pairs from `data.hypotheses`, each cut to `LEDGER_TEXT_MAX`.
    A string item is a bare claim; an item without a non-blank string claim is skipped;
    anything but a list gives []."""
    raw = d.get("hypotheses")
    if not isinstance(raw, list | tuple):
        return []
    out: list[tuple[str, str]] = []
    for h in raw:
        claim, how = (h, "") if isinstance(h, str) else (None, "")
        if isinstance(h, Mapping):
            claim, how = h.get("claim"), h.get("how_to_check")
        if isinstance(claim, str) and claim.strip():
            how = how.strip() if isinstance(how, str) else ""
            out.append((_clip(claim.strip()), _clip(how)))
    return out


def _model(d: Mapping[str, Any]) -> str:
    m = d.get("model")
    return m.strip() if isinstance(m, str) else ""


def _count(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def _is_count(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v > 0


def _by_model(d: Mapping[str, Any]) -> str:
    """` (model fake-templated)`, ` (stub)`, both, or nothing: stubs announce themselves."""
    parts = [f"model {_model(d)}"] if _model(d) else []
    if d.get("stub"):
        parts.append("stub")
    return f" ({', '.join(parts)})" if parts else ""


def _text(d: Mapping[str, Any], key: str) -> str:
    v = d.get(key)
    return v.strip() if isinstance(v, str) else ""


def _asked_line(e: EventLike) -> str:
    d = _data(e)
    trigger = f" (trigger {_text(d, 'trigger')})" if _text(d, "trigger") else ""
    question = _text(d, "question")
    said = f": {question}" if question else "."
    return f"The healer on {_device(e)} asked the Fleet Doctor for advice{trigger}{said}"


def _advised_line(e: EventLike) -> str:
    d = _data(e)
    n = len(_hypotheses(d))
    line = f"The Fleet Doctor sent {_count(n, 'hypothesis', 'hypotheses')} to {_device(e)}"
    line += _by_model(d) + "."
    removed = d.get("removed_for_safety")
    if _is_count(removed):
        were = "was" if removed == 1 else "were"
        line += (
            f" {_count(removed, 'hypothesis', 'hypotheses')} named a safety or visor path and "
            f"{were} removed."
        )
    if d.get("parsed_ok") is False:
        line += " Its reply wasn't the JSON asked for, so the advice is the raw prose."
    return line


def _over_cap_line(e: EventLike) -> str:
    d = _data(e)
    which = _text(d, "which") or "daily"
    spent, cap = d.get("spent_today_usd"), d.get("cap_usd")
    amounts = ""
    if all(isinstance(v, int | float) and not isinstance(v, bool) for v in (spent, cap)):
        amounts = f" (${spent:.2f} of ${cap:.2f} spent today)"
    return f"The Fleet Doctor did not run: it is over its {which} cap{amounts}."


def _declined_line(e: EventLike) -> str:
    d = _data(e)
    stage = f" at {_words(_text(d, 'stage'))}" if _text(d, "stage") else ""
    reason = f": {_words(_text(d, 'reason'))}" if _text(d, "reason") else ""
    return f"The Fleet Doctor declined{stage}{reason}."


def _consult_opened_line(e: EventLike) -> str:
    d = _data(e)
    attempt = d.get("attempt")
    nth = f" (attempt {attempt})" if _is_count(attempt) else ""
    by = {"device": " because the device asked for one", "cloud": " on the cloud's own"}
    trigger = f", trigger {_text(d, 'trigger')}" if _text(d, "trigger") else ""
    return (
        f"The Fleet Doctor opened a consult on this incident{nth}"
        f"{by.get(_text(d, 'origin'), '')}{trigger}."
    )


def _diagnosed_line(e: EventLike) -> str:
    d = _data(e)
    n = len(_hypotheses(d))
    line = f"The Fleet Doctor diagnosed the incident: {_count(n, 'hypothesis', 'hypotheses')}"
    line += _by_model(d) + "."
    if d.get("parsed_ok") is False:
        line += " Its reply wasn't the JSON asked for, so the ledger is the raw prose."
    return line


def _drafted_line(e: EventLike) -> str:
    d = _data(e)
    raw = d.get("paths")
    paths = [p for p in raw if isinstance(p, str)] if isinstance(raw, list) else []
    where = f" touching {', '.join(paths)}" if paths else ""
    repro = " with a reproduction test" if d.get("has_repro_test") else ""
    stub = " It is a labeled stub patch, not a real fix." if d.get("stub") else ""
    return f"The Fleet Doctor drafted a fix{where}{repro}.{stub}"


def _checked_line(e: EventLike) -> str:
    d = _data(e)
    if d.get("stub"):
        line = "The Fleet Doctor's checks are a stub: nothing ran."
    else:
        line = f"The fix {'passed' if d.get('passed') else 'failed'} the Fleet Doctor's checks."
    if _text(d, "summary"):
        line += f" {_text(d, 'summary')}"
    if d.get("touches_safety"):
        line += " It touches the safety layer or visor."
    return line


DISCARD_REASONS = {
    "touches_safety": "it touches the safety layer or visor",
    "safety_paths_unknown": "the safety paths aren't confirmed yet",
}


def _discarded_line(e: EventLike) -> str:
    reason = _text(_data(e), "reason")
    why = DISCARD_REASONS.get(reason, _words(reason) or "no reason given")
    return f"The Fleet Doctor discarded its fix: {why}."


def _proposal(e: EventLike) -> str:
    return f"fix {e.refs.get('proposal') or '?'}"


def _offered_line(e: EventLike) -> str:
    stub = " Its checks were a stub." if _data(e).get("stub_checked") else ""
    return f"The Fleet Doctor offered {_proposal(e)} to {_device(e)}.{stub}"


def _result_line(e: EventLike) -> str:
    d = _data(e)
    result = _words(_text(d, "result") or "an unknown result")
    overlay = f" as overlay {e.refs['overlay']}" if e.refs.get("overlay") else ""
    reason = f": {_text(d, 'reason')}" if _text(d, "reason") else ""
    return f"{_device(e)} reported {_proposal(e)} {result}{overlay}{reason}."


def _closed_line(e: EventLike) -> str:
    outcome = _words(_text(_data(e), "outcome") or "no outcome")
    consult = f" {e.refs['consult']}" if e.refs.get("consult") else ""
    return f"The Fleet Doctor closed consult{consult}: {outcome}."


def _gave_up_line(e: EventLike) -> str:
    d = _data(e)
    used = d.get("consults_used")
    after = f" after {_count(used, 'consult', 'consults')}" if _is_count(used) else ""
    reason = f": {_text(d, 'reason')}" if _text(d, "reason") else ""
    return f"The Fleet Doctor gave up on this incident{after}{reason}."


LINES.update(
    {
        "doctor.asked": _asked_line,
        "doctor.advised": _advised_line,
        "doctor.blocked_over_cap": _over_cap_line,
        "doctor.declined": _declined_line,
        "doctor.consult_opened": _consult_opened_line,
        "doctor.diagnosed": _diagnosed_line,
        "doctor.drafted": _drafted_line,
        "doctor.checked": _checked_line,
        "doctor.proposal_discarded": _discarded_line,
        "doctor.proposal_offered": _offered_line,
        "doctor.proposal_result": _result_line,
        "doctor.proposal_expired": lambda e: (
            f"{_proposal(e).capitalize()} expired before {_device(e)} applied it."
        ),
        "doctor.consult_closed": _closed_line,
        "doctor.gave_up": _gave_up_line,
    }
)

# The kinds whose hypotheses make the ledger, and the source each one is.
LEDGER_SOURCES: dict[str, str] = {"doctor.advised": "ask", "doctor.diagnosed": "consult"}

# Kinds about a device, a version, the fleet or a dataset, never one incident: their
# events don't carry `refs.incident`, so they need no chain label. A kind that starts
# carrying an incident moves into `LABELS` (the label test checks both lists).
NOT_IN_CHAINS: frozenset[str] = frozenset(
    {
        "device.first_seen",
        "device.registered",
        "device.assigned",
        "device.updated",
        "device.running_changed",
        "device.health_changed",
        "device.overlay_changed",
        "install.reported",
        "firmware.version_created",
        "harness.version_created",
        "fleet.frozen",
        "fleet.unfrozen",
        "fleet.overnight_started",
        "fleet.overnight_stopped",
        "recording.blobs_pruned",
        "batch.requested",
        "batch.ran",
        "dataset.created",
        "dataset.snapshotted",
        "export.requested",
        "export.built",
        "export.failed",
    }
)

# Where an incident stands after a step of this kind: (words for a badge, tone), with tone
# one of good / bad / wait / info. Kinds not listed don't change the status.
STATUS: dict[str, tuple[str, str]] = {
    "incident.reported": ("open", "bad"),
    "incident.agent_acted": ("agent acted", "wait"),
    "overlay.applied": ("overlay on trial", "wait"),
    "overlay.held": ("overlay held", "good"),
    "overlay.reverted": ("overlay reverted", "bad"),
    "overlay.locked_in": ("locked in", "good"),
    "overlay.dropped": ("overlay dropped", "info"),
    "overlay.superseded": ("superseded", "info"),
    "candidate.promoted": ("in the fleet", "good"),
    "candidate.rolled_back": ("rolled back", "bad"),
    "candidate.frozen": ("frozen", "bad"),
    "candidate.awaiting_canary": ("awaiting canary", "wait"),
    "candidate.awaiting_verify": ("awaiting verify", "wait"),
    "candidate.unverified": ("unverified pass", "bad"),
    "workorder.created": ("work order", "wait"),
    "workorder.card_posted": ("work order posted", "wait"),
    "workorder.status_changed": ("work order updated", "wait"),
    "candidate.created": ("candidate", "wait"),
    "candidate.canary_started": ("on canary", "wait"),
    "candidate.restarted": ("restarting from replay", "wait"),
    "candidate.stale": ("re-queued for the next batch", "wait"),
    "candidate.error": ("gate error", "bad"),
    "overlay.deferred": ("held for the next batch", "wait"),
    "batch.error": ("lock-in failed", "bad"),
}
# A work-order event's `refs.status` says more than its kind, so it picks the badge (M5).
WORK_ORDER_STATUS: dict[str, tuple[str, str]] = {
    "pending": ("work order queued", "wait"),
    "blocked_over_cap": ("blocked: over cap", "bad"),
    "needs_human": ("work order needs a person", "bad"),
    "posted": ("work order posted", "wait"),
    "in_progress": ("fleet-ops on it", "wait"),
    "done": ("part ordered", "good"),
    "stopped": ("order stopped", "bad"),
    "denied": ("work order denied", "info"),
}
STATUS_TONES = ("good", "bad", "wait", "info")

# Where a kind sits in the loop, to order events that share a timestamp (an incident,
# the agent's actions and its overlay all carry the incident's `occurred_at`).
STAGE: dict[str, int] = {
    "incident.reported": 0,
    "incident.agent_acted": 1,
    "overlay.applied": 2,
    "recording.received": 10,
    "recording.blob_stored": 11,
    "recording.linked": 12,
    # One worker tick can open, post and update a work order at the same `now`.
    "workorder.created": 60,
    "workorder.post_held": 61,
    "workorder.post_retried": 61,
    "workorder.card_posted": 61,
    "workorder.status_changed": 62,
    "workorder.card_updated": 62,
    # One batch run or gate tick writes these with one `ts`; keep them in loop order.
    "overlay.locked_in": 70,
    "candidate.created": 71,
    "candidate.replayed": 72,
    "candidate.awaiting_canary": 73,
    "candidate.awaiting_verify": 72,
    "candidate.unverified": 72,
    "candidate.canary_started": 73,
    "candidate.frozen": 74,
    "candidate.restarted": 74,
    "candidate.rolled_back": 75,
    "candidate.promoted": 75,
    "candidate.stale": 75,
    "candidate.error": 75,
    "overlay.lockin_rolled_back": 76,
    "overlay.lockin_error": 76,
    "overlay.requeued": 76,
}
_DEFAULT_STAGE = 50

# --- Fleet Doctor: where an incident stands, and tie order (PR 6) ------------------------
# A consult moves the badge while it is open; a closed one picks it from `data.outcome`
# (interfaces.md §5.2). L1 asks (asked, advised) and the consult's inner steps don't move it.
STATUS.update(
    {
        "doctor.consult_opened": ("Fleet Doctor consulting", "wait"),
        "doctor.proposal_offered": ("Fleet Doctor fix offered", "wait"),
        "doctor.consult_closed": ("Fleet Doctor consult closed", "info"),  # no outcome given
        "doctor.gave_up": ("Fleet Doctor gave up", "bad"),
    }
)
# Every outcome but `held` means nothing of the Doctor's was applied and kept, so the
# incident isn't fixed: bad. `held` sets no status (`overlay.held` already says so, and a
# close that lands after lock-in must not pull the badge back from the gate).
CONSULT_OUTCOME_STATUS: dict[str, tuple[str, str] | None] = {
    "held": None,
    "advised": ("Fleet Doctor advised only", "bad"),
    "declined": ("Fleet Doctor declined", "bad"),
    "blocked_over_cap": ("Fleet Doctor: over cap", "bad"),
    "discarded": ("Fleet Doctor fix discarded", "bad"),
    "failed_checks": ("Fleet Doctor fix failed checks", "bad"),
    "reverted": ("Fleet Doctor fix reverted", "bad"),
    "failed_verify": ("Fleet Doctor fix failed verify", "bad"),
    "stale": ("Fleet Doctor fix stale", "bad"),
    "rejected": ("Fleet Doctor fix rejected", "bad"),
    "expired": ("Fleet Doctor fix expired", "bad"),
    "gave_up": ("Fleet Doctor gave up", "bad"),
}

# Statuses a person has to act on. Nothing the Fleet Doctor does covers them up; a later
# non-Doctor step (the work order moving on, an unfreeze) still can.
NEEDS_PERSON: frozenset[tuple[str, str]] = frozenset(
    {
        WORK_ORDER_STATUS["blocked_over_cap"],
        WORK_ORDER_STATUS["needs_human"],
        WORK_ORDER_STATUS["stopped"],
        STATUS["candidate.frozen"],
    }
)


def _consult_status(d: Mapping[str, Any]) -> tuple[str, str] | None:
    outcome = _text(d, "outcome")
    if not outcome:
        return STATUS["doctor.consult_closed"]
    return CONSULT_OUTCOME_STATUS.get(outcome, (f"consult closed: {_words(outcome)}", "info"))


def _doctor_status(s: Step) -> tuple[str, str] | None:
    """The status a doctor step sets, or None if it doesn't move it."""
    if s.kind == "doctor.consult_closed":
        return _consult_status(s.doctor or {})
    return STATUS.get(s.kind) if s.label else None


def _settled(steps: Iterable[Step]) -> tuple[str, str] | None:
    """The status from the latest non-Doctor step that sets one, or None."""
    for s in reversed(list(steps)):
        if s.kind.startswith("doctor."):
            continue
        if s.kind.startswith("workorder.") and s.refs.get("status") in WORK_ORDER_STATUS:
            return WORK_ORDER_STATUS[s.refs["status"]]
        if s.kind in STATUS and s.label:
            return STATUS[s.kind]
    return None


# One stage for every doctor kind: after the device's own heal and its evidence, before
# work orders and lock-in. A tick can write several doctor events with one `ts` (open,
# refuse, close; close, then attempt 2), so among themselves they keep their write order.
DOCTOR_STAGE = 30
STAGE.update({kind: DOCTOR_STAGE for kind in LABELS if kind.startswith("doctor.")})


def stage(e: EventLike) -> int:
    """`STAGE` for this event. A Doctor fix's `overlay.applied` (refs `proposal`) is written
    just after its `doctor.proposal_result`, with the same `ts`; it takes the doctor stage so
    the story reads result → overlay (`services/proposals._apply_overlay`), not the stage of
    the device's own heal, which would put it first."""
    if e.kind == "overlay.applied" and "proposal" in (e.refs or {}):
        return DOCTOR_STAGE
    return STAGE.get(e.kind, _DEFAULT_STAGE)


def _default_label(e: EventLike) -> str:
    return e.kind.replace(".", " ").replace("_", " ")


def _default_line(e: EventLike) -> str:
    return f"{e.actor}: {_default_label(e)}."


def build_story(events: Iterable[EventLike], incident_id: str) -> Story:
    """The incident's events as ordered, readable steps. Pass every event that refs it."""
    indexed = list(enumerate(events))
    indexed.sort(key=lambda p: (p[1].ts, stage(p[1]), p[0]))
    steps = tuple(
        Step(
            at=e.ts,
            actor=e.actor,
            actor_class=e.actor_class,
            kind=e.kind,
            line=LINES.get(e.kind, _default_line)(e),
            why=e.why,
            label=LABELS.get(e.kind, _default_label)(e),
            refs=dict(e.refs),
            evidence=_evidence(e) if "recording" in e.refs else None,
            doctor=_doctor(e),
        )
        for _, e in indexed
    )
    return Story(incident_id=incident_id, steps=steps)
