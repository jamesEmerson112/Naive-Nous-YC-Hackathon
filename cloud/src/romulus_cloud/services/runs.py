"""Labelled runs from the database, and the standing fleet dataset (fleet learning, design
§2–3).

- `load(session, ...)`: every heal attempt as a `domain.runs.LabelledRun`. Agent runs first
  (`agent_runs`, the real pod's reports, APIs §3.11); then each incident no run worked on,
  from `Incident.agent` (the fake arms send no runs, `cloud/CLAUDE.md` "Real device data").
  The verdict joins the incident's overlay, the candidates built from it and its work order.
- `ensure_standing_dataset(session, now, settings)`: plain code, no model. Makes sure the
  `fleet-runs` dataset exists, and when labelled `pass`/`fail` runs have grown by
  LEARNING_MIN_NEW since its last snapshot, snapshots it and asks for a `trajectories`
  export. Every write is actor `agent:fleet-learning`, class `agent`.

Nothing here commits; the worker step does.
"""

from collections import defaultdict
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from romulus_cloud.domain import runs as dr
from romulus_cloud.domain.runs import LabelledRun
from romulus_cloud.models import (
    AgentRun,
    Candidate,
    Dataset,
    DatasetVersion,
    Device,
    Incident,
    Overlay,
    WorkOrder,
)
from romulus_cloud.settings import Settings

ACTOR = "agent:fleet-learning"
ACTOR_CLASS = "agent"
DATASET_NAME = "fleet-runs"
EXPORT_FORMAT = "trajectories"


# --- loading ---------------------------------------------------------------------------------


def _step(s: dict[str, Any]) -> dict[str, Any]:
    args = s.get("args")
    return {
        "thought": str(s.get("thought") or ""),
        "tool": str(s.get("tool") or "unknown"),
        "args": dict(args) if isinstance(args, dict) else {},
        "effect": str(s.get("effect") or ""),
        "ok": bool(s.get("ok")),
    }


def _fallback_steps(inc: Incident) -> tuple[dict[str, Any], ...]:
    """A fake arm's `Incident.agent`: its reasoning, then one step per action it took."""
    actions = [a for a in (inc.actions or []) if a not in (None, "")]
    reasoning = (inc.agent_reasoning or "").strip()
    steps = []
    for i, action in enumerate(actions):
        steps.append(
            {
                "thought": reasoning if i == 0 else "",
                "tool": "device_action",
                "args": {"action": action if isinstance(action, str) else str(action)},
                "effect": "",
                "ok": True,
            }
        )
    return tuple(steps)


def _versions(inc: Incident | None, dev: Device | None) -> dict[str, Any]:
    running = (inc.running if inc else None) or {}
    out = {
        "firmware": running.get("firmware", dev.running_firmware if dev else None),
        "harness": running.get("harness", dev.running_harness if dev else None),
        "mcu_build": running.get("mcu_build") or (dev.running_mcu_build if dev else None),
    }
    return {k: v for k, v in out.items() if v is not None}


def _recurred(inc: Incident, by_kind: dict, skip: set[str]) -> float | None:
    """Seconds until the same kind came back on the same device, if it did inside the window."""
    for other in by_kind.get((inc.device_id, inc.kind), []):
        if other.id in skip or other.id == inc.id:
            continue
        gap = (other.occurred_at - inc.occurred_at).total_seconds()
        if 0 < gap <= dr.REPEAT_WINDOW_S:
            return gap
    return None


def load(
    session: Session,
    *,
    now: datetime | None = None,
    fleet_id: str = "default",
    incident_ids: set[str] | None = None,
) -> list[LabelledRun]:
    """Every labelled run in the fleet, oldest first. With `incident_ids`, only the runs
    whose (first) incident is one of them. `now` is unused: the verdict reads no clock.

    An incident that an agent run worked on yields one labelled run, from the run; an
    incident no run names yields one from `Incident.agent`.
    """
    devices = {d.id: d for d in session.scalars(select(Device).where(Device.fleet_id == fleet_id))}
    incidents = {
        i.id: i for i in session.scalars(select(Incident).where(Incident.fleet_id == fleet_id))
    }
    overlays = {
        (o.device_id, o.id): o
        for o in session.scalars(select(Overlay).where(Overlay.fleet_id == fleet_id))
    }
    work_orders = dict(
        session.execute(
            select(WorkOrder.incident_id, WorkOrder.id).where(WorkOrder.fleet_id == fleet_id)
        ).all()
    )
    cands_by_overlay: dict[tuple[str, str], list[Candidate]] = defaultdict(list)
    cands = {
        c.id: c for c in session.scalars(select(Candidate).where(Candidate.fleet_id == fleet_id))
    }
    for c in cands.values():
        for ref in c.from_overlays or []:
            if isinstance(ref, dict):
                cands_by_overlay[(ref.get("device_id"), ref.get("overlay_id"))].append(c)
    by_kind: dict[tuple[str, str], list[Incident]] = defaultdict(list)
    for inc in sorted(incidents.values(), key=lambda i: (i.occurred_at, i.id)):
        by_kind[(inc.device_id, inc.kind)].append(inc)

    def facts(inc: Incident | None) -> tuple[dict | None, dict | None, list[dict]]:
        if inc is None:
            return None, None, []
        ov = overlays.get((inc.device_id, inc.overlay_id)) if inc.overlay_id else None
        incident = {
            "kind": inc.kind,
            "device_id": inc.device_id,
            "resolved": inc.resolved,
            "resolving_action": inc.resolving_action,
            "suspected_cause": inc.suspected_cause,
            "work_order": work_orders.get(inc.id),
        }
        if ov is None:
            return incident, None, []
        overlay = {"id": ov.id, "status": ov.status, "status_reason": ov.status_reason}
        built = list(cands_by_overlay.get((ov.device_id, ov.id), []))
        if ov.candidate_id and ov.candidate_id in cands and cands[ov.candidate_id] not in built:
            built.append(cands[ov.candidate_id])
        candidates = [
            {
                "id": c.id,
                "type": c.type,
                "version": c.version,
                "code_rev": c.code_rev,
                "status": c.status,
            }
            for c in sorted(built, key=lambda c: c.id)
        ]
        return incident, overlay, candidates

    out: list[LabelledRun] = []
    covered: set[str] = set()
    rows = session.scalars(
        select(AgentRun).where(AgentRun.fleet_id == fleet_id).order_by(AgentRun.started_at)
    )
    for row in rows:
        ids = [str(i) for i in row.incident_ids or []]
        covered.update(ids)
        first = ids[0] if ids else None
        if incident_ids is not None and first not in incident_ids:
            continue
        report = row.report or {}
        inc = incidents.get(first) if first else None
        dev = devices.get(row.device_id)
        incident, overlay, candidates = facts(inc)
        problem = report.get("problem") if isinstance(report.get("problem"), dict) else {}
        kinds = [str(i.get("kind")) for i in problem.get("incidents") or [] if isinstance(i, dict)]
        outcome = report.get("outcome") if isinstance(report.get("outcome"), dict) else {}
        run = {
            "sim": row.sim,
            "state": row.state,
            "status": row.status,
            "verification": report.get("verification"),
            "outcome": outcome,
        }
        recurred = _recurred(inc, by_kind, set(ids)) if inc is not None else None
        verdict, verifier = dr.verdict(
            run, incident, overlay, candidates, recurred_after_s=recurred
        )
        writeup = report.get("writeup") if isinstance(report.get("writeup"), dict) else {}
        agent = report.get("agent") if isinstance(report.get("agent"), dict) else {}
        out.append(
            LabelledRun(
                id=f"run:{row.device_id}/{row.run_id}",
                device_id=row.device_id,
                device_class=dev.device_class if dev else "unknown",
                device_kind=dev.kind if dev else "unknown",
                kind=inc.kind if inc else (kinds[0] if kinds else "unknown"),
                started_at=row.started_at,
                model=agent.get("model"),
                versions=_versions(inc, dev),
                steps=tuple(_step(s) for s in report.get("steps") or [] if isinstance(s, dict)),
                verdict=verdict,
                verifier=verifier,
                incident_id=first,
                run_id=row.run_id,
                problem=_problem_text(inc, problem, kinds, row.device_id),
                final=str(writeup.get("report") or ""),
                source="agent_run",
            )
        )

    for inc in sorted(incidents.values(), key=lambda i: (i.occurred_at, i.id)):
        if inc.id in covered or (incident_ids is not None and inc.id not in incident_ids):
            continue
        dev = devices.get(inc.device_id)
        incident, overlay, candidates = facts(inc)
        verdict, verifier = dr.verdict(
            None, incident, overlay, candidates, recurred_after_s=_recurred(inc, by_kind, set())
        )
        agent = (
            (inc.raw or {}).get("agent") if isinstance((inc.raw or {}).get("agent"), dict) else {}
        )
        out.append(
            LabelledRun(
                id=f"inc:{inc.id}",
                device_id=inc.device_id,
                device_class=dev.device_class if dev else "unknown",
                device_kind=dev.kind if dev else "unknown",
                kind=inc.kind,
                started_at=inc.occurred_at,
                model=agent.get("model"),
                versions=_versions(inc, dev),
                steps=_fallback_steps(inc),
                verdict=verdict,
                verifier=verifier,
                incident_id=inc.id,
                run_id=None,
                problem=_problem_text(inc, {}, [], inc.device_id),
                final=(inc.agent_reasoning or "").strip(),
                source="incident",
            )
        )
    return sorted(out, key=lambda r: (r.started_at, r.id))


def _problem_text(inc: Incident | None, problem: dict, kinds: list[str], device_id: str) -> str:
    if inc is not None:
        what = f"{inc.kind}: {inc.summary}" if inc.summary else inc.kind
    elif problem.get("headline") or problem.get("summary"):
        what = str(problem.get("headline") or problem.get("summary"))
    else:
        what = ", ".join(kinds) or "a fault"
    return f"{device_id} reported {what}. Find the cause and fix it."


# --- the standing dataset ----------------------------------------------------------------------


def _labelled_ids(runs: list[LabelledRun]) -> set[tuple[str, str]]:
    """(run id, verdict) of every pass/fail run a snapshot can hold (one tied to an incident)."""
    return {(r.id, r.verdict) for r in runs if r.verdict in dr.LABELLED and r.incident_id}


def _frozen_labelled(manifest: dict[str, Any]) -> set[tuple[str, str]]:
    out = set()
    for ctx in (manifest.get("context") or {}).values():
        for r in ctx.get("labelled_runs") or []:
            if r.get("verdict") in dr.LABELLED:
                out.add((r["id"], r["verdict"]))
    return out


def ensure_standing_dataset(
    session: Session, now: datetime, settings: Settings
) -> dict[str, Any] | None:
    """Keep `fleet-runs` current (design §3). Returns what it did, e.g.
    `{"dataset_id", "created", "snapshot", "export_id", "new", "counts"}`; None when
    LEARNING_MODE is off."""
    if settings.learning_mode == "off":
        return None
    from romulus_cloud.services import datasets  # datasets imports this module lazily too

    fleet = settings.fleet_id
    ds = session.scalar(
        select(Dataset)
        .where(Dataset.fleet_id == fleet, Dataset.name == DATASET_NAME)
        .order_by(Dataset.created_at)
        .limit(1)
    )
    created = ds is None
    if ds is None:
        ds = datasets.create_dataset(
            session,
            name=DATASET_NAME,
            purpose="harness",
            filter_={},
            actor=ACTOR,
            actor_class=ACTOR_CLASS,
            fleet_id=fleet,
            now=now,
        )
    runs = load(session, now=now, fleet_id=fleet)
    live = _labelled_ids(runs)
    last_n = session.scalar(
        select(func.max(DatasetVersion.n)).where(DatasetVersion.dataset_id == ds.id)
    )
    frozen: set[tuple[str, str]] = set()
    if last_n:
        frozen = _frozen_labelled(
            datasets.frozen_manifest(session, datasets.get_version(session, ds.id, last_n))
        )
    new = len(live - frozen)
    summary: dict[str, Any] = {
        "dataset_id": ds.id,
        "created": created,
        "snapshot": None,
        "export_id": None,
        "new": new,
        "counts": dr.counts(runs),
    }
    if new < max(1, settings.learning_min_new):
        return summary
    version = datasets.snapshot(session, ds.id, actor=ACTOR, actor_class=ACTOR_CLASS, now=now)
    exp = datasets.request_export(
        session,
        ds.id,
        version.n,
        EXPORT_FORMAT,
        redact=True,
        actor=ACTOR,
        actor_class=ACTOR_CLASS,
        now=now,
    )
    summary |= {"snapshot": version.n, "export_id": exp.id}
    return summary
