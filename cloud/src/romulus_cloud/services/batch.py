"""The batch job: fold held overlays into candidates (v0 plan §7.3, D0, BUILD §3).

`maybe_run(session, now)` runs when `BATCH_INTERVAL_S` has passed since the last run, or
someone asked (`fleet_state.batch_requested`). Per device class, it folds **all** held
overlays into at most one candidate per kind (`lock_in = by_patch_kind(rules=merge_rules,
harness=write_skill, code=draft_code)`), except overlays the gate marked `solo` (bisected
out of a failed fold, `incidents.requeue_marks`): each of those gets candidates of its
own, so a failure is pinned on it alone. So a class can have several live candidates per
kind at once; the gate canaries one per install type at a time and the others go stale
and are rebuilt on the new base, so N solo overlays cost O(N²) versions (fine at this
scale; the gate caps stale re-queues per overlay):

- **rules / params → one `firmware` candidate:** the class's current manifest with each
  overlay's diff merged in, oldest first (deterministic `apply_overlay`, D3). An overlay
  whose base disagrees with the *fleet's* manifest on what it touches is a lost update and
  is `dropped`; one that conflicts only with an overlay earlier in this run stays `held`
  for the next run; a diff that can't merge is `dropped`.
- **code → one `code` candidate:** `draft_code_v0`, the FakeCoder pass-through: the
  package is the base code plus each device patch as a file (not applied), and a new
  firmware manifest names it. It waits at `awaiting_canary` (B7) unless the setting allows.
  With a real coder (`CODER_PROVIDER` not `fake`, M8b), each code overlay is first drafted
  by the coding agent as a job off the tick (`services/code_lockin.py`, `services/jobs.py`)
  and stays held until the draft is done (identical patches share one draft; at most
  CODER_MAX_DRAFTS_PER_HOUR start, beyond that it waits held, "coder budget"); then the
  overlays of one draft fold alone (a draft is a whole change against the base) into a code
  version with the coder's diff applied. A failing draft, a coder error or a diff touching
  `verify/` makes the overlay `lockin_error`, sticky, and builds nothing. In either lane, a
  held Fleet Doctor overlay whose proposal checks out uses that patch as its draft, with no
  Coder call, job or budget (D24, `services/doctor_lockin.py`); the rest is unchanged.
- **harness → one `harness` candidate:** the LLM writes one skill per overlay (FakeLlm by
  default) into the current harness tarball under `knowledge_dir`, `harness.json` bumped.

The LLM writes whys and skills, never merged config; on `LlmError` the text is templated
and the event says so. The LLM calls happen first, with nothing written and no row locked;
the writes follow in one savepoint per class, so one bad class records `batch.error`,
marks its overlays `lockin_error` (not retried, so the LLM isn't called again every tick)
and the rest proceed. `fleet_state` is touched only at the very end, so the kill switch
never waits on the LLM. Nothing here commits.
"""

import io
import json
import tarfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from functools import partial
from typing import Any

from sqlalchemy import distinct, func, select, tuple_
from sqlalchemy.orm import Session

from romulus_cloud import events
from romulus_cloud.adapters.blobstore import BlobStore, build_blobstore
from romulus_cloud.adapters.coder import CodeDraft, Coder, CoderError, build_coder, redact
from romulus_cloud.adapters.llm import FakeLlm, Llm, LlmError, build_llm
from romulus_cloud.domain import coder_retry
from romulus_cloud.domain.merge import (
    MergeError,
    apply_overlay,
    base_compatible,
    classify_patch,
    config_touches_safety,
)
from romulus_cloud.errors import ApiError
from romulus_cloud.ids import new_id
from romulus_cloud.models import (
    Candidate,
    CodeVersion,
    Device,
    Event,
    FirmwareVersion,
    FleetState,
    HarnessVersion,
    Incident,
    Overlay,
)
from romulus_cloud.services import code_lockin, doctor_lockin, fleet, incidents, registry
from romulus_cloud.services.coder_runs import patch_refusal
from romulus_cloud.services.gate import label
from romulus_cloud.services.jobs import Done, Failed, InlineJobs, Jobs, Missing, default_jobs
from romulus_cloud.settings import Settings, get_settings
from romulus_contracts import touches_safety as code_touches_safety

ACTOR = "agent:batch"
CONFIG_KINDS = frozenset({"rules", "params"})
PASSTHROUGH_NOTE = "pass-through: coding agent not wired"
LOCKIN_ERROR = "lockin_error"
CODER_UNAVAILABLE = "coder_unavailable"  # overlay.deferred reason: a provider blip, retried
Key = tuple[str, str]  # (device_id, overlay_id)


class _Unavailable:
    """The coder setting can't build a coder (e.g. no LLM_BASE_URL): every draft fails with
    that reason, so each code overlay gets a sticky why instead of the batch crashing."""

    def __init__(self, why: str) -> None:
        self.why = why

    def draft(self, patch, incident, code_rev, files=None, task=None) -> CodeDraft:
        raise CoderError(self.why)


def _build_coder(settings: Settings) -> Coder:
    try:
        return build_coder(
            settings.coder_provider,
            settings.coder_agent,
            settings.coder_image,
            base_url=settings.llm_base_url,
            model=settings.llm_model,
            api_key=settings.llm_api_key.get_secret_value(),
            test_cmd=settings.coder_test_cmd,
            step_limit=settings.coder_step_limit,
            time_limit_s=settings.coder_time_limit_s,
            scratch_dir=settings.coder_scratch_dir or None,
            max_tokens=settings.coder_max_tokens,
        )
    except (CoderError, NotImplementedError, ValueError) as exc:
        return _Unavailable(f"CODER_PROVIDER={settings.coder_provider}: {exc}")


@dataclass
class Tools:
    """What the batch needs from outside: settings and the adapters they choose. `jobs`
    runs the coding agent off the tick (only with a real coder)."""

    settings: Settings
    llm: Llm
    coder: Coder
    store: BlobStore
    jobs: Jobs = field(default_factory=InlineJobs)
    coder_blocked: str | None = None  # `code_lockin.coder_blocker`: code overlays wait

    @classmethod
    def from_settings(cls, settings: Settings) -> "Tools":
        return cls(
            settings=settings,
            llm=build_llm(
                settings.llm_mode,
                settings.llm_provider,
                settings.llm_base_url,
                settings.llm_model,
                settings.llm_api_key.get_secret_value(),
            ),
            coder=_build_coder(settings),
            store=build_blobstore(settings.blob_dir),
            jobs=default_jobs(settings.jobs_max_workers, code_lockin.DRAFT_POOL),
            coder_blocked=code_lockin.coder_blocker(settings),
        )

    @property
    def coding_agent(self) -> bool:
        """True when code patches go through the coding agent (not the pass-through)."""
        return self.settings.coder_provider != "fake"

    @property
    def secrets(self) -> list[str]:
        return [self.settings.llm_api_key.get_secret_value()]


@dataclass
class RunResult:
    ran: bool
    candidates: list[Candidate] = field(default_factory=list)
    dropped: list[tuple[str, str]] = field(default_factory=list)  # (device_id, overlay_id)
    deferred: list[tuple[str, str]] = field(default_factory=list)  # stays held
    errors: list[tuple[str, str]] = field(default_factory=list)


def is_due(session: Session, now: datetime, interval_s: float, fleet_id: str) -> bool:
    """Read-only: no row is created or locked here."""
    row = session.get(FleetState, fleet_id)
    if row is None or row.batch_requested or row.batch_last_run_at is None:
        return True
    return (now - row.batch_last_run_at).total_seconds() >= interval_s


def maybe_run(
    session: Session,
    now: datetime,
    *,
    settings: Settings | None = None,
    tools: Tools | None = None,
) -> RunResult:
    """The worker step: run the batch if it is due or was requested; otherwise do nothing."""
    tools = tools or Tools.from_settings(settings or get_settings())
    fleet_id = tools.settings.fleet_id
    if not is_due(session, now, tools.settings.batch_interval_s, fleet_id) and not (
        tools.coding_agent and _a_draft_finished(session, tools, now)
    ):
        return RunResult(ran=False)
    return run(session, now, tools)


def held_overlays(session: Session, fleet_id: str) -> list[Overlay]:
    """Held overlays not yet locked in whose incident has arrived, oldest first."""
    stmt = select(Overlay).where(
        Overlay.fleet_id == fleet_id,
        Overlay.status == "held",
        Overlay.candidate_id.is_(None),
        Overlay.incident_id.is_not(None),
    )
    return list(session.scalars(stmt.order_by(Overlay.occurred_at, Overlay.id)))


# --- preparing (LLM calls, no writes) -----------------------------------------------------


@dataclass
class _Item:
    """One held overlay with everything the LLM said about it."""

    overlay: Overlay
    incident: Incident
    device_class: str
    kinds: frozenset[str]
    explanation: str
    skill: str
    llm_note: str  # "" or why the templated fallback was used

    @property
    def key(self) -> tuple[str, str]:
        return (self.overlay.device_id, self.overlay.id)


def _incident_doc(incident: Incident, overlay: Overlay) -> dict[str, Any]:
    """What the LLM sees: the incident and its overlay, as plain data."""
    return {
        "id": incident.id,
        "device_id": incident.device_id,
        "kind": incident.kind,
        "summary": incident.summary,
        "agent_reasoning": incident.agent_reasoning,
        "actions": incident.actions,
        "resolving_action": incident.resolving_action,
        "suspected_cause": incident.suspected_cause,
        "signals": incident.signals,
        "overlay": {"id": overlay.id, "diff": overlay.diff, "code_patch": overlay.code_patch},
    }


def _llm_text(llm: Llm, method: str, doc: dict[str, Any]) -> tuple[str, str]:
    """(text, note): the LLM's text, or FakeLlm's templated text and why the LLM failed."""
    try:
        return getattr(llm, method)(doc), ""
    except LlmError as exc:
        text = getattr(FakeLlm(), method)(doc)
        return text, f"LLM unavailable ({exc}); used templated text instead"


def _prepare(session: Session, overlay: Overlay, tools: Tools) -> _Item:
    incident = session.get(Incident, overlay.incident_id)
    device = session.get(Device, overlay.device_id)
    if incident is None or device is None:
        raise ValueError(f"overlay {overlay.id} has no incident or device row")
    doc = _incident_doc(incident, overlay)
    explanation, note1 = _llm_text(tools.llm, "explain", doc)
    skill, note2 = _llm_text(tools.llm, "write_skill", doc)
    return _Item(
        overlay=overlay,
        incident=incident,
        device_class=device.device_class or "so101",
        kinds=classify_patch({"diff": overlay.diff, "code_patch": overlay.code_patch}),
        explanation=explanation,
        skill=skill,
        llm_note=note1 or note2,
    )


# --- the run ------------------------------------------------------------------------------


def run(session: Session, now: datetime, tools: Tools) -> RunResult:
    fleet_id = tools.settings.fleet_id
    row = session.get(FleetState, fleet_id)
    requested = bool(row and row.batch_requested)
    result = RunResult(ran=True)
    overlays = held_overlays(session, fleet_id)
    ready = [ov for ov in overlays if not _proposal_pending(session, ov, now, result)]
    drafts: dict[Key, tuple[str, str, CodeDraft]] = {}
    if tools.coding_agent:
        ready, drafts = _await_drafts(session, ready, now, tools, result)

    items: list[_Item] = []
    for overlay in ready:
        try:
            items.append(_prepare(session, overlay, tools))
        except Exception as exc:  # a broken row must not stop the others
            _mark_error(session, [overlay], f"preparing it failed: {exc!r}", now, result)

    classes = sorted({i.device_class for i in items})
    for device_class in classes:
        group = [i for i in items if i.device_class == device_class]
        solo = [i for i in group if incidents.requeue_marks(session, i.overlay).get("solo")]
        # Drafted code overlays fold by draft: a draft is a whole change on the base, and
        # overlays with the identical patch share one draft (and one candidate).
        by_draft: dict[str, list[_Item]] = {}
        for i in group:
            if i.key in drafts and i not in solo:
                by_draft.setdefault(drafts[i.key][1], []).append(i)
        drafted = [i for part in by_draft.values() for i in part]
        rest = [i for i in group if i not in solo and i not in drafted]
        parts = ([rest] if rest else []) + list(by_draft.values()) + [[i] for i in solo]
        for part in parts:
            marks = (len(result.dropped), len(result.deferred))
            try:
                with session.begin_nested():
                    result.candidates += _fold(session, part, now, tools, result, drafts)
            except Exception as exc:  # the savepoint rolled the part back; forget its notes
                del result.dropped[marks[0] :], result.deferred[marks[1] :]
                _mark_error(
                    session,
                    [i.overlay for i in part],
                    f"locking in class {device_class} failed: {exc!r}",
                    now,
                    result,
                )

    # Bookkeeping last, in a few statements: the fleet_state row is locked only from here
    # to the worker's commit, never across the LLM calls above (the kill switch shares it).
    state = fleet.state(session, fleet_id, lock=True)
    if requested:
        state.batch_requested = False  # a press during the run stays set, so it runs again
    state.batch_last_run_at = now
    if overlays or requested:
        # A timer run with nothing to do writes nothing, so the overnight log stays readable.
        why = (
            f"Batch ran ({'requested' if requested else 'timer'}): "
            f"{len(overlays)} held overlay(s), {len(result.candidates)} candidate(s) created, "
            f"{len(result.deferred)} left held, {len(result.dropped)} dropped, "
            f"{len(result.errors)} failed."
        )
        events.record(
            session,
            ACTOR,
            "agent",
            "batch.ran",
            {"fleet": fleet_id},
            why,
            data={
                "requested": requested,
                "candidates": [c.id for c in result.candidates],
                "deferred": [list(d) for d in result.deferred],
                "dropped": [list(d) for d in result.dropped],
                "errors": [list(e) for e in result.errors],
            },
            ts=now,
            fleet_id=fleet_id,
        )
    session.flush()
    return result


def _overlay_refs(ov: Overlay, candidate: Candidate | None = None) -> dict[str, Any]:
    refs: dict[str, Any] = {"device": ov.device_id, "overlay": ov.id}
    if ov.incident_id:
        refs["incident"] = ov.incident_id
    if candidate is not None:
        refs["candidate"] = candidate.id
    if ov.touches_safety or (candidate is not None and candidate.touches_safety):
        refs["touches_safety"] = True
    return refs


def _mark_error(
    session: Session,
    overlays: list[Overlay],
    why: str,
    now: datetime,
    result: RunResult,
    extra: dict[str, Any] | None = None,
) -> None:
    """Mark overlays `lockin_error` with the reason, so the batch doesn't retry them (and
    call the LLM again) every tick. The device keeps its local fix."""
    for ov in overlays:
        ov = session.get(Overlay, (ov.device_id, ov.id)) or ov
        if ov.status != "held":
            continue
        ov.status, ov.status_reason, ov.updated_at = LOCKIN_ERROR, why, now
        result.errors.append((ov.device_id, ov.id))
        events.record(
            session,
            ACTOR,
            "agent",
            "batch.error",
            _overlay_refs(ov),
            f"Overlay {ov.id} from {ov.device_id} could not be locked in: {why}. It is not "
            "retried; the device keeps its local fix.",
            data={"error": why, **(extra or {})},
            ts=now,
            fleet_id=ov.fleet_id,
        )
    session.flush()


def _lock_still_held(session: Session, items: list[_Item]) -> list[_Item]:
    """Lock the group's overlays and keep those still held (the device may have reverted one
    while the LLM was writing)."""
    keys = [i.key for i in items]
    rows = session.scalars(
        select(Overlay)
        .where(tuple_(Overlay.device_id, Overlay.id).in_(keys))
        .with_for_update()
        .execution_options(populate_existing=True)
    ).all()
    held = {(r.device_id, r.id) for r in rows if r.status == "held" and r.candidate_id is None}
    return [i for i in items if i.key in held]


def _current(session: Session, device_class: str, fleet_id: str):
    firmware, harness = registry.default_assignment(session, device_class, fleet_id)
    if firmware is None or harness is None:
        raise ApiError(
            409, "no_assignment", f"No firmware or harness is assigned to class {device_class}."
        )
    return (
        registry.get_firmware(session, firmware, fleet_id),
        registry.get_harness(session, harness, fleet_id),
    )


def _next(session: Session, model, fleet_id: str) -> int:
    top = session.scalar(select(func.max(model.version)).where(model.fleet_id == fleet_id))
    return (top or 0) + 1


def _fold(
    session: Session,
    group: list[_Item],
    now: datetime,
    tools: Tools,
    result: RunResult,
    drafts: dict[Key, tuple[str, str, CodeDraft]] | None = None,
) -> list[Candidate]:
    group = _lock_still_held(session, group)
    if not group:
        return []
    fleet_id = group[0].overlay.fleet_id
    current_fw, current_hv = _current(session, group[0].device_class, fleet_id)
    made: dict[tuple[str, str], list[Candidate]] = {i.key: [] for i in group}

    config, accepted = _merge_config(session, group, current_fw, now, result, tools)
    taken = [i for i in group if i.key not in {*result.dropped, *result.deferred}]
    if config is not None:
        for i in accepted:
            made[i.key].append(config)
    code_items = [i for i in taken if "code" in i.kinds]
    if code_items:
        drafts = drafts or {}
        if all(i.key in drafts for i in code_items):
            code = _lock_in_drafts(session, code_items, current_fw, now, tools, drafts)
        else:
            code = _draft_code(session, code_items, current_fw, now, tools)
        for i in code_items:
            made[i.key].append(code)
    if taken:
        harness = _write_skills(session, taken, current_hv, now, tools)
        for i in taken:
            made[i.key].append(harness)

    for i in taken:
        cands = made[i.key]
        ov = i.overlay
        notes = [
            f"no lock-in stage for {k!r}; that part is left out"
            for k in sorted(i.kinds - CONFIG_KINDS - {"code"})
        ]
        ov.status, ov.candidate_id, ov.updated_at = "locked_in", cands[0].id, now
        ov.status_reason = f"locked into {', '.join(label(c) for c in cands)}"
        events.record(
            session,
            ACTOR,
            "agent",
            "overlay.locked_in",
            _overlay_refs(ov, cands[0]),
            f"Overlay {ov.id} from {ov.device_id} was locked in as "
            f"{', '.join(label(c) for c in cands)}; the gate now replays and canaries "
            f"{'them' if len(cands) > 1 else 'it'} before the fleet gets anything."
            + "".join(f" Note: {n}." for n in notes),
            data={"candidates": [c.id for c in cands], "labels": [label(c) for c in cands]},
            ts=now,
            fleet_id=fleet_id,
        )
    session.flush()
    return list({c.id: c for cands in made.values() for c in cands}.values())


def _defer(session: Session, item: _Item, why: str, now: datetime, result: RunResult) -> None:
    """Leave the overlay held for the next run, and say why (no state change)."""
    result.deferred.append(item.key)
    events.record(
        session,
        ACTOR,
        "agent",
        "overlay.deferred",
        _overlay_refs(item.overlay),
        f"Overlay {item.overlay.id} from {item.overlay.device_id} stays held for the next "
        f"batch: {why}.",
        ts=now,
        fleet_id=item.overlay.fleet_id,
    )


def _drop(session: Session, item: _Item, why: str, now: datetime, data, result) -> None:
    ov = item.overlay
    ov.status, ov.status_reason, ov.updated_at = "dropped", why, now
    result.dropped.append(item.key)
    events.record(
        session,
        ACTOR,
        "agent",
        "overlay.dropped",
        _overlay_refs(ov),
        f"Overlay {ov.id} from {ov.device_id} was not locked in: {why}.",
        data=data,
        ts=now,
        fleet_id=ov.fleet_id,
    )


def _merge_config(
    session: Session,
    group: list[_Item],
    current: FirmwareVersion,
    now: datetime,
    result: RunResult,
    tools: Tools,
) -> tuple[Candidate | None, list[_Item]]:
    """Fold every rules/params diff into one manifest, oldest first. No LLM touches it."""
    manifest = current.manifest
    accepted: list[_Item] = []
    results = []
    for item in group:
        if not item.kinds & CONFIG_KINDS:
            continue
        ov = item.overlay
        base = (
            session.get(FirmwareVersion, (ov.fleet_id, ov.base_firmware))
            if ov.base_firmware
            else None
        )
        base_manifest = base.manifest if base else None
        diff = {k: v for k, v in (ov.diff or {}).items() if k in CONFIG_KINDS}
        fleet_ok = base_compatible(base_manifest, current.manifest, diff)
        if not fleet_ok:
            _drop(session, item, fleet_ok.why, now, {"conflicts": list(fleet_ok.conflicts)}, result)
            continue
        run_ok = base_compatible(base_manifest, manifest, diff)
        if not run_ok:
            others = ", ".join(i.overlay.id for i in accepted)
            _defer(
                session,
                item,
                f"it conflicts with {others} in this run ({', '.join(run_ok.conflicts)})",
                now,
                result,
            )
            continue
        try:
            merged = apply_overlay(manifest, diff)
        except MergeError as exc:
            _drop(session, item, str(exc), now, {"problems": exc.problems}, result)
            continue
        manifest = merged.manifest
        accepted.append(item)
        results.append(merged)
    if not accepted:
        return None, []

    version = _next(session, FirmwareVersion, current.fleet_id)
    final = apply_overlay(manifest, {}, version=version)  # validates and sets the version
    whole = apply_overlay(current.manifest, None)  # baseline for the overall change list
    names = tools.settings.safety_paths
    safety = any(i.overlay.touches_safety for i in accepted) or any(
        config_touches_safety(r, safety_rules=names, safety_params=names) for r in results
    )
    old_rules, new_rules = whole.manifest.get("rules") or {}, final.manifest["rules"]
    changes = [f"{i.overlay.id}: {r.why}" for i, r in zip(accepted, results, strict=True)]
    registry.create_firmware(
        session,
        final.manifest,
        actor=ACTOR,
        actor_class="agent",
        why=f"Firmware v{version} = v{current.version} + {len(accepted)} overlay(s) "
        f"({'; '.join(changes)}); merged deterministically, no LLM.",
        source="batch",
        parent_version=current.version,
        fleet_id=current.fleet_id,
    )
    cand = _new_candidate(
        session,
        accepted,
        now,
        tools,
        type_="firmware",
        version=version,
        code_rev=None,
        why=f"v{current.version} + {'; '.join(changes)}. "
        + " ".join(i.explanation for i in accepted),
        touches_safety=safety,
        stub_stages=[],
        data={
            "parent_version": current.version,
            "changed_rules": sorted(r for r in new_rules if old_rules.get(r) != new_rules[r]),
            "removed_rules": sorted(set(old_rules) - set(new_rules)),
        },
    )
    return cand, accepted


def _new_candidate(
    session: Session,
    items: list[_Item],
    now: datetime,
    tools: Tools,
    *,
    type_: str,
    version: int,
    code_rev: str | None,
    why: str,
    touches_safety: bool,
    stub_stages: list[str],
    data: dict[str, Any],
) -> Candidate:
    first = items[0]
    incident_ids = list(dict.fromkeys(i.incident.id for i in items))
    cand = Candidate(
        id=new_id("cand"),
        fleet_id=first.overlay.fleet_id,
        type=type_,
        version=version,
        code_rev=code_rev,
        device_class=first.device_class,
        from_overlays=[
            {"device_id": i.overlay.device_id, "overlay_id": i.overlay.id} for i in items
        ],
        status="replaying",
        why=why,
        touches_safety=touches_safety,
        stub_stages=stub_stages,
        incident_id=first.incident.id,
        created_at=now,
        updated_at=now,
    )
    session.add(cand)
    session.flush()
    notes = list(dict.fromkeys(i.llm_note for i in items if i.llm_note))
    llm = "".join(f" {n}." for n in notes)
    safety = (
        " It touches the safety layer or visor: simulated canary only, under the kill switch (K17)."
        if touches_safety
        else ""
    )
    stubs = f" Stub stages: {', '.join(stub_stages)}." if stub_stages else ""
    overlays = ", ".join(i.overlay.id for i in items)
    refs: dict[str, Any] = {
        "candidate": cand.id,
        "incident": first.incident.id,
        "incidents": incident_ids,
    }
    if touches_safety:
        refs["touches_safety"] = True
    events.record(
        session,
        ACTOR,
        "agent",
        "candidate.created",
        refs,
        f"Candidate {label(cand)} from overlay(s) {overlays}: {why}{safety}{stubs}{llm}",
        data={
            "type": type_,
            "version": version,
            "code_rev": code_rev,
            "llm_model": tools.llm.model,
            "llm_fallback": bool(notes),
            "stub_stages": stub_stages,
            "overlays": cand.from_overlays,
            **data,
        },
        ts=now,
        fleet_id=cand.fleet_id,
    )
    return cand


def _skill_name(incident: Incident) -> str:
    return f"{incident.kind}-{incident.id[:8]}.md".replace("/", "-")


def _read_tarball(store: BlobStore, sha256: str) -> dict[str, bytes]:
    with registry.open_package(store, sha256, "the base package") as stream:
        data = stream.read()
    files: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        for member in tar.getmembers():
            if member.isfile():
                files[member.name.removeprefix("./")] = tar.extractfile(member).read()  # type: ignore[union-attr]
    return files


def _write_skills(
    session: Session, items: list[_Item], current: HarnessVersion, now: datetime, tools: Tools
) -> Candidate:
    """harness: one skill per overlay, all in one new harness version (D22)."""
    files = _read_tarball(tools.store, current.sha256)
    meta = json.loads(files.get("harness.json", b"{}"))
    version = _next(session, HarnessVersion, current.fleet_id)
    knowledge_dir = (meta.get("knowledge_dir") or "skills/").rstrip("/") + "/"
    names = []
    for item in items:
        name = knowledge_dir + _skill_name(item.incident)
        files[name] = item.skill.encode()
        names.append(name)
    files["harness.json"] = json.dumps({**meta, "version": version}, indent=2).encode() + b"\n"
    registry.create_harness(
        session,
        tools.store,
        registry.build_tarball(files),
        actor=ACTOR,
        actor_class="agent",
        why=f"Harness v{version} = v{current.version} + {', '.join(names)}, written by "
        f"{tools.llm.model}.",
        source="batch",
        parent_version=current.version,
        fleet_id=current.fleet_id,
    )
    kinds = ", ".join(dict.fromkeys(i.incident.kind for i in items))
    return _new_candidate(
        session,
        items,
        now,
        tools,
        type_="harness",
        version=version,
        code_rev=None,
        why=f"v{current.version} + {len(names)} skill(s) on handling {kinds}. "
        + " ".join(i.explanation for i in items),
        touches_safety=False,
        stub_stages=[],
        data={"parent_version": current.version, "skills": names},
    )


def _draft_code(
    session: Session, items: list[_Item], current: FirmwareVersion, now: datetime, tools: Tools
) -> Candidate:
    """code: `draft_code_v0`, the FakeCoder pass-through (BUILD §3). Never promotes by default."""
    base_rev = current.manifest.get("code_rev")
    base = session.get(CodeVersion, (current.fleet_id, base_rev))
    files = _read_tarball(tools.store, base.sha256) if base else {}
    notes, proposals = [], []
    doctor_safety = False  # a Doctor proposal's own touches_safety (D24)
    for item in items:
        ov = item.overlay
        doctor = doctor_lockin.proposal_draft(session, ov, now, base_rev=base_rev)
        draft = doctor or tools.coder.draft(
            ov.code_patch or "", {"id": item.incident.id}, ov.base_code_rev or base_rev
        )
        if doctor:
            proposals.append(doctor.proposal_id)
            doctor_safety = doctor_safety or doctor.touches_safety
        files[f"romulus/patches/{ov.id}.diff"] = draft.diff.encode()
        notes.append(draft.notes)
    code, _ = registry.create_code(
        session,
        tools.store,
        registry.build_tarball(files),
        rev=None,
        actor=ACTOR,
        actor_class="agent",
        why=f"Code from {len(items)} overlay(s) on {base_rev}: {PASSTHROUGH_NOTE}; the package "
        "is the base code plus each device patch as a file, not applied.",
        source="passthrough",
        parent_rev=base_rev,
        fleet_id=current.fleet_id,
    )
    version = _next(session, FirmwareVersion, current.fleet_id)
    manifest = {**current.manifest, "version": version, "code_rev": code.rev}
    registry.create_firmware(
        session,
        manifest,
        actor=ACTOR,
        actor_class="agent",
        why=f"Firmware v{version} = v{current.version} with code {code.rev} ({PASSTHROUGH_NOTE}).",
        source="batch",
        parent_version=current.version,
        fleet_id=current.fleet_id,
    )
    safety = (
        doctor_safety
        or any(i.overlay.touches_safety for i in items)
        or any(
            code_touches_safety(i.overlay.code_patch, tools.settings.safety_paths) for i in items
        )
    )
    return _new_candidate(
        session,
        items,
        now,
        tools,
        type_="code",
        version=version,
        code_rev=code.rev,
        why=f"{PASSTHROUGH_NOTE}: the devices' own patches, unchanged ({'; '.join(notes)}). "
        + " ".join(i.explanation for i in items),
        touches_safety=safety,
        stub_stages=["draft_code", "replay"],
        data={
            "parent_version": current.version,
            **doctor_lockin.candidate_data(proposals, whole=len(proposals) == len(items)),
        },
    )


# --- the coding agent (M8b, F11): drafts run as jobs off the tick -------------------------


def _has_code(ov: Overlay) -> bool:
    return "code" in classify_patch({"diff": ov.diff, "code_patch": ov.code_patch})


def _base_code(session: Session, ov: Overlay) -> CodeVersion:
    """The code version the overlay's class runs now: the coder's base tree (B1: until
    `device/` lands, whatever was registered with `POST /v1/code`)."""
    device = session.get(Device, ov.device_id)
    device_class = (device.device_class if device else None) or "so101"
    firmware, _ = _current(session, device_class, ov.fleet_id)
    rev = firmware.manifest.get("code_rev")
    cv = session.get(CodeVersion, (ov.fleet_id, rev)) if rev else None
    if cv is None:
        raise CoderError(
            f"class {device_class} runs firmware v{firmware.version}, whose code {rev!r} is "
            "not registered (POST /v1/code), so the coding agent has no base tree"
        )
    return cv


def _a_draft_finished(session: Session, tools: Tools, now: datetime) -> bool:
    """A held code overlay's draft is done (or failed), or its Fleet Doctor proposal checks
    out (D24: it is its own draft) and changed since the last run (once, not every tick), so
    the batch should run now rather than wait for BATCH_INTERVAL_S. Reads only; polls
    in-memory jobs."""
    state = session.get(FleetState, tools.settings.fleet_id)
    since = state.batch_last_run_at if state else None
    for ov in held_overlays(session, tools.settings.fleet_id):
        if not _has_code(ov):
            continue
        try:
            rev = _base_code(session, ov).rev
            key = code_lockin.draft_key(ov, rev)
        except Exception:  # the run itself records why
            return True
        if isinstance(tools.jobs.poll(key), Done | Failed):
            return True
        if not tools.coder_blocked and doctor_lockin.newly_verified(
            session, ov, rev, now, since=since
        ):
            return True
    return False


def _coder_incident(session: Session, ov: Overlay) -> dict[str, Any]:
    incident = session.get(Incident, ov.incident_id)
    if incident is None:
        return {"id": ov.incident_id, "device_id": ov.device_id}
    return {
        "id": incident.id,
        "device_id": incident.device_id,
        "kind": incident.kind,
        "summary": incident.summary,
        "explanation": incident.agent_reasoning,
    }


def _drafts_this_hour(session: Session, fleet_id: str, now: datetime) -> int:
    """Coder drafts started in the last hour (from `coder.started` events, so a restart
    doesn't reset the budget), minus attempts the provider turned away with exactly 0 tokens
    spent, counted once per job attempt (overlays sharing a draft share its job): the
    batch's own (`overlay.deferred`, `batch.error`) and the Fleet Doctor's (`doctor.declined`),
    the same count as `doctor_code.coder_budget`."""
    recent = (Event.fleet_id == fleet_id, Event.ts > now - timedelta(hours=1))
    started = select(func.count(Event.id)).where(*recent, Event.kind == "coder.started")
    job_attempt = Event.data["job"].astext.concat(":").concat(Event.data["attempt"].astext)
    free = select(func.count(distinct(job_attempt))).where(
        *recent,
        Event.kind.in_(["overlay.deferred", "batch.error", "doctor.declined"]),
        Event.data.contains({"reason": CODER_UNAVAILABLE, "no_tokens": True}),
    )
    return max((session.scalar(started) or 0) - (session.scalar(free) or 0), 0)


@dataclass(frozen=True)
class _Blip:
    """A draft job's transient-failure state: its latest attempt number, when it may run
    again (None after the last attempt), and each attempt's reason."""

    attempt: int = 0
    retry_at: datetime | None = None
    reasons: tuple[str, ...] = ()


def _blip_state(session: Session, fleet_id: str, key: str) -> _Blip:
    """From events (so the backoff survives a restart), per draft key: a later overlay with
    the same patch inherits the backoff of the job it shares."""
    stmt = (
        select(Event)
        .where(
            Event.fleet_id == fleet_id,
            Event.kind == "overlay.deferred",
            Event.data.contains({"reason": CODER_UNAVAILABLE, "job": key}),
        )
        .order_by(Event.ts, Event.id)
    )
    by_attempt: dict[int, Event] = {}
    for e in session.scalars(stmt):
        by_attempt.setdefault(int(e.data.get("attempt") or 0), e)
    if not by_attempt:
        return _Blip()
    last = by_attempt[max(by_attempt)]
    return _Blip(
        attempt=max(by_attempt),
        retry_at=datetime.fromisoformat(last.data["retry_at"]),
        reasons=tuple(by_attempt[n].data.get("detail", "") for n in sorted(by_attempt)),
    )


def _coder_failed(
    session: Session,
    ov: Overlay,
    key: str,
    outcome: coder_retry.CoderOutcome,
    why: str,
    now: datetime,
    result: RunResult,
    blips: dict[str, _Blip],
) -> None:
    """A failed draft: a provider blip defers the overlay with a backoff (bounded, per draft
    key); a bad draft, or the 5th blip in a row, makes it sticky `lockin_error` (BUILD §8).
    `blips` holds this run's new state per key, so overlays sharing one job record the same
    attempt."""
    verdict = coder_retry.classify(outcome)
    if not verdict.transient:
        _mark_error(session, [ov], why, now, result)
        return
    if key not in blips:
        past = _blip_state(session, ov.fleet_id, key)
        attempt = past.attempt + 1
        blips[key] = _Blip(attempt, coder_retry.retry_at(attempt, now), (*past.reasons, why))
    blip = blips[key]
    data = {
        "reason": CODER_UNAVAILABLE,
        "job": key,
        "attempt": blip.attempt,
        "label": verdict.label,
        "detail": why,
        "no_tokens": outcome.tokens == 0,  # a known zero only: None may have spent tokens
    }
    if blip.retry_at is None:
        listed = "; ".join(f"attempt {i}: {r}" for i, r in enumerate(blip.reasons, 1))
        why = f"the coding agent was unavailable {blip.attempt} times in a row ({listed})"
        _mark_error(session, [ov], why, now, result, extra=data)
        return
    result.deferred.append((ov.device_id, ov.id))
    events.record(
        session,
        ACTOR,
        "agent",
        "overlay.deferred",
        _overlay_refs(ov),
        f"Overlay {ov.id} from {ov.device_id} stays held: coder unavailable ({verdict.label}), "
        f"retry after {blip.retry_at:%H:%M} UTC (attempt {blip.attempt} of "
        f"{coder_retry.MAX_TRANSIENT}). {why}.",
        data={**data, "retry_at": blip.retry_at.isoformat()},
        ts=now,
        fleet_id=ov.fleet_id,
    )


def _outcome(draft: CodeDraft) -> coder_retry.CoderOutcome:
    return coder_retry.CoderOutcome(
        passed=draft.passed,
        exit_status=draft.exit_status,
        error=draft.error,
        steps=draft.steps,
        tokens=draft.tokens,
        no_result=draft.no_result,
    )


def _say_once(session: Session, ov: Overlay, reason: str, why: str, now: datetime) -> None:
    """An `overlay.deferred` event with `reason`, unless its last batch step already said so
    (a waiting overlay doesn't add an event every batch run)."""
    kinds = ["overlay.deferred", "overlay.requeued", "coder.started"]
    past = events.find(session, {"device": ov.device_id, "overlay": ov.id}, kinds=kinds)
    last = past[-1] if past else None
    if last and last.kind == "overlay.deferred" and (last.data or {}).get("reason") == reason:
        return
    events.record(
        session,
        ACTOR,
        "agent",
        "overlay.deferred",
        _overlay_refs(ov),
        f"Overlay {ov.id} from {ov.device_id} stays held for the next batch: {why}.",
        data={"reason": reason},
        ts=now,
        fleet_id=ov.fleet_id,
    )


def _proposal_pending(session: Session, ov: Overlay, now: datetime, result: RunResult) -> bool:
    """A held code overlay whose Fleet Doctor proposal is still `offered` (the device's result
    hasn't arrived) waits held: no Coder job yet, so a later `applied` can't orphan one."""
    if not doctor_lockin.claims(ov) or not _has_code(ov):
        return False
    try:
        rev = _base_code(session, ov).rev
    except Exception:  # the lanes record why
        return False
    if not doctor_lockin.pending(session, ov, rev, now):
        return False
    result.deferred.append((ov.device_id, ov.id))
    pid = (ov.raw or {}).get("proposal_id")
    _say_once(
        session,
        ov,
        "doctor_pending",
        f"its Fleet Doctor proposal {pid} is still offered; it is locked in once the device's "
        "result arrives, or goes to the Coder if the offer expires",
        now,
    )
    return True


def _await_drafts(
    session: Session, overlays: list[Overlay], now: datetime, tools: Tools, result: RunResult
) -> tuple[list[Overlay], dict[Key, tuple[str, str, CodeDraft]]]:
    """Send each held code overlay to the coding agent as a job, and keep it held until its
    draft is done. Returns the overlays ready to fold and each one's (base rev, draft key,
    draft).

    - Identical patches on the same code version share one draft (the key is their hash).
    - A verified Fleet Doctor proposal is its own draft: no job, no budget (D24).
    - At most CODER_MAX_DRAFTS_PER_HOUR drafts start per hour; over it, the overlay waits
      `held` ("coder budget"), not sticky.
    - A failing draft, a coder error or a diff touching `verify/` marks the overlay
      `lockin_error` (sticky: never re-drafted, so the agent isn't called again every run),
      unless the failure is transient (`domain.coder_retry`: a rate limit, 5xx, timeout, or an
      agent that spent no tokens): then it stays held and is retried after a backoff of 2 min
      doubling to 30 min, and only the 5th transient failure in a row is sticky, listing every
      attempt's reason. The backoff belongs to the draft key, so an overlay with the same
      patch arriving later waits too. An attempt known to have spent 0 tokens is given back
      to the hourly budget, once per job attempt however many overlays share it.
    - With no way to run the coder here (`coder_blocker`), code overlays just stay held: the
      worker logged why once at startup.
    """
    ready: list[Overlay] = []
    drafts: dict[Key, tuple[str, str, CodeDraft]] = {}
    consumed: set[str] = set()
    blips: dict[str, _Blip] = {}
    budget = tools.settings.coder_max_drafts_per_hour
    started = _drafts_this_hour(session, tools.settings.fleet_id, now)
    for ov in overlays:
        if not _has_code(ov):
            ready.append(ov)
            continue
        if tools.coder_blocked:
            result.deferred.append((ov.device_id, ov.id))
            continue
        try:
            cv = _base_code(session, ov)
            # D24: a verified Doctor proposal is its own draft (no job, no budget); it still
            # gets patch_refusal below and the gate's replay.
            doctor = doctor_lockin.proposal_draft(session, ov, now, base_rev=cv.rev)
            key = doctor.key if doctor else code_lockin.draft_key(ov, cv.rev)
            status = Done(doctor) if doctor else tools.jobs.poll(key)
            if isinstance(status, Missing):  # new, or the worker restarted: (re)submit
                wait = _blip_state(session, ov.fleet_id, key).retry_at
                if wait is not None and now < wait:  # this job's backoff, shared by its key
                    result.deferred.append((ov.device_id, ov.id))
                    continue
                if started >= budget:
                    result.deferred.append((ov.device_id, ov.id))
                    _say_once(
                        session,
                        ov,
                        "coder_budget",
                        f"coder budget: {started} drafts started in the last hour "
                        f"(CODER_MAX_DRAFTS_PER_HOUR={budget}); it is drafted when the budget "
                        "frees up",
                        now,
                    )
                    continue
                files = code_lockin.read_package(tools.store, cv)
                incident = _coder_incident(session, ov)
                job = partial(tools.coder.draft, ov.code_patch or "", incident, cv.rev, files)
                tools.jobs.submit(key, job)
                started += 1
                events.record(
                    session,
                    ACTOR,
                    "agent",
                    "coder.started",
                    _overlay_refs(ov),
                    f"The coding agent started drafting a clean change for overlay {ov.id} "
                    f"from {ov.device_id} on code {cv.rev} (CODER_PROVIDER="
                    f"{tools.settings.coder_provider}, a job off the gate's tick); the overlay "
                    "stays held until the draft is done.",
                    data={"job": key, "base_code_rev": cv.rev},
                    ts=now,
                    fleet_id=ov.fleet_id,
                )
                status = tools.jobs.poll(key)
        except Exception as exc:
            why = redact(f"the coding agent could not start: {exc}", tools.secrets)
            _mark_error(session, [ov], why, now, result)
            continue
        if isinstance(status, Failed | Done):
            consumed.add(key)
        if isinstance(status, Failed):
            why = redact(f"the coding agent could not run ({status.error})", tools.secrets)
            failed = coder_retry.CoderOutcome(False, error=redact(status.error, tools.secrets))
            _coder_failed(session, ov, key, failed, why, now, result, blips)
        elif isinstance(status, Done):
            draft = status.result
            refused = patch_refusal(draft.diff) if draft.passed else None
            if refused:
                why = redact(f"the coding agent's diff is refused: {refused}", tools.secrets)
                _mark_error(session, [ov], why, now, result)
            elif draft.passed:
                drafts[(ov.device_id, ov.id)] = (cv.rev, key, draft)
                ready.append(ov)
            else:
                summary = code_lockin.draft_summary(draft)
                why = redact(f"the coding agent's draft did not pass: {summary}", tools.secrets)
                _coder_failed(session, ov, key, _outcome(draft), why, now, result, blips)
        else:  # still running: stays held, nothing written
            result.deferred.append((ov.device_id, ov.id))
    for key in consumed:  # applied below (or marked); identical patches shared it this run
        tools.jobs.forget(key)
    return ready, drafts


def _lock_in_drafts(
    session: Session,
    items: list[_Item],
    current: FirmwareVersion,
    now: datetime,
    tools: Tools,
    drafts: dict[Key, tuple[str, str, CodeDraft]],
) -> Candidate:
    """code: the coder's diff applied to the base package → a new code version (`source =
    coder`), a firmware manifest naming it, and a code candidate the gate replays. A verified
    Fleet Doctor proposal (D24) goes the same way with its patch as the diff, as a `source =
    doctor` code version (the gate replays both for real) and with the proposal's own
    `touches_safety` OR'd in."""
    base_rev = current.manifest.get("code_rev")
    base = session.get(CodeVersion, (current.fleet_id, base_rev)) if base_rev else None
    if base is None:
        raise CoderError(f"the base code {base_rev!r} is not registered")
    files = code_lockin.read_package(tools.store, base)
    notes, diffs, raw_diffs, flags, proposals = [], [], [], [], []
    doctor_safety = False  # a Doctor proposal's own touches_safety (D24)
    applied: set[str] = set()
    for item in items:
        rev, key, draft = drafts[item.key]
        if rev != base_rev:
            raise CoderError(
                f"overlay {item.overlay.id}'s draft is on code {rev}, but the class runs {base_rev}"
            )
        if key in applied:  # the same draft as an earlier overlay in this part
            continue
        applied.add(key)
        if isinstance(draft, doctor_lockin.DoctorDraft):
            proposals.append(draft.proposal_id)
            doctor_safety = doctor_safety or draft.touches_safety
        files = code_lockin.apply_diff(files, draft.diff)
        notes.append(redact(code_lockin.draft_summary(draft), tools.secrets))
        raw_diffs.append(draft.diff)
        diffs.append(redact(draft.diff, tools.secrets))
        flags += list(draft.flags)
    ids = ", ".join(i.overlay.id for i in items)
    doctor = len(proposals) == len(applied)  # a part is one draft, so all or none
    if doctor:
        by = f"the Fleet Doctor (proposal {', '.join(proposals)}, no second Coder pass, D24)"
        code_why = f"Code drafted by {by} on {base_rev} for overlay(s) {ids}: the base "
        code_why += "package with the proposal's checked patch applied."
    else:
        by = "the coding agent"
        code_why = f"Code drafted by the coding agent on {base_rev} for overlay(s) {ids}: the "
        code_why += "base package with the agent's diff applied."
    code, _ = registry.create_code(
        session,
        tools.store,
        registry.build_tarball(files),
        rev=None,
        actor=ACTOR,
        actor_class="agent",
        why=code_why,
        source=doctor_lockin.SOURCE if doctor else code_lockin.SOURCE,
        parent_rev=base_rev,
        fleet_id=current.fleet_id,
    )
    version = _next(session, FirmwareVersion, current.fleet_id)
    manifest = {**current.manifest, "version": version, "code_rev": code.rev}
    registry.create_firmware(
        session,
        manifest,
        actor=ACTOR,
        actor_class="agent",
        why=f"Firmware v{version} = v{current.version} with code {code.rev} (drafted by {by}).",
        source="batch",
        parent_version=current.version,
        fleet_id=current.fleet_id,
    )
    safety = (
        doctor_safety
        or any(i.overlay.touches_safety for i in items)
        or any(code_touches_safety(d, tools.settings.safety_paths) for d in raw_diffs)
    )
    flagged = f" Flagged for review: {'; '.join(flags)}." if flags else ""
    who = "the Fleet Doctor" if doctor else "the coding agent"
    replay = (
        " The gate replays it in fresh containers (device tests and held-out verify "
        "scripts) before any device gets it."
    )
    return _new_candidate(
        session,
        items,
        now,
        tools,
        type_="code",
        version=version,
        code_rev=code.rev,
        why=f"{who}'s change on code {base_rev}: {' '.join(notes)}{flagged}{replay} "
        + " ".join(i.explanation for i in items),
        touches_safety=safety,
        stub_stages=[],
        data={
            "parent_version": current.version,
            "base_code_rev": base_rev,
            "diff": "\n".join(diffs),
            "coder_flags": flags,
            **doctor_lockin.candidate_data(proposals, whole=doctor),
        },
    )
