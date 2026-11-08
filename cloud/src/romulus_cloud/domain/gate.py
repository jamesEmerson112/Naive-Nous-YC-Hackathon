"""The gate's pure decisions: replay, canary verdict, candidate state machine (v0 plan §7.4, D6).

Every decision the gate makes is a function here; `services/gate.py` only loads inputs,
calls these, writes the result and its Event. Nothing here touches the DB, the network or
the clock: `now` is always passed in.

- `replay(manifest, cases)`: the candidate's rules must fire each case's expected action
  (APIs §2.7, `romulus_contracts.rules.replay_report`).
- `replay_stub(reason)` / `replay_code_stub()`: a stub replay that passes and says so.
- `canary_verdict(observations, started_at, window_s, now)`: pending, pass or fail.
- `next_state(state, inputs, now)`: one step of the candidate state machine.
- `Stage`, `compose` / `>>`, `ID`, `passthrough`, `by_kind`: stages as composable functions
  (BUILD §3: `gate = replay >> canary >> promote_or_rollback`).

Pure: imports only `romulus_contracts` and the standard library.
"""

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Literal, NamedTuple

from romulus_contracts import FirmwareManifest, RulesError, replay_report
from romulus_contracts.rules import Skipped

# --------------------------------------------------------------------------- replay


@dataclass(frozen=True)
class ReplayCase:
    """One past incident to replay: its signals and the action that fixed it (telemetry §4)."""

    case_id: str
    signals: Mapping[str, Any]
    expected_action: str | None
    kind: str = ""


@dataclass(frozen=True)
class CaseResult:
    """How one case went. `fired` lists `rule->action`; `skipped` the samples evaluate ignored."""

    case_id: str
    passed: bool
    why: str
    fired: tuple[str, ...] = ()
    skipped: tuple[Skipped, ...] = ()


@dataclass(frozen=True)
class ReplayResult:
    """The replay stage's outcome. `stub` is True when nothing was actually checked."""

    passed: bool
    why: str
    cases: tuple[CaseResult, ...] = ()
    stub: bool = False

    def to_json(self) -> dict[str, Any]:
        """The shape stored in `candidates.replay` (cases checked and passed)."""
        return {
            "passed": self.passed,
            "why": self.why,
            "stub": self.stub,
            "checked": len(self.cases),
            "passed_count": sum(c.passed for c in self.cases),
            "cases": [
                {"case_id": c.case_id, "passed": c.passed, "why": c.why, "fired": list(c.fired)}
                for c in self.cases
            ],
        }


def _ignored(skipped: Iterable[Skipped]) -> str:
    counts: dict[tuple[str, str], int] = {}
    for s in skipped:
        counts[(s.signal, s.reason)] = counts.get((s.signal, s.reason), 0) + 1
    return ", ".join(f"{signal}: {n} {reason}" for (signal, reason), n in counts.items())


def _replay_case(rules: Mapping[str, Any], case: ReplayCase) -> CaseResult:
    try:
        report = replay_report(rules, case.signals, case.expected_action)
    except TypeError as exc:  # signals is not a mapping
        return CaseResult(case.case_id, False, f"replay failed: {exc}")
    why = report.why()
    # On a pass, `why()` leaves out ignored samples. Mention the bad ones (a flaky sensor,
    # D2), but not signals the incident simply doesn't carry: other rules' signals.
    bad = [s for s in report.skipped if s.reason not in ("missing", "empty")]
    if report.passed and bad:
        why += "; ignored " + _ignored(bad)
    return CaseResult(
        case_id=case.case_id,
        passed=report.passed,
        why=why,
        fired=tuple(f"{f.rule}->{f.action}" for f in report.fired),
        skipped=report.skipped,
    )


def replay(
    manifest: Mapping[str, Any] | FirmwareManifest, cases: Iterable[ReplayCase]
) -> ReplayResult:
    """Replay every case against the candidate's rules (v0 plan §7.4, APIs §2.7).

    Passes only when there is at least one case and every case passes: a candidate must at
    least replay its own incident (P0), then the regression corpus (P1). Malformed rules
    fail every case rather than raising.
    """
    rules = (
        manifest.rules if isinstance(manifest, FirmwareManifest) else manifest.get("rules") or {}
    )
    cases = list(cases)
    if not cases:
        return ReplayResult(False, "replay failed: no incidents to replay (need at least one)")
    try:
        results = tuple(_replay_case(rules, case) for case in cases)
    except RulesError as exc:
        return ReplayResult(False, f"replay failed: candidate rules are malformed ({exc})")
    failed = [r for r in results if not r.passed]
    n = len(results)
    noun = "incident" if n == 1 else "incidents"
    if not failed:
        why = f"replayed {n} {noun}; {n} pass"
        if n == 1:
            why += f" ({results[0].why})"
        return ReplayResult(True, why, results)
    detail = "; ".join(f"{r.case_id}: {r.why}" for r in failed)
    return ReplayResult(
        False,
        f"replayed {n} {noun}; {n - len(failed)} pass, {len(failed)} fail ({detail})",
        results,
    )


def replay_stub(reason: str) -> ReplayResult:
    """A replay that checks nothing, passes, and says so (BUILD §2 rule 8)."""
    return ReplayResult(True, f"replay: stub, passed by default; {reason}", stub=True)


def replay_code_stub() -> ReplayResult:
    """Replay for code candidates until the device tests run in the sandbox (M8, B1, B8)."""
    return replay_stub("device tests not wired (M8)")


# --------------------------------------------------------------------------- canary


@dataclass(frozen=True)
class CheckinObs:
    """A canary check-in (APIs §2.3): `health` is ok, degraded or failing."""

    at: datetime
    health: str


@dataclass(frozen=True)
class InstallObs:
    """A canary install report (APIs §2.4) for the candidate's version."""

    at: datetime
    result: str  # installed | rolled_back | hash_mismatch | invalid
    detail: str = ""


@dataclass(frozen=True)
class IncidentObs:
    """An incident the canary reported."""

    at: datetime
    kind: str


CanaryObs = CheckinObs | InstallObs | IncidentObs

INSTALL_FAILURES = frozenset({"rolled_back", "hash_mismatch", "invalid"})
"""Install results that fail a canary at once (`invalid` is the contracts' proposed value)."""

DEFAULT_MIN_CHECKINS = 2
"""Healthy check-ins a canary needs in its window, so one lucky report can't pass it
*(inferred; D6 leaves "healthy for N minutes" to settle with the device side)*."""


@dataclass(frozen=True)
class Verdict:
    status: Literal["pending", "pass", "fail"]
    why: str


def _ts(at: datetime, started_at: datetime) -> str:
    return f"{(at - started_at).total_seconds():g} s after the canary started"


def canary_verdict(
    observations: Iterable[CanaryObs],
    started_at: datetime,
    window_s: float,
    now: datetime,
    *,
    repeat_kinds: Iterable[str] = (),
    min_checkins: int = DEFAULT_MIN_CHECKINS,
) -> Verdict:
    """Pending, pass or fail for one canary (v0 plan §7.4, D6, D7).

    Pass the observations from the canary device only, and installs for this candidate
    only. Order doesn't matter. Observations before `started_at` are ignored (they belong
    to the previous version).

    1. **Fail fast**, at any time after `started_at` (even after the window ends):
       a check-in with `health: failing`, an install result in `INSTALL_FAILURES`, or an
       incident whose kind is in `repeat_kinds` (the kinds the candidate was meant to fix).
       The earliest such observation is the reason.
    2. **Pending** while `now < started_at + window_s`.
    3. **At or after the window end**, "healthy enough" means all of:
       an `installed` report; at least `min_checkins` check-ins with `health: ok` inside
       the window; and the last check-in inside the window is `ok`. A `degraded` check-in
       doesn't fail fast (it may be transient) but blocks a pass unless a later `ok`
       follows it. Otherwise the verdict is fail, with the reason.
    Incidents of other kinds don't fail the canary; the pass reason mentions them.
    """
    if window_s < 0:
        raise ValueError(f"window_s must be >= 0, got {window_s}")
    repeat = set(repeat_kinds)
    obs = sorted((o for o in observations if o.at >= started_at), key=lambda o: o.at)
    end = started_at + timedelta(seconds=window_s)

    for o in obs:
        when = _ts(o.at, started_at)
        if isinstance(o, CheckinObs) and o.health == "failing":
            return Verdict("fail", f"canary reported health failing {when}")
        if isinstance(o, InstallObs) and o.result in INSTALL_FAILURES:
            detail = f" ({o.detail})" if o.detail else ""
            return Verdict("fail", f"canary install {o.result} {when}{detail}")
        if isinstance(o, IncidentObs) and o.kind in repeat:
            return Verdict("fail", f"canary repeated {o.kind} {when}")

    if now < end:
        left = (end - now).total_seconds()
        return Verdict("pending", f"canary window open; {left:g} s of {window_s:g} s left")

    in_window = [o for o in obs if o.at <= end]
    checkins = [o for o in in_window if isinstance(o, CheckinObs)]
    ok = [c for c in checkins if c.health == "ok"]
    if not any(isinstance(o, InstallObs) and o.result == "installed" for o in in_window):
        return Verdict("fail", f"no successful install reported within the {window_s:g} s window")
    if len(ok) < min_checkins:
        return Verdict(
            "fail",
            f"only {len(ok)} healthy check-ins in the {window_s:g} s window; need {min_checkins}",
        )
    if checkins and checkins[-1].health != "ok":
        return Verdict("fail", f"last check-in in the window was {checkins[-1].health}")
    why = f"canary healthy for {window_s:g} s: installed, {len(ok)} ok check-ins"
    degraded = len(checkins) - len(ok)
    if degraded:
        why += f", {degraded} non-ok check-ins that recovered"
    others = sorted({o.kind for o in in_window if isinstance(o, IncidentObs)})
    if others:
        why += f"; unrelated incidents: {', '.join(others)}"
    return Verdict("pass", why)


# --------------------------------------------------------------------------- state machine

State = Literal["replaying", "awaiting_canary", "canary", "promoted", "rolled_back", "frozen"]
STATES: tuple[str, ...] = (
    "replaying",
    "awaiting_canary",
    "canary",
    "promoted",
    "rolled_back",
    "frozen",
)
TERMINAL = frozenset({"promoted", "rolled_back"})


@dataclass(frozen=True)
class GateInputs:
    """Everything `next_state` needs, loaded by the service.

    - `frozen`: the kill switch is on (D11).
    - `kind`: firmware | harness | code.
    - `replay`: the replay stage's result, once it has run (state `replaying`).
    - `passthrough`: a code candidate whose code is the device's own patch, not drafted by
      the coding agent (BUILD §3, `draft_code_v0 = Id`).
    - `real_code_canary`: a canary device exists that runs real device code (B7).
    - `allow_passthrough_code_promotion`: the setting (default False).
    - `canary_observations`, `canary_started_at`, `canary_window_s`, `repeat_kinds`,
      `min_checkins`: inputs to `canary_verdict` (state `canary`).
    - `touches_safety`: visibility only (K17); it changes the `why`, never the path.
    """

    frozen: bool = False
    kind: str = "firmware"
    replay: ReplayResult | None = None
    passthrough: bool = False
    real_code_canary: bool = False
    allow_passthrough_code_promotion: bool = False
    canary_observations: tuple[CanaryObs, ...] = ()
    canary_started_at: datetime | None = None
    canary_window_s: float = 120
    repeat_kinds: frozenset[str] = field(default_factory=frozenset)
    min_checkins: int = DEFAULT_MIN_CHECKINS
    touches_safety: bool = False


class Transition(NamedTuple):
    """`(new_state, why)`. `new_state == state` means nothing changed (write no event)."""

    state: str
    why: str


def _canary_blocker(inputs: GateInputs) -> str | None:
    """Why a candidate can't go to (or out of) canary right now, or None (B7, BUILD §3)."""
    if inputs.kind != "code":
        return None
    if inputs.passthrough and not inputs.allow_passthrough_code_promotion:
        return (
            "pass-through code candidate (the device's own patch, no coding agent); "
            "ALLOW_PASSTHROUGH_CODE_PROMOTION is off"
        )
    if not inputs.real_code_canary:
        return "no canary device runs real device code (B7)"
    return None


def _step(state: str, inputs: GateInputs, now: datetime) -> Transition:
    if state in TERMINAL:
        return Transition(state, f"{state} is final")
    if inputs.frozen:
        if state == "frozen":
            return Transition(state, "fleet still frozen; candidate waits")
        return Transition("frozen", f"fleet frozen by the kill switch while {state}")
    if state == "frozen":
        return Transition("replaying", "fleet unfrozen; restarting from replay")

    if state == "replaying":
        if inputs.replay is None:
            return Transition(state, "waiting for replay")
        if not inputs.replay.passed:
            return Transition("rolled_back", f"{inputs.replay.why}; never assigned to a device")
        blocker = _canary_blocker(inputs)
        if blocker:
            return Transition("awaiting_canary", f"{inputs.replay.why}; awaiting canary: {blocker}")
        return Transition("canary", f"{inputs.replay.why}; starting canary")

    if state == "awaiting_canary":
        blocker = _canary_blocker(inputs)
        if blocker:
            return Transition(state, f"awaiting canary: {blocker}")
        return Transition("canary", "a canary can take it now; starting canary")

    if state == "canary":
        if inputs.canary_started_at is None:
            return Transition(state, "canary not started yet")
        verdict = canary_verdict(
            inputs.canary_observations,
            inputs.canary_started_at,
            inputs.canary_window_s,
            now,
            repeat_kinds=inputs.repeat_kinds,
            min_checkins=inputs.min_checkins,
        )
        if verdict.status == "fail":
            return Transition("rolled_back", verdict.why)
        if verdict.status == "pending":
            return Transition(state, verdict.why)
        blocker = _canary_blocker(inputs)  # the setting may have changed mid-canary
        if blocker:
            return Transition("awaiting_canary", f"{verdict.why}; not promoting: {blocker}")
        return Transition("promoted", f"{verdict.why}; promoting to the fleet")

    raise ValueError(f"unknown candidate state {state!r}; expected one of {STATES}")


def next_state(state: str, inputs: GateInputs, now: datetime) -> Transition:
    """One step of the candidate state machine (v0 plan §7.4, D6, D11, B7). See the PR table.

    replaying -> canary | awaiting_canary | rolled_back; awaiting_canary -> canary;
    canary -> promoted | rolled_back; any live state -> frozen while the kill switch is on;
    frozen -> replaying on unfreeze (F5). promoted and rolled_back are final.
    """
    t = _step(state, inputs, now)
    if inputs.touches_safety and t.state != state:
        t = Transition(t.state, f"{t.why} [touches safety layer or visor; K17]")
    return t


# --------------------------------------------------------------------------- composition


@dataclass(frozen=True)
class StageResult:
    """One stage's output: whether to continue, the context for the next stage, and notes.

    `notes` are plain-English lines for the Event's `why`; `stubs` names stub stages that
    ran (stored in `candidates.stub_stages`).
    """

    ok: bool
    ctx: Any
    notes: tuple[str, ...] = ()
    stubs: tuple[str, ...] = ()


@dataclass(frozen=True)
class Stage:
    """A named `(ctx) -> StageResult` function. `a >> b` runs b on a's ctx if a is ok."""

    name: str
    fn: Callable[[Any], StageResult]

    def __call__(self, ctx: Any) -> StageResult:
        return self.fn(ctx)

    def __rshift__(self, other: "Stage") -> "Stage":
        return compose(self, other)


def compose(*stages: Stage) -> Stage:
    """Run stages left to right, stopping at the first that isn't ok. Notes accumulate."""

    def run(ctx: Any) -> StageResult:
        notes: tuple[str, ...] = ()
        stubs: tuple[str, ...] = ()
        for stage in stages:
            r = stage(ctx)
            ctx, notes, stubs = r.ctx, notes + r.notes, stubs + r.stubs
            if not r.ok:
                return StageResult(False, ctx, notes, stubs)
        return StageResult(True, ctx, notes, stubs)

    return Stage(" >> ".join(s.name for s in stages) or "id", run)


ID = Stage("id", lambda ctx: StageResult(True, ctx))
"""The identity stage: passes ctx through and adds nothing, so `ID >> s` behaves as `s`."""


def passthrough(name: str) -> Stage:
    """A stub stage: passes ctx through and labels itself (BUILD §3, `draft_code_v0 = Id`)."""
    return Stage(
        name, lambda ctx: StageResult(True, ctx, (f"{name}: stub, pass-through",), (name,))
    )


def by_kind(kind_of: Callable[[Any], str], **stages: Stage) -> Stage:
    """Dispatch on `kind_of(ctx)` (BUILD §3: `by_candidate_kind`, `by_patch_kind`).

    An unknown kind fails with a note instead of passing silently.
    """

    def run(ctx: Any) -> StageResult:
        kind = kind_of(ctx)
        stage = stages.get(kind)
        if stage is None:
            return StageResult(False, ctx, (f"no stage for kind {kind!r}",))
        return stage(ctx)

    return Stage("by_kind(" + ", ".join(f"{k}={s.name}" for k, s in stages.items()) + ")", run)
