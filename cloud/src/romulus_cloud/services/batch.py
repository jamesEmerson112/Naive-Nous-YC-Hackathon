"""The batch job: lock held overlays in as candidates (v0 plan §7.3, D0, BUILD §3).

`maybe_run(session, now)` runs when `BATCH_INTERVAL_S` has passed since the last run, or
someone asked (`fleet_state.batch_requested`). For each held overlay not yet locked in it
dispatches on what the overlay carries (`lock_in = by_patch_kind(rules=merge_rules,
harness=write_skill, code=draft_code)`):

- **rules / params:** `base_compatible` against the class's current manifest, then the
  deterministic `apply_overlay` → a new firmware version → a `firmware` candidate.
- **harness:** for every overlay with an incident, the LLM writes a skill (FakeLlm by
  default) into a new harness version: the current harness tarball plus
  `<knowledge_dir>/<name>.md`, with `harness.json`'s version bumped → a `harness` candidate.
- **code:** `draft_code_v0`, the FakeCoder pass-through: the package is the base code plus
  the device's patch file (not applied), and a new firmware manifest names it → a `code`
  candidate, which waits at `awaiting_canary` (B7) unless the setting allows it.

The LLM writes the candidate's `why` and the skill, never merged config. On `LlmError` the
batch falls back to templated text and says so. The overlay becomes `locked_in` (or
`dropped`, with the reason), and every event carries `incident` + `candidate`, so the
incident's story shows lock-in → gate → outcome. Nothing here commits.
"""

import io
import json
import tarfile
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from romulus_cloud import events
from romulus_cloud.adapters.blobstore import BlobStore, build_blobstore
from romulus_cloud.adapters.coder import Coder, build_coder
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
            coder=build_coder(settings.coder_provider, settings.coder_agent, settings.coder_image),
            store=build_blobstore(settings.blob_dir),
        )


@dataclass
class RunResult:
    ran: bool
    candidates: list[Candidate] = field(default_factory=list)
    dropped: list[tuple[str, str]] = field(default_factory=list)  # (device_id, overlay_id)


def is_due(session: Session, now: datetime, interval_s: float, fleet_id: str) -> bool:
    row = fleet.state(session, fleet_id)
    if row.batch_requested or row.batch_last_run_at is None:
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


def run(session: Session, now: datetime, tools: Tools) -> RunResult:
    fleet_id = tools.settings.fleet_id
    row = fleet.state(session, fleet_id)
    requested = row.batch_requested
    row.batch_requested, row.batch_last_run_at = False, now
    result = RunResult(ran=True)
    overlays = held_overlays(session, fleet_id)
    for overlay in overlays:
        made = _lock_in(session, overlay, now, tools)
        if made:
            result.candidates.extend(made)
        else:
            result.dropped.append((overlay.device_id, overlay.id))
    if overlays or requested:
        # A timer run with nothing to do writes nothing, so the overnight log stays readable.
        why = (
            f"Batch ran ({'requested' if requested else 'timer'}): "
            f"{len(overlays)} held overlay(s), {len(result.candidates)} candidate(s) created, "
            f"{len(result.dropped)} overlay(s) dropped."
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
                "dropped": [list(d) for d in result.dropped],
            },
            ts=now,
            fleet_id=fleet_id,
        )
    session.flush()
    return result


# --- one overlay ------------------------------------------------------------------------


@dataclass
class _Ctx:
    """One overlay's lock-in: what it is, the class's current versions, the LLM's prose."""

    overlay: Overlay
    incident: Incident
    device_class: str
    fleet_id: str
    current_fw: FirmwareVersion
    explanation: str
    llm_note: str  # "" or why the templated fallback was used
    notes: list[str] = field(default_factory=list)


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


def _refs(ctx: _Ctx, candidate: Candidate | None = None) -> dict[str, Any]:
    refs: dict[str, Any] = {
        "incident": ctx.incident.id,
        "device": ctx.overlay.device_id,
        "overlay": ctx.overlay.id,
    }
    if candidate is not None:
        refs["candidate"] = candidate.id
        if candidate.touches_safety:
            refs["touches_safety"] = True
    return refs


def _current_firmware(session: Session, device_class: str, fleet_id: str) -> FirmwareVersion:
    """The manifest most of the class is assigned (what promotion would replace)."""
    version, _ = registry.default_assignment(session, device_class, fleet_id)
    if version is None:
        raise ApiError(409, "no_firmware", f"No firmware is assigned to class {device_class}.")
    return registry.get_firmware(session, version, fleet_id)


def _next_firmware(session: Session, fleet_id: str) -> int:
    top = session.scalar(
        select(func.max(FirmwareVersion.version)).where(FirmwareVersion.fleet_id == fleet_id)
    )
    return (top or 0) + 1


def _next_harness(session: Session, fleet_id: str) -> int:
    top = session.scalar(
        select(func.max(HarnessVersion.version)).where(HarnessVersion.fleet_id == fleet_id)
    )
    return (top or 0) + 1


def _lock_in(session: Session, overlay: Overlay, now: datetime, tools: Tools) -> list[Candidate]:
    incident = session.get(Incident, overlay.incident_id)
    device = session.get(Device, overlay.device_id)
    assert incident is not None and device is not None
    device_class = device.device_class or "so101"
    doc = _incident_doc(incident, overlay)
    explanation, llm_note = _llm_text(tools.llm, "explain", doc)
    ctx = _Ctx(
        overlay=overlay,
        incident=incident,
        device_class=device_class,
        fleet_id=overlay.fleet_id,
        current_fw=_current_firmware(session, device_class, overlay.fleet_id),
        explanation=explanation,
        llm_note=llm_note,
    )

    kinds = classify_patch({"diff": overlay.diff, "code_patch": overlay.code_patch})
    made: list[Candidate] = []
    if kinds & CONFIG_KINDS:
        cand = _merge_rules(session, ctx, now, tools)
        if cand is None:
            return []  # dropped: a lost update or a bad merge; the event says which
        made.append(cand)
    if "code" in kinds:
        made.append(_draft_code(session, ctx, now, tools))
    for kind in sorted(kinds - CONFIG_KINDS - {"code"}):
        ctx.notes.append(f"no lock-in stage for {kind!r}; that part of the overlay is left out")
    made.append(_write_skill(session, ctx, doc, now, tools))

    primary = made[0]
    overlay.status = "locked_in"
    overlay.status_reason = f"locked into {', '.join(label(c) for c in made)}"
    overlay.candidate_id = primary.id
    overlay.updated_at = now
    notes = "".join(f" Note: {n}." for n in ctx.notes)
    events.record(
        session,
        ACTOR,
        "agent",
        "overlay.locked_in",
        _refs(ctx, primary),
        f"Overlay {overlay.id} from {overlay.device_id} was locked in as "
        f"{', '.join(label(c) for c in made)}; the gate now replays and canaries "
        f"{'them' if len(made) > 1 else 'it'} before the fleet gets anything.{notes}",
        data={"candidates": [c.id for c in made], "labels": [label(c) for c in made]},
        ts=now,
        fleet_id=ctx.fleet_id,
    )
    return made


def _drop(session: Session, ctx: _Ctx, why: str, now: datetime, data: dict[str, Any]) -> None:
    ov = ctx.overlay
    ov.status, ov.status_reason, ov.updated_at = "dropped", why, now
    refs = _refs(ctx)
    if ov.touches_safety:
        refs["touches_safety"] = True
    events.record(
        session,
        ACTOR,
        "agent",
        "overlay.dropped",
        refs,
        f"Overlay {ov.id} from {ov.device_id} was not locked in: {why}.",
        data=data,
        ts=now,
        fleet_id=ctx.fleet_id,
    )


def _new_candidate(
    session: Session,
    ctx: _Ctx,
    now: datetime,
    *,
    type_: str,
    version: int,
    code_rev: str | None,
    why: str,
    touches_safety: bool,
    stub_stages: list[str],
    llm_model: str,
    data: dict[str, Any],
) -> Candidate:
    cand = Candidate(
        id=new_id("cand"),
        fleet_id=ctx.fleet_id,
        type=type_,
        version=version,
        code_rev=code_rev,
        device_class=ctx.device_class,
        from_overlays=[{"device_id": ctx.overlay.device_id, "overlay_id": ctx.overlay.id}],
        status="replaying",
        why=why,
        touches_safety=touches_safety,
        stub_stages=stub_stages,
        incident_id=ctx.incident.id,
        created_at=now,
        updated_at=now,
    )
    session.add(cand)
    session.flush()
    llm = f" {ctx.llm_note}." if ctx.llm_note else ""
    safety = (
        " It touches the safety layer or visor: simulated canary only, under the kill switch (K17)."
        if touches_safety
        else ""
    )
    stubs = f" Stub stages: {', '.join(stub_stages)}." if stub_stages else ""
    events.record(
        session,
        ACTOR,
        "agent",
        "candidate.created",
        _refs(ctx, cand),
        f"Candidate {label(cand)} from overlay {ctx.overlay.id}: {why}{safety}{stubs}{llm}",
        data={
            "type": type_,
            "version": version,
            "code_rev": code_rev,
            "llm_model": llm_model,
            "llm_fallback": bool(ctx.llm_note),
            "stub_stages": stub_stages,
            **data,
        },
        ts=now,
        fleet_id=ctx.fleet_id,
    )
    return cand


def _merge_rules(session: Session, ctx: _Ctx, now: datetime, tools: Tools) -> Candidate | None:
    """rules/params: the deterministic merge (D3). No LLM touches the manifest."""
    ov, current = ctx.overlay, ctx.current_fw
    base = (
        session.get(FirmwareVersion, (ctx.fleet_id, ov.base_firmware)) if ov.base_firmware else None
    )
    diff = {k: v for k, v in (ov.diff or {}).items() if k in CONFIG_KINDS}
    compat = base_compatible(base.manifest if base else None, current.manifest, diff)
    if not compat:
        _drop(session, ctx, compat.why, now, {"conflicts": list(compat.conflicts)})
        return None
    version = _next_firmware(session, ctx.fleet_id)
    try:
        merged = apply_overlay(current.manifest, diff, version=version)
    except MergeError as exc:
        _drop(session, ctx, str(exc), now, {"problems": exc.problems})
        return None
    safety_names = tools.settings.safety_paths
    safety = ov.touches_safety or config_touches_safety(
        merged, safety_rules=safety_names, safety_params=safety_names
    )
    registry.create_firmware(
        session,
        merged.manifest,
        actor=ACTOR,
        actor_class="agent",
        why=f"Firmware v{version} = v{current.version} + overlay {ov.id} ({merged.why}); "
        "merged deterministically, no LLM.",
        source="batch",
        parent_version=current.version,
        fleet_id=ctx.fleet_id,
    )
    return _new_candidate(
        session,
        ctx,
        now,
        type_="firmware",
        version=version,
        code_rev=None,
        why=f"v{current.version} + overlay {ov.id}: {merged.why}. {ctx.explanation}",
        touches_safety=safety,
        stub_stages=[],
        llm_model=tools.llm.model,
        data={
            "parent_version": current.version,
            "added_rules": list(merged.added_rules),
            "changed_rules": list(merged.changed_rules),
            "removed_rules": list(merged.removed_rules),
            "changed_params": list(merged.changed_params),
        },
    )


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


def _write_skill(
    session: Session, ctx: _Ctx, doc: dict[str, Any], now: datetime, tools: Tools
) -> Candidate:
    """harness: the LLM writes a markdown skill into a new harness version (D22)."""
    _, current_v = registry.default_assignment(session, ctx.device_class, ctx.fleet_id)
    current = registry.get_harness(session, current_v, ctx.fleet_id)  # type: ignore[arg-type]
    skill, note = _llm_text(tools.llm, "write_skill", doc)
    files = _read_tarball(tools.store, current.sha256)
    meta = json.loads(files.get("harness.json", b"{}"))
    version = _next_harness(session, ctx.fleet_id)
    knowledge_dir = (meta.get("knowledge_dir") or "skills/").rstrip("/") + "/"
    name = _skill_name(ctx.incident)
    files[knowledge_dir + name] = skill.encode()
    files["harness.json"] = json.dumps({**meta, "version": version}, indent=2).encode() + b"\n"
    registry.create_harness(
        session,
        tools.store,
        registry.build_tarball(files),
        actor=ACTOR,
        actor_class="agent",
        why=f"Harness v{version} = v{current.version} + skill {knowledge_dir}{name}, "
        f"written by {tools.llm.model} from incident {ctx.incident.id}.",
        source="batch",
        parent_version=current.version,
        fleet_id=ctx.fleet_id,
    )
    if note and not ctx.llm_note:
        ctx.llm_note = note
    return _new_candidate(
        session,
        ctx,
        now,
        type_="harness",
        version=version,
        code_rev=None,
        why=f"v{current.version} + skill {knowledge_dir}{name} on handling {ctx.incident.kind}. "
        f"{ctx.explanation}",
        touches_safety=False,
        stub_stages=[],
        llm_model=tools.llm.model,
        data={"parent_version": current.version, "skill": name},
    )


def _draft_code(session: Session, ctx: _Ctx, now: datetime, tools: Tools) -> Candidate:
    """code: `draft_code_v0`, the FakeCoder pass-through (BUILD §3). Never promotes by default."""
    ov, current = ctx.overlay, ctx.current_fw
    base_rev = ov.base_code_rev or current.manifest.get("code_rev")
    draft = tools.coder.draft(ov.code_patch or "", {"id": ctx.incident.id}, base_rev)
    base = session.get(CodeVersion, (ctx.fleet_id, base_rev))
    files = _read_tarball(tools.store, base.sha256) if base else {}
    files[f"romulus/patches/{ov.id}.diff"] = draft.diff.encode()
    data = registry.build_tarball(files)
    code, _ = registry.create_code(
        session,
        tools.store,
        data,
        rev=None,
        actor=ACTOR,
        actor_class="agent",
        why=f"Code from overlay {ov.id} on {base_rev}: {PASSTHROUGH_NOTE}; the package is the "
        "base code plus the device's patch file, not applied.",
        source="passthrough",
        parent_rev=base_rev,
        fleet_id=ctx.fleet_id,
    )
    version = _next_firmware(session, ctx.fleet_id)
    manifest = {**current.manifest, "version": version, "code_rev": code.rev}
    registry.create_firmware(
        session,
        manifest,
        actor=ACTOR,
        actor_class="agent",
        why=f"Firmware v{version} = v{current.version} with code {code.rev} ({PASSTHROUGH_NOTE}).",
        source="batch",
        parent_version=current.version,
        fleet_id=ctx.fleet_id,
    )
    safety = code_touches_safety(ov.code_patch, tools.settings.safety_paths)
    return _new_candidate(
        session,
        ctx,
        now,
        type_="code",
        version=version,
        code_rev=code.rev,
        why=f"{PASSTHROUGH_NOTE}: the device's own patch from overlay {ov.id}, unchanged "
        f"({draft.notes}). {ctx.explanation}",
        touches_safety=safety,
        stub_stages=["draft_code", "replay"],
        llm_model=tools.llm.model,
        data={"parent_version": current.version},
    )
