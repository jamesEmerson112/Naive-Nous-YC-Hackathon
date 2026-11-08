"""Labelled runs: one heal attempt plus a verdict that only a verifier gives (fleet learning,
`docs/recursive-self-improvement/design.md` §2).

- `LabelledRun`: the frozen view every reader shares (the `/learning` page, the fleet dataset,
  cluster review, later the digest's repeat rate). `services/runs.py` builds them from
  `agent_runs` first, then from incidents with no run (the fake arms).
- `verdict(...)`: the verdict table. The first matching row wins:
  1. `excluded`: a rehearsal (`agent_runs.sim`), a run still going, or hardware (a work order,
     or a park for a person);
  2. `fail`: the bench check failed, the agent left it broken, the lock-in rolled back, the
     device reverted the overlay, the gate rolled back a candidate built from it, or the same
     kind came back on the device inside the window;
  3. `pass`: the bench check passed, the gate promoted a candidate built from it, or the
     overlay held on the device (the mark-good timer);
  4. `unverified`: everything else, including a device that says `fixed` with nothing
     independent behind it. A model never grades its own run (design §0 rule 2).

Pure: standard library only; inputs are plain dicts, never ORM rows; nothing reads a clock.
"""

from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

PASS = "pass"
FAIL = "fail"
UNVERIFIED = "unverified"
EXCLUDED = "excluded"
VERDICTS = (PASS, FAIL, UNVERIFIED, EXCLUDED)
LABELLED = (PASS, FAIL)
"""The verdicts a verifier gave: only these become training rows."""

# Fixes a person or a part makes (kept equal to `domain.datasets.HARDWARE_ACTIONS`; domain
# modules don't import each other).
HARDWARE_ACTIONS = ("park", "torque_off", "replace_part", "order_part", "work_order")
REPEAT_WINDOW_S = 900.0
"""The same fault kind back on the same device this soon after means the fix didn't hold."""

# The table as the dataset card prints it: (verdict, when).
TABLE = (
    (
        EXCLUDED,
        "a rehearsal (`agent_runs.sim`), a run still going, or hardware (work order or park)",
    ),
    (
        FAIL,
        "bench check failed; agent left it broken; lock-in rolled back; device reverted the "
        "overlay; gate rolled back a candidate built from it; same kind back on the device "
        f"within {int(REPEAT_WINDOW_S // 60)} min",
    ),
    (PASS, "bench check passed; gate promoted a candidate built from it; overlay held on device"),
    (UNVERIFIED, "everything else, including a device that says fixed with nothing behind it"),
)


@dataclass(frozen=True)
class LabelledRun:
    id: str  # "run:<device>/<run_id>" or "inc:<incident_id>" (fake-arm fallback)
    device_id: str
    device_class: str
    device_kind: str  # kind "sim" → SIM badge everywhere
    kind: str  # fault kind; a run takes its first linked incident's
    started_at: datetime
    model: str | None
    versions: dict  # {"firmware", "harness", "mcu_build"} when known
    steps: tuple[dict, ...]  # {thought, tool, args, effect, ok}
    verdict: str  # pass | fail | unverified | excluded
    verifier: str  # a sentence: "gate promoted harness v4"
    incident_id: str | None
    run_id: str | None
    # Additions for the export (defaults, so the design's fields stay positional):
    problem: str = ""  # what the agent was asked to fix, in words
    final: str = ""  # the agent's own final report (never the verdict)
    source: str = ""  # "agent_run" | "incident" (fallback)

    @property
    def sim_device(self) -> bool:
        return self.device_kind == "sim"

    def to_dict(self) -> dict[str, Any]:
        """Plain JSON (times as ISO strings, steps as a list): what a snapshot freezes."""
        d = asdict(self)
        d["started_at"] = self.started_at.isoformat()
        d["steps"] = [dict(s) for s in self.steps]
        return d


def _f(value: Any) -> str:
    return str(value).strip() if value not in (None, "") else ""


def _cand_words(c: Mapping[str, Any]) -> str:
    what = _f(c.get("type")) or "candidate"
    v = c.get("version")
    ver = f" v{v}" if v is not None else (f" {c['code_rev']}" if c.get("code_rev") else "")
    return f"{what}{ver} ({c.get('id')})"


def verdict(
    run: Mapping[str, Any] | None,
    incident: Mapping[str, Any] | None = None,
    overlay: Mapping[str, Any] | None = None,
    candidates: Iterable[Mapping[str, Any]] = (),
    *,
    recurred_after_s: float | None = None,
) -> tuple[str, str]:
    """(verdict, verifier sentence) for one heal attempt. First matching row wins.

    - `run`: an agent run as `{sim, state, status, verification, outcome}` (the row's
      columns plus the report's `verification` and `outcome`), or None for a fake arm.
    - `incident`: `{kind, device_id, resolved, resolving_action, suspected_cause,
      work_order}`, or None.
    - `overlay`: `{id, status, status_reason}` of the incident's overlay, or None.
    - `candidates`: `[{id, type, version, status}]` built from that overlay.
    - `recurred_after_s`: seconds until the same kind came back on the device, when it did
      inside `REPEAT_WINDOW_S`.
    """
    run = run or {}
    inc = incident or {}
    ov = overlay or {}
    cands = list(candidates)
    verification = run.get("verification") if isinstance(run.get("verification"), Mapping) else {}
    outcome = run.get("outcome") if isinstance(run.get("outcome"), Mapping) else {}

    # 1. excluded
    if run.get("sim"):
        return EXCLUDED, "rehearsal: a simulated run, not fleet behaviour"
    if run and (run.get("state") == "running" or run.get("status") == "working"):
        return EXCLUDED, "still running"
    if inc.get("work_order"):
        return EXCLUDED, f"hardware: work order {inc['work_order']}"
    action = _f(inc.get("resolving_action")).lower()
    if action in HARDWARE_ACTIONS or inc.get("suspected_cause") == "hardware":
        return EXCLUDED, f"hardware: parked for a person ({action or 'suspected hardware'})"

    # 2. fail
    if verification.get("passed") is False:
        loops = verification.get("final_loops")
        detail = ""
        if isinstance(loops, Mapping):
            detail = _f(loops.get("check") or loops.get("verdict"))
        return FAIL, "bench check failed" + (f": {detail}" if detail else "")
    if run.get("status") == "broken":
        headline = _f(outcome.get("headline"))
        return FAIL, "the agent left it broken" + (f": {headline}" if headline else "")
    if ov.get("status") == "lockin_rolled_back":
        reason = _f(ov.get("status_reason"))
        return FAIL, f"lock-in of overlay {ov.get('id')} rolled back" + (
            f": {reason}" if reason else ""
        )
    if ov.get("status") == "reverted":
        reason = _f(ov.get("status_reason"))
        return FAIL, f"the device reverted overlay {ov.get('id')}" + (
            f": {reason}" if reason else ""
        )
    for c in cands:
        if c.get("status") == "rolled_back":
            return FAIL, f"gate rolled back {_cand_words(c)}"
    if recurred_after_s is not None and recurred_after_s <= REPEAT_WINDOW_S:
        mins = max(1, round(recurred_after_s / 60))
        where = f" on {inc['device_id']}" if inc.get("device_id") else ""
        return FAIL, f"{inc.get('kind') or 'the fault'} came back{where} {mins} min later"

    # 3. pass
    if verification.get("passed") is True:
        return PASS, "bench check passed (score.json)"
    for c in cands:
        if c.get("status") == "promoted":
            return PASS, f"gate promoted {_cand_words(c)}"
    if ov.get("status") == "held":
        return PASS, f"overlay {ov.get('id')} held on device (mark-good timer)"

    # 4. unverified
    if run.get("status") == "fixed":
        return UNVERIFIED, "device says fixed; nothing checked it"
    for c in cands:
        return UNVERIFIED, f"locked into {_cand_words(c)}; the gate hasn't decided"
    if inc.get("resolved"):
        return UNVERIFIED, "device says resolved; nothing checked it"
    return UNVERIFIED, "nothing checked it"


def counts(runs: Iterable[LabelledRun]) -> dict[str, int]:
    """{verdict: n} over all four verdicts (zeros included)."""
    out = dict.fromkeys(VERDICTS, 0)
    for r in runs:
        out[r.verdict] = out.get(r.verdict, 0) + 1
    return out
