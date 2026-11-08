"""Reflex rules evaluation: the one function the device and the cloud gate share (APIs §2.7).

Pure, deterministic, standard library only. It runs on the device (reflexes) and in
the gate's replay, so both sides agree by construction. Do not add I/O, clocks,
randomness or third-party imports here.

Rule shape (APIs §2.1), keyed by rule name:

    {"camera_occluded": {"signal": "brightness", "below": 0.35, "action": "safe_pose"}}

Semantics, pinned by tests/test_rules.py. Items marked (choice) are readings of an
under-specified spot in APIs §2.7; they are listed in the PR and the README.

1. A rule fires when **any** sample of its `signal` is strictly `< below` or
   strictly `> above`. A sample equal to the threshold does not fire.
2. (choice) A rule may set `below`, `above`, or both. With both, it fires when a
   sample is outside the band, i.e. `< below` **or** `> above`, and `below` must be
   smaller than `above` (otherwise the rule would fire on every sample, which is
   treated as a mistake). A threshold set to `null`/`None` counts as not set.
3. `evaluate` returns one `Fired` per firing rule, sorted by rule name (plain
   string order). There is no priority field: callers that need a single action
   decide themselves. Each `Fired` reports the first sample (lowest index) that
   tripped the rule.
4. (choice) Missing signals: a rule whose signal is absent from `signals`, or has
   no usable samples, does not fire. "No data" never triggers a reflex.
5. (choice) Samples: a signal's value is a list/tuple of samples, or a single
   number (one sample). Only real numbers count: `int` and `float`, but not
   `bool`, not NaN. `None`, strings ("0.3" is not coerced), NaN, booleans and
   nested values are skipped, never an error, because a flaky sensor (fault D2)
   must not crash the reflex loop. +/-inf are real comparisons and can fire.
6. (choice) Malformed rules raise `RulesError` before anything is evaluated, listing
   every problem, so the result never depends on which rule happened to be broken.
   `FirmwareManifest` runs the same check (`validate_rules`) when a manifest is
   parsed, so a manifest that parses can always be evaluated, and a bad manifest
   is rejected at install time rather than in the reflex loop. A rule is
   malformed when it is not a mapping, its name is not a non-empty string, its
   `signal` or `action` is not a non-empty string, it has neither threshold, a
   threshold is not a finite real number (bools rejected), or `below >= above`.
7. Other keys inside a rule are ignored (and kept by the caller), so the device
   team can annotate rules. (choice) This includes keys like `enabled`: v0.1 has
   no way to switch a rule off other than removing it.
8. Inputs are never mutated. `signals` that is not a mapping raises `TypeError`.

Skipped samples: `skipped_samples` lists every sample `evaluate` ignored and why, and
`replay_report` bundles it with the result so the gate can explain a failed replay.

Replay (APIs §2.7): a candidate passes replay for an incident when the incident's
`resolving_action` is among the fired actions; see `replay_passes`.
"""

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal


class RulesError(ValueError):
    """The rules mapping is malformed. `problems` lists every issue found."""

    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("malformed rules: " + "; ".join(problems))


@dataclass(frozen=True)
class Fired:
    """One rule that fired, with the first sample that tripped it."""

    rule: str
    signal: str
    action: str
    direction: Literal["below", "above"]
    threshold: float
    value: float
    index: int


def _is_real(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and not math.isnan(x)


def _threshold_problem(name: str, key: str, value: Any) -> str | None:
    if value is None:
        return None
    if not _is_real(value) or math.isinf(value):
        return f"rule {name!r}: {key!r} must be a finite number, got {value!r}"
    return None


def validate_rules(rules: Any) -> list[str]:
    """Return every problem with `rules` (empty list means `evaluate` will accept it)."""
    if not isinstance(rules, Mapping):
        return [f"rules must be a mapping of rule name to rule, got {type(rules).__name__}"]
    problems: list[str] = []
    for name, rule in rules.items():
        if not isinstance(name, str) or not name:
            problems.append(f"rule name must be a non-empty string, got {name!r}")
            continue
        if not isinstance(rule, Mapping):
            problems.append(f"rule {name!r} must be a mapping, got {type(rule).__name__}")
            continue
        for key in ("signal", "action"):
            value = rule.get(key)
            if not isinstance(value, str) or not value:
                problems.append(f"rule {name!r}: {key!r} must be a non-empty string")
        below, above = rule.get("below"), rule.get("above")
        threshold_problems = [
            p
            for p in (
                _threshold_problem(name, "below", below),
                _threshold_problem(name, "above", above),
            )
            if p
        ]
        problems.extend(threshold_problems)
        if below is None and above is None:
            problems.append(f"rule {name!r}: needs 'below' or 'above'")
        elif not threshold_problems and below is not None and above is not None:
            if below >= above:
                problems.append(
                    f"rule {name!r}: 'below' ({below!r}) must be smaller than 'above' ({above!r})"
                )
    return problems


def _samples(value: Any) -> list[tuple[int, float]]:
    """(index, sample) pairs for the usable samples of one signal value."""
    if isinstance(value, (list, tuple)):
        return [(i, s) for i, s in enumerate(value) if _is_real(s)]
    if _is_real(value):
        return [(0, value)]
    return []


def evaluate(rules: Mapping[str, Mapping[str, Any]], signals: Mapping[str, Any]) -> list[Fired]:
    """Every rule that fires on `signals`, sorted by rule name (APIs §2.7).

    See the module docstring for the exact semantics. Raises `RulesError` if
    `rules` is malformed and `TypeError` if `signals` is not a mapping.
    """
    problems = validate_rules(rules)
    if problems:
        raise RulesError(problems)
    if not isinstance(signals, Mapping):
        raise TypeError(f"signals must be a mapping, got {type(signals).__name__}")

    fired: list[Fired] = []
    for name in sorted(rules):
        rule = rules[name]
        below, above = rule.get("below"), rule.get("above")
        for index, sample in _samples(signals.get(rule["signal"])):
            if below is not None and sample < below:
                direction, threshold = "below", below
            elif above is not None and sample > above:
                direction, threshold = "above", above
            else:
                continue
            fired.append(
                Fired(
                    rule=name,
                    signal=rule["signal"],
                    action=rule["action"],
                    direction=direction,
                    threshold=threshold,
                    value=sample,
                    index=index,
                )
            )
            break
    return fired


def fired_actions(fired: list[Fired]) -> list[str]:
    """The distinct actions in `fired`, in the order they first appear."""
    return list(dict.fromkeys(f.action for f in fired))


@dataclass(frozen=True)
class Skipped:
    """Why a rule's signal contributed no usable sample. `index` is None for the whole signal."""

    signal: str
    index: int | None
    reason: str


def _skip_reason(sample: Any) -> str | None:
    if _is_real(sample):
        return None
    if sample is None:
        return "null"
    if isinstance(sample, bool):
        return "boolean"
    if isinstance(sample, float):
        return "NaN"
    if isinstance(sample, str):
        return "string"
    return f"not a number ({type(sample).__name__})"


def skipped_samples(
    rules: Mapping[str, Mapping[str, Any]], signals: Mapping[str, Any]
) -> list[Skipped]:
    """Every sample `evaluate` ignored for the signals `rules` use, and why.

    Sorted by signal name, then index. A missing signal or an empty list is reported once
    with `index=None` ("missing" / "empty"). Raises like `evaluate`.
    """
    problems = validate_rules(rules)
    if problems:
        raise RulesError(problems)
    if not isinstance(signals, Mapping):
        raise TypeError(f"signals must be a mapping, got {type(signals).__name__}")
    skipped: list[Skipped] = []
    for signal in sorted({rule["signal"] for rule in rules.values()}):
        if signal not in signals:
            skipped.append(Skipped(signal, None, "missing"))
            continue
        value = signals[signal]
        if isinstance(value, (list, tuple)):
            if not value:
                skipped.append(Skipped(signal, None, "empty"))
            for i, sample in enumerate(value):
                reason = _skip_reason(sample)
                if reason:
                    skipped.append(Skipped(signal, i, reason))
        else:
            reason = _skip_reason(value)
            if reason:
                skipped.append(Skipped(signal, None, reason))
    return skipped


@dataclass(frozen=True)
class ReplayReport:
    """Replay of one incident against candidate rules, with enough detail to explain it."""

    passed: bool
    resolving_action: str | None
    fired: tuple[Fired, ...]
    skipped: tuple[Skipped, ...]

    def why(self) -> str:
        """One plain-English sentence for the gate's replay event."""
        if not self.resolving_action:
            return "replay failed: the incident has no resolving_action"
        hit = next((f for f in self.fired if f.action == self.resolving_action), None)
        if hit:
            op = "<" if hit.direction == "below" else ">"
            return (
                f"replay passed: {hit.rule} fired {hit.action} "
                f"({hit.signal}[{hit.index}] = {hit.value} {op} {hit.threshold})"
            )
        fired = ", ".join(f"{f.rule}->{f.action}" for f in self.fired) or "nothing"
        text = f"replay failed: {self.resolving_action} did not fire (fired: {fired})"
        if self.skipped:
            counts: dict[tuple[str, str], int] = {}
            for s in self.skipped:
                counts[(s.signal, s.reason)] = counts.get((s.signal, s.reason), 0) + 1
            text += "; ignored " + ", ".join(
                f"{signal}: {n} {reason}" for (signal, reason), n in counts.items()
            )
        return text


def replay_report(
    rules: Mapping[str, Mapping[str, Any]],
    signals: Mapping[str, Any],
    resolving_action: str | None,
) -> ReplayReport:
    """`replay_passes` plus what fired and which samples were ignored (for the event's why)."""
    fired = evaluate(rules, signals)
    passed = bool(resolving_action) and resolving_action in fired_actions(fired)
    return ReplayReport(
        passed=passed,
        resolving_action=resolving_action,
        fired=tuple(fired),
        skipped=tuple(skipped_samples(rules, signals)),
    )


def replay_passes(
    rules: Mapping[str, Mapping[str, Any]],
    signals: Mapping[str, Any],
    resolving_action: str | None,
) -> bool:
    """True when `resolving_action` is among the actions `rules` fire on `signals` (APIs §2.7).

    An incident with no `resolving_action` (None or "") never passes.
    """
    if not resolving_action:
        return False
    return resolving_action in fired_actions(evaluate(rules, signals))
