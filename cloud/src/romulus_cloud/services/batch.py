"""The batch job: fold held overlays into candidates (v0 plan §7.3, D0, BUILD §3).

`maybe_run(session, now)` runs when `BATCH_INTERVAL_S` has passed since the last run, or
someone asked (`fleet_state.batch_requested`). Per device class, it folds **all** held
overlays into at most one candidate per kind (`lock_in = by_patch_kind(rules=merge_rules,
harness=write_skill, code=draft_code)`):

- **rules / params → one `firmware` candidate:** the class's current manifest with each
  overlay's diff merged in, oldest first (deterministic `apply_overlay`, D3). An overlay
  whose base disagrees with the *fleet's* manifest on what it touches is a lost update and
  is `dropped`; one that conflicts only with an overlay earlier in this run stays `held`
  for the next run; a diff that can't merge is `dropped`.
- **code → one `code` candidate** (`draft_code`, M8, F11): the coder (`CODER_PROVIDER`)
  gets each code overlay's patch, its incident and the class's code tree, in order, each on
  top of the last; with `local_agent` it writes a clean change and runs the device tests in
  the sandbox. The new tree becomes a code version and a new firmware manifest names it
  (the coder never edits the manifest). An overlay whose draft fails (coder error, failing
  tests) gets `lockin_error` with the coder's why and test output, and is not retried.
  With FakeCoder (default) it is the labeled pass-through: the base code plus each device
  patch as a file, not applied. Code candidates wait at `awaiting_canary` until a canary
  runs real code (B7).
- **harness → one `harness` candidate:** the LLM writes one skill per overlay (FakeLlm by
  default) into the current harness tarball under `knowledge_dir`, `harness.json` bumped.

The LLM writes whys and skills, never merged config; on `LlmError` the text is templated
and the event says so. The LLM and coder calls happen first, with no row locked;
the writes follow in one savepoint per class, so one bad class records `batch.error`,
marks its overlays `lockin_error` (not retried, so the LLM isn't called again every tick)
and the rest proceed. `fleet_state` is touched only at the very end, so the kill switch
never waits on the LLM. Nothing here commits.
"""

import io
import json
import tarfile
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import func, select, tuple_
from sqlalchemy.orm import Session

from romulus_cloud import events
from romulus_cloud.adapters.blobstore import BlobStore, build_blobstore
from romulus_cloud.adapters.coder import CodeDraft, Coder, build_coder
from romulus_cloud.adapters.llm import FakeLlm, Llm, LlmError, build_llm
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
    FirmwareVersion,
    FleetState,
    HarnessVersion,
    Incident,
    Overlay,
)
from romulus_cloud.services import fleet, registry
from romulus_cloud.services.gate import label
from romulus_cloud.settings import Settings, get_settings
from romulus_contracts import touches_safety as code_touches_safety

ACTOR = "agent:batch"
CONFIG_KINDS = frozenset({"rules", "params"})
PASSTHROUGH_NOTE = "pass-through: coding agent not wired"
LOCKIN_ERROR = "lockin_error"
WHY_TAIL = 1500  # characters of coder test output kept in a why (the event data has it all)


@dataclass
class Tools:
    """What the batch needs from outside: settings and the adapters they choose."""

    settings: Settings
    llm: Llm
    coder: Coder
    store: BlobStore

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
            coder=build_coder(settings),
            store=build_blobstore(settings.blob_dir),
        )


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
    if not is_due(session, now, tools.settings.batch_interval_s, fleet_id):
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

    items: list[_Item] = []
    for overlay in overlays:
        try:
            items.append(_prepare(session, overlay, tools))
        except Exception as exc:  # a broken row must not stop the others
            _mark_error(session, [overlay], f"preparing it failed: {exc!r}", now, result)

    classes = sorted({i.device_class for i in items})
    plans = {c: _draft_class_code(session, items, c, now, tools, result) for c in classes}
    failed = set(result.errors)
    items = [i for i in items if i.key not in failed]
    for device_class in classes:
        group = [i for i in items if i.device_class == device_class]
        if not group:
            continue
        marks = (len(result.dropped), len(result.deferred))
        try:
            with session.begin_nested():
                result.candidates += _fold(session, group, now, tools, result, plans[device_class])
        except Exception as exc:  # the savepoint rolled the class back; forget its notes
            del result.dropped[marks[0] :], result.deferred[marks[1] :]
            _mark_error(
                session,
                [i.overlay for i in group],
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
    data: dict[str, Any] | None = None,
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
            data={"error": why, **(data or {})},
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
    plan: "_CodePlan | None",
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
        code = _draft_code(session, code_items, current_fw, now, tools, plan)
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


# --- code (M8, F11) -----------------------------------------------------------------------


@dataclass
class _CodePlan:
    """The coder's work for one class, done before any row is locked.

    `keys` are the overlays drafted, in order, each on top of the last; `files` is the
    final tree, or None when the coder built none (FakeCoder's pass-through).
    """

    base_rev: str | None
    keys: list[tuple[str, str]] = field(default_factory=list)
    drafts: list[CodeDraft] = field(default_factory=list)
    files: dict[str, bytes] | None = None


def _tail(text: str, cap: int = WHY_TAIL) -> str:
    return text if len(text) <= cap else "…" + text[-cap:]


def _draft_class_code(
    session: Session,
    items: list[_Item],
    device_class: str,
    now: datetime,
    tools: Tools,
    result: RunResult,
) -> _CodePlan | None:
    """Run the coder on the class's code overlays, oldest first, each on the tree the last
    one left. A failed draft marks its overlay `lockin_error` (never retried) and the next
    one goes on from the tree before it. No row is locked while the coder runs."""
    code_items = [i for i in items if i.device_class == device_class and "code" in i.kinds]
    if not code_items:
        return None
    fleet_id = code_items[0].overlay.fleet_id
    firmware, _ = registry.default_assignment(session, device_class, fleet_id)
    fw = session.get(FirmwareVersion, (fleet_id, firmware)) if firmware is not None else None
    if fw is None:
        return None  # _fold reports the missing assignment for the whole class
    plan = _CodePlan(base_rev=fw.manifest.get("code_rev"))
    base = session.get(CodeVersion, (fleet_id, plan.base_rev)) if plan.base_rev else None
    tree = _read_tarball(tools.store, base.sha256) if base else {}
    for item in code_items:
        ov = item.overlay
        try:
            draft = tools.coder.draft(
                ov.code_patch or "",
                _incident_doc(item.incident, ov),
                plan.base_rev or ov.base_code_rev or "",
                registry.build_tarball(tree),
            )
        except Exception as exc:  # CoderError or a bug: same outcome, the why says which
            _mark_error(session, [ov], f"the coding agent failed: {exc}", now, result)
            continue
        if not draft.passed:
            why = (
                f"the coding agent's change did not pass the device tests ({draft.notes}). "
                f"Test output:\n{_tail(draft.test_output) or '(none)'}"
            )
            data = {"coder": {"diff": draft.diff, "test_output": draft.test_output}}
            _mark_error(session, [ov], why, now, result, data)
            continue
        plan.keys.append(item.key)
        plan.drafts.append(draft)
        if draft.files is not None:
            tree = plan.files = draft.files
    return plan


def _draft_code(
    session: Session,
    items: list[_Item],
    current: FirmwareVersion,
    now: datetime,
    tools: Tools,
    plan: _CodePlan | None,
) -> Candidate:
    """code: store the coder's tree as a code version, name it in a new firmware manifest,
    and make the candidate. Never promotes by default (B7)."""
    base_rev = current.manifest.get("code_rev")
    if plan is None or plan.keys != [i.key for i in items] or plan.base_rev != base_rev:
        raise ValueError(
            "the code drafted before locking no longer matches this lock-in (the class's code "
            "or its overlays changed while the coder ran)"
        )
    drafts = plan.drafts
    if plan.files is None:  # FakeCoder: the labeled pass-through (draft_code_v0)
        base = session.get(CodeVersion, (current.fleet_id, base_rev))
        files = _read_tarball(tools.store, base.sha256) if base else {}
        for item, draft in zip(items, drafts, strict=True):
            files[f"romulus/patches/{item.overlay.id}.diff"] = draft.diff.encode()
        source, stub_stages = "passthrough", ["draft_code", "replay"]
        how = PASSTHROUGH_NOTE
        code_why = (
            f"{PASSTHROUGH_NOTE}; the package is the base code plus each device patch as a "
            "file, not applied."
        )
        cand_why = (
            f"{PASSTHROUGH_NOTE}: the devices' own patches, unchanged "
            f"({'; '.join(d.notes for d in drafts)})."
        )
    else:
        files, source = plan.files, "coder"
        agents = sorted({d.agent for d in drafts if d.agent})
        drafted = all(d.agent for d in drafts)
        # No agent drafted it: the device's own patch, tested. The gate treats that as a
        # pass-through (`draft_code` stub) and holds it unless the setting allows.
        stub_stages = [] if drafted else ["draft_code"]
        how = f"drafted by {', '.join(agents)}" if drafted else "the device patch, no coding agent"
        code_why = f"{how}; the device tests and verify scripts passed in the sandbox."
        diff = "\n".join(d.diff for d in drafts)
        diff = diff if len(diff) <= WHY_TAIL else diff[:WHY_TAIL] + "…"
        cand_why = (
            f"Coder ({how}): {'; '.join(d.notes for d in drafts)}. Device tests passed.\n"
            f"Diff:\n{diff}\nTest output:\n{_tail(drafts[-1].test_output, 600)}\n"
        )
    code, _ = registry.create_code(
        session,
        tools.store,
        registry.build_tarball(files),
        rev=None,
        actor=ACTOR,
        actor_class="agent",
        why=f"Code from {len(items)} overlay(s) on {base_rev}: {code_why}",
        source=source,
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
        why=f"Firmware v{version} = v{current.version} with code {code.rev} ({how}).",
        source="batch",
        parent_version=current.version,
        fleet_id=current.fleet_id,
    )
    paths = tools.settings.safety_paths
    safety = any(
        code_touches_safety(i.overlay.code_patch, paths)
        or (plan.files is not None and code_touches_safety(d.diff, paths))
        for i, d in zip(items, drafts, strict=True)
    )
    return _new_candidate(
        session,
        items,
        now,
        tools,
        type_="code",
        version=version,
        code_rev=code.rev,
        why=cand_why + " " + " ".join(i.explanation for i in items),
        touches_safety=safety,
        stub_stages=stub_stages,
        data={
            "parent_version": current.version,
            "base_code_rev": base_rev,
            "coder": [
                {
                    "overlay": i.overlay.id,
                    "agent": d.agent,
                    "notes": d.notes,
                    "diff": d.diff,
                    "test_output": d.test_output,
                }
                for i, d in zip(items, drafts, strict=True)
            ],
        },
    )
