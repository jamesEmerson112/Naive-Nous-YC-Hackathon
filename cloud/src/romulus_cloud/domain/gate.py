"""The gate's pure decisions: replay, canary verdict, candidate state machine (v0 plan §7.4, D6).

Every decision the gate makes is a function here; `services/gate.py` only loads inputs,
calls these, writes the result and its Event. Nothing here touches the DB, the network or
the clock: `now` is always passed in.

- `replay(manifest, cases)`: the candidate's rules must fire each case's expected action
  (APIs §2.7, `romulus_contracts.rules.replay_report`).
- `replay_stub(reason)` / `replay_code_stub()`: a stub replay that passes and says so.
- `canary_verdict(observations, started_at, window_s, now, version=, install_type=)`:
  pending, pass or fail, counting only reports for the candidate's version.
- `repeat_since(observations, started_at, version=, repeat_kinds=)`: a repeat incident on
  the candidate's version since a run started (checked on unfreeze).
- `next_state(state, inputs, now) -> Transition(state, why, start_canary)`: one step of the
  candidate state machine. Defaults fail closed. On `start_canary` the service sets
  `canary_started_at = now`, so every canary run starts fresh.
- `Stage`, `compose` / `>>`, `ID`, `passthrough`, `by_kind`: stages as composable functions
  (BUILD §3: `gate = replay >> canary >> promote_or_rollback`).

Pure: imports only `romulus_contracts` and the standard library.
"""

import math
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

    @classmethod
    def from_json(cls, data: Mapping[str, Any] | None) -> "ReplayResult | None":
        """Read back `to_json()` (as stored in `candidates.replay`); None stays None.

        A missing `stub` reads as True: fail closed, "no tests ran" until proven otherwise.
        """
        if data is None:
            return None
        cases = tuple(
            CaseResult(c["case_id"], bool(c["passed"]), c.get("why", ""), tuple(c.get("fired", ())))
            for c in data.get("cases", ())
        )
        return cls(
            bool(data.get("passed")), data.get("why", ""), cases, bool(data.get("stub", True))
        )


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

Version = int | str
"""A firmware/harness version (int) or a code rev (str). Compared with `==`: 8 != "8"."""


@dataclass(frozen=True)
class CheckinObs:
    """A canary check-in (APIs §2.3).

    - `health`: ok, degraded or failing.
    - `version`: what the device runs, of the candidate's type (e.g. `running.firmware` for
      a firmware candidate). None means unknown.
    - `overlay_id`: the device's active overlay, if any.
    - `device_id`: check-ins with the same `(device_id, at)` count once.
    """

    at: datetime
    health: str
    version: Version | None = None
    overlay_id: str | None = None
    device_id: str = ""


@dataclass(frozen=True)
class InstallObs:
    """A canary install report (APIs §2.4): `type` firmware | harness | code, and `version`."""

    at: datetime
    result: str  # installed | rolled_back | hash_mismatch | invalid
    version: Version
    type: str = "firmware"
    detail: str = ""


@dataclass(frozen=True)
class IncidentObs:
    """An incident the canary reported. `version` is what it ran (None: unknown)."""

    at: datetime
    kind: str
    version: Version | None = None


CanaryObs = CheckinObs | InstallObs | IncidentObs

INSTALL_FAILURES = frozenset({"rolled_back", "hash_mismatch", "invalid"})
"""Install results that fail a canary at once (`invalid` is the contracts' proposed value)."""

DEFAULT_MIN_CHECKINS = 2
"""Healthy check-ins a canary needs in its window, so one lucky report can't pass it
*(inferred; D6 leaves "healthy for N minutes" to settle with the device side)*."""

COVERAGE_FRACTION = 1 / 3
"""A pass needs an `ok` check-in in the last third of the window, so a canary that goes
silent can't pass on early reports *(inferred)*."""


@dataclass(frozen=True)
class Verdict:
    status: Literal["pending", "pass", "fail"]
    why: str


def _ts(at: datetime, started_at: datetime) -> str:
    return f"{(at - started_at).total_seconds():g} s after the canary started"


def check_window(window_s: float) -> None:
    """Raise `ValueError` unless `window_s` is a finite number of seconds >= 0."""
    if (
        isinstance(window_s, bool)
        or not isinstance(window_s, (int, float))
        or not math.isfinite(window_s)
        or window_s < 0
    ):
        raise ValueError(f"canary window must be a finite number of seconds >= 0, got {window_s!r}")


def canary_verdict(
    observations: Iterable[CanaryObs],
    started_at: datetime,
    window_s: float,
    now: datetime,
    *,
    version: Version,
    install_type: str,
    repeat_kinds: Iterable[str] = (),
    min_checkins: int = DEFAULT_MIN_CHECKINS,
) -> Verdict:
    """Pending, pass or fail for one canary run (v0 plan §7.4, D6, D7).

    `started_at` is this run's start (`Transition.start_canary`); observations before it
    are ignored, so a re-run never reuses an earlier run's reports. Order doesn't matter.
    Pass only the canary device's observations. `version` / `install_type` name the
    candidate. An observation with an unknown version (None) counts as this version for
    every failure signal (fail closed) but never as a healthy check-in.

    1. **Fail fast**, at any time at or after `started_at` (even after the window ends):
       - an install of this `install_type` and `version` that is `rolled_back`,
         `hash_mismatch` or `invalid` (other versions' and types' installs are ignored);
       - after the matching install, a check-in on another known version: the canary fell
         back without an install report ("canary no longer runs firmware 8");
       - a `failing` check-in, or an incident whose kind is in `repeat_kinds`, unless it
         is known to come from another version;
       - a check-in with an `overlay_id`, unless it is known to come from another version:
         the healer had to patch the candidate, so it didn't stand on its own.
       The earliest such observation is the reason.
    2. **Pending** while `now < started_at + window_s`.
    3. **At or after the window end** (exactly `window_s` counts), "healthy enough" means,
       counting check-ins from the first matching `installed` report up to the window end,
       each `(device_id, at)` once:
       - an `installed` report for this version inside the window;
       - at least `min_checkins` (>= 1) `ok` check-ins on this version;
       - one of them in the last third of the window (coverage: no going silent);
       - the last check-in is one of them. A `degraded` (or unknown-version) check-in
         is not ok; it is tolerated only if a later ok check-in follows it.
       Otherwise the verdict is fail, with the reason.
    Incidents of other kinds don't fail the canary; the pass reason mentions them.
    """
    check_window(window_s)
    repeat = set(repeat_kinds)
    end = started_at + timedelta(seconds=window_s)
    obs = sorted((o for o in observations if o.at >= started_at), key=lambda o: o.at)

    def other_version(o: CheckinObs | IncidentObs) -> bool:
        return o.version is not None and o.version != version

    def this_install(o: CanaryObs) -> bool:
        return isinstance(o, InstallObs) and o.type == install_type and o.version == version

    if isinstance(min_checkins, bool) or not isinstance(min_checkins, int) or min_checkins < 1:
        raise ValueError(f"min_checkins must be an integer >= 1, got {min_checkins!r}")
    installed_at = next((o.at for o in obs if this_install(o) and o.result == "installed"), None)
    seen: set[tuple[str, datetime]] = set()
    checkins: list[CheckinObs] = []  # this or an unknown version, after the install, deduped
    for o in obs:
        if (
            isinstance(o, CheckinObs)
            and installed_at is not None
            and o.at >= installed_at
            and not other_version(o)
            and (o.device_id, o.at) not in seen
        ):
            seen.add((o.device_id, o.at))
            checkins.append(o)

    failures: list[tuple[datetime, str]] = []
    for o in obs:
        when = _ts(o.at, started_at)
        if this_install(o) and o.result in INSTALL_FAILURES:
            detail = f" ({o.detail})" if o.detail else ""
            failures.append((o.at, f"canary install {o.result} {when}{detail}"))
        elif isinstance(o, IncidentObs) and o.kind in repeat and not other_version(o):
            failures.append((o.at, f"canary repeated {o.kind} {when}"))
        elif isinstance(o, CheckinObs):
            if installed_at is not None and o.at >= installed_at and other_version(o):
                failures.append(
                    (
                        o.at,
                        f"canary no longer runs {install_type} {version}: "
                        f"reported {o.version} {when}",
                    )
                )
            elif o.health == "failing" and not other_version(o):
                failures.append((o.at, f"canary reported health failing {when}"))
            elif o.overlay_id is not None and not other_version(o):
                failures.append((o.at, f"canary needed a local overlay ({o.overlay_id}) {when}"))
    if failures:
        return Verdict("fail", min(failures, key=lambda f: f[0])[1])

    if now < end:
        left = (end - now).total_seconds()
        return Verdict("pending", f"canary window open; {left:g} s of {window_s:g} s left")

    if installed_at is None or installed_at > end:
        return Verdict(
            "fail",
            f"no successful {install_type} {version} install within the {window_s:g} s window",
        )
    in_window = [c for c in checkins if c.at <= end]
    ok = [c for c in in_window if c.health == "ok" and c.version == version]
    if len(ok) < min_checkins:
        return Verdict(
            "fail",
            f"only {len(ok)} healthy check-ins on {install_type} {version} after install "
            f"in the {window_s:g} s window; need {min_checkins}",
        )
    tail = end - timedelta(seconds=window_s * COVERAGE_FRACTION)
    if not any(c.at >= tail for c in ok):
        return Verdict(
            "fail",
            f"no healthy check-in in the last {window_s * COVERAGE_FRACTION:g} s of the window",
        )
    last = in_window[-1]
    if last is not ok[-1]:
        state = last.health if last.version == version else f"{last.health}, version unknown"
        return Verdict("fail", f"last check-in in the window was not ok ({state})")
    why = f"canary healthy for {window_s:g} s: installed {version}, {len(ok)} ok check-ins"
    if len(in_window) > len(ok):
        why += f", {len(in_window) - len(ok)} non-ok check-ins that recovered"
    others = sorted({o.kind for o in obs if isinstance(o, IncidentObs) and o.at <= end})
    if others:
        why += f"; unrelated incidents: {', '.join(others)}"
    return Verdict("pass", why)


def repeat_since(
    observations: Iterable[CanaryObs],
    started_at: datetime,
    *,
    version: Version,
    repeat_kinds: Iterable[str],
) -> str | None:
    """Why the candidate failed, if the canary repeated one of `repeat_kinds` at or after
    `started_at` on this `version` (or an unknown one: fail closed, as `canary_verdict`
    does). The gate asks on unfreeze, so a repeat the canary reported while the fleet was
    frozen (outside any open window) still counts against the candidate it hit."""
    repeat = set(repeat_kinds)
    hits = sorted(
        (
            o
            for o in observations
            if isinstance(o, IncidentObs)
            and o.at >= started_at
            and o.kind in repeat
            and (o.version is None or o.version == version)
        ),
        key=lambda o: o.at,
    )
    if not hits:
        return None
    return f"canary repeated {hits[0].kind} {_ts(hits[0].at, started_at)}"


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
RULE_KINDS = frozenset({"firmware", "harness"})
"""Candidate kinds that skip the code rules. Matched exactly; anything else is held."""


@dataclass(frozen=True)
class GateInputs:
    """Everything `next_state` needs, loaded by the service. Defaults fail closed.

    - `frozen`: the kill switch is on (D11).
    - `kind`: firmware | harness | code, matched exactly.
    - `replay`: the replay result. Keep passing it after replay (`ReplayResult.from_json`):
      a code candidate whose replay was a stub (no device tests ran) can't go on.
    - `canary_available`: a canary device exists (default False).
    - `passthrough`: a code candidate whose code is the device's own patch, not drafted by
      the coding agent (BUILD §3). Default True: the service opts out explicitly.
    - `real_code_canary`: a canary device exists that runs real device code (B7).
    - `allow_passthrough_code_promotion`: the setting (default False).
    - `candidate_version`, `install_type`: what the canary must install and run
      (`install_type` defaults to `kind`). Required in state `canary`.
    - `canary_started_at`: this canary run's start (set when `start_canary` was True).
      Pass it in state `frozen` too: on unfreeze, a repeat incident on the candidate's
      version since then fails the candidate instead of restarting it (`repeat_since`).
    - `canary_observations`, `canary_window_s`, `repeat_kinds`, `min_checkins`: inputs to
      `canary_verdict`.
    - `touches_safety`: visibility only (K17); it changes the `why`, never the path.
    """

    frozen: bool = False
    kind: str = "firmware"
    replay: ReplayResult | None = None
    canary_available: bool = False
    passthrough: bool = True
    real_code_canary: bool = False
    allow_passthrough_code_promotion: bool = False
    candidate_version: Version | None = None
    install_type: str | None = None
    canary_observations: tuple[CanaryObs, ...] = ()
    canary_started_at: datetime | None = None
    canary_window_s: float = 120
    repeat_kinds: frozenset[str] = field(default_factory=frozenset)
    min_checkins: int = DEFAULT_MIN_CHECKINS
    touches_safety: bool = False


class Transition(NamedTuple):
    """One step's result. `state == old state` means nothing changed (write no event).

    `start_canary` is True on every move into `canary`: the service must then assign the
    canary and set `canary_started_at = now`, so the run starts fresh (F5).
    """

    state: str
    why: str
    start_canary: bool = False


def _canary_blocker(inputs: GateInputs) -> str | None:
    """Why a candidate can't go to (or out of) canary right now, or None. Fails closed."""
    if not inputs.canary_available:
        return "no canary device is available"
    if inputs.kind in RULE_KINDS:
        return None
    if inputs.kind != "code":
        return f"unknown candidate kind {inputs.kind!r} (known: firmware, harness, code)"
    allow = inputs.allow_passthrough_code_promotion
    if inputs.passthrough and not allow:
        return (
            "pass-through code candidate (the device's own patch, no coding agent); "
            "ALLOW_PASSTHROUGH_CODE_PROMOTION is off"
        )
    if (inputs.replay is None or inputs.replay.stub) and not allow:
        return (
            "no device tests ran (replay was a stub, M8); ALLOW_PASSTHROUGH_CODE_PROMOTION is off"
        )
    if not inputs.real_code_canary:
        return "no canary device runs real device code (B7)"
    return None


def _start_canary(why: str) -> Transition:
    return Transition("canary", why, start_canary=True)


def _step(state: str, inputs: GateInputs, now: datetime) -> Transition:
    if state in TERMINAL:
        return Transition(state, f"{state} is final")
    if inputs.frozen:
        if state == "frozen":
            return Transition(state, "fleet still frozen; candidate waits")
        return Transition("frozen", f"fleet frozen by the kill switch while {state}")
    if state == "frozen":
        if inputs.canary_started_at is not None and inputs.candidate_version is not None:
            hit = repeat_since(
                inputs.canary_observations,
                inputs.canary_started_at,
                version=inputs.candidate_version,
                repeat_kinds=inputs.repeat_kinds,
            )
            if hit:
                return Transition("rolled_back", f"{hit} (seen on unfreeze); not restarting")
        return Transition("replaying", "fleet unfrozen; restarting from replay")

    if state == "replaying":
        if inputs.replay is None:
            return Transition(state, "waiting for replay")
        if not inputs.replay.passed:
            return Transition("rolled_back", f"{inputs.replay.why}; never assigned to a device")
        blocker = _canary_blocker(inputs)
        if blocker:
            return Transition("awaiting_canary", f"{inputs.replay.why}; awaiting canary: {blocker}")
        return _start_canary(f"{inputs.replay.why}; starting a fresh canary")

    if state == "awaiting_canary":
        blocker = _canary_blocker(inputs)
        if blocker:
            return Transition(state, f"awaiting canary: {blocker}")
        return _start_canary("a canary can take it now; starting a fresh canary")

    if state == "canary":
        if inputs.canary_started_at is None:
            return Transition(state, "canary not started yet")
        if inputs.candidate_version is None:
            raise ValueError("GateInputs.candidate_version is required in state 'canary'")
        verdict = canary_verdict(
            inputs.canary_observations,
            inputs.canary_started_at,
            inputs.canary_window_s,
            now,
            version=inputs.candidate_version,
            install_type=inputs.install_type or inputs.kind,
            repeat_kinds=inputs.repeat_kinds,
            min_checkins=inputs.min_checkins,
        )
        if verdict.status == "fail":
            return Transition("rolled_back", verdict.why)
        if verdict.status == "pending":
            return Transition(state, verdict.why)
        blocker = _canary_blocker(inputs)  # inputs may have changed mid-canary
        if blocker:
            return Transition("awaiting_canary", f"{verdict.why}; not promoting: {blocker}")
        return Transition("promoted", f"{verdict.why}; promoting to the fleet")

    raise ValueError(f"unknown candidate state {state!r}; expected one of {STATES}")


def next_state(state: str, inputs: GateInputs, now: datetime) -> Transition:
    """One step of the candidate state machine (v0 plan §7.4, D6, D11, B7). See the PR table.

    replaying -> canary | awaiting_canary | rolled_back; awaiting_canary -> canary;
    canary -> promoted | rolled_back | awaiting_canary; any live state -> frozen while the
    kill switch is on; frozen -> replaying on unfreeze (F5), or rolled_back if the canary
    repeated a folded incident's kind on this version since its run started. promoted and
    rolled_back are final. Every move into canary has `start_canary=True`.
    """
    if state == "canary":
        check_window(inputs.canary_window_s)
    t = _step(state, inputs, now)
    if inputs.touches_safety and t.state != state:
        t = t._replace(why=f"{t.why} [touches safety layer or visor; K17]")
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
