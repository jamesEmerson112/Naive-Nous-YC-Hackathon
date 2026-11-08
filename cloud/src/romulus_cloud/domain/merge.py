"""Deterministic merge of an overlay's diff into a firmware manifest (v0 plan §7.3, D3).

The batch job builds a candidate manifest as `apply_overlay(assigned_manifest, overlay.diff)`.
No LLM is involved: the LLM writes prose and skills only, never merged config.

Merge semantics (JSON Merge Patch, RFC 7386, restricted to `rules` and `params`):

- The diff is a partial manifest: `{"rules": {...}, "params": {...}}` (APIs §2.5,
  `Overlay.diff`). Either key may be missing. `None` or `{}` changes nothing.
- **Any other top-level key is a `MergeError`.** `version`, `device_class`, `code_rev` and
  `policy` never change through an overlay (D3). A `harness` key is rejected too: harness
  changes come in as skills the batch writes (D22), not as device diffs.
- **Rules** are keyed by rule name:
  - `{"x": {...}}` where rule `x` exists: the fields are merged into it. A field set to
    `null` removes that field, e.g. `{"below": null, "above": 900}` flips a threshold.
    Fields not named keep their base value.
  - `{"x": {...}}` where rule `x` doesn't exist: adds rule `x` (it must be complete).
  - `{"x": null}`: removes rule `x`. Removing a rule the base doesn't have is a
    `MergeError` (the overlay was made against a different base). Removals are reported
    in `MergeResult.removed_rules` and named in its `why`.
- **Params** deep-merge: nested mappings merge key by key, any other value (number,
  string, list) replaces the base value, and `null` removes the key. Removing a key the
  base doesn't have is a `MergeError`, as for rules.
- **Numbers must be finite.** NaN or ±inf anywhere in the diff is a `MergeError` (JSON
  dumping would silently turn them into `null`).
- The merged manifest is validated as a `FirmwareManifest` (which runs
  `rules.validate_rules`), so every merge result can go straight to `rules.evaluate`.
  An invalid result (e.g. a new rule without `action`) is a `MergeError` listing the
  problems.
- Inputs are never mutated. `MergeResult.manifest` is the manifest dumped with
  `model_dump(mode="json")`, the same form `firmware_response` serves and hashes.

Helpers for the batch job: `config_touches_safety` (K17) and `base_compatible` (merge only
when the assigned manifest still agrees with the overlay's base on what the diff touches).

Pure: imports only `romulus_contracts` and the standard library.
"""

import copy
import math
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from typing import Any

from romulus_contracts import FirmwareManifest

MERGEABLE_KEYS = ("rules", "params")
"""The only top-level diff keys `apply_overlay` merges into a manifest (D3)."""

PATCH_KINDS = ("rules", "params", "harness", "code")
"""Known patch kinds for `by_patch_kind` dispatch (BUILD §3)."""

Path = tuple[str, ...]
_MISSING = object()


class MergeError(ValueError):
    """The diff can't be merged into the manifest. `problems` lists every reason."""

    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("cannot merge overlay: " + "; ".join(problems))


@dataclass(frozen=True)
class MergeResult:
    """The merged manifest and what the merge changed (rule names; dotted param paths)."""

    manifest: dict[str, Any]
    added_rules: tuple[str, ...] = ()
    changed_rules: tuple[str, ...] = ()
    removed_rules: tuple[str, ...] = ()
    changed_params: tuple[str, ...] = ()

    @property
    def why(self) -> str:
        """One plain-English line, e.g. "changes rule camera_occluded; removes rule estop"."""
        parts = [f"adds rule {r}" for r in self.added_rules]
        parts += [f"changes rule {r}" for r in self.changed_rules]
        parts += [f"removes rule {r}" for r in self.removed_rules]
        parts += [f"changes {p}" for p in self.changed_params]
        return "; ".join(parts) or "no change"


def _dotted(path: Path) -> str:
    return ".".join(path)


def _non_finite(value: Any, path: Path) -> Iterator[str]:
    if isinstance(value, float) and not math.isfinite(value):
        yield f"{_dotted(path)} must be a finite number, got {value!r}"
    elif isinstance(value, Mapping):
        for key, item in value.items():
            yield from _non_finite(item, (*path, str(key)))
    elif isinstance(value, (list, tuple)):
        for i, item in enumerate(value):
            yield from _non_finite(item, (*path, str(i)))


def _merge_patch(base: Any, patch: Mapping[str, Any], path: str, problems: list[str]) -> Any:
    """RFC 7386 merge of `patch` into `base` (a copy). Removing a missing key is a problem."""
    result = dict(base) if isinstance(base, Mapping) else {}
    for key, value in patch.items():
        where = f"{path}.{key}"
        if value is None:
            if key in result:
                del result[key]
            else:
                problems.append(f"{where} is removed, but the base manifest doesn't have it")
        elif isinstance(value, Mapping):
            result[key] = _merge_patch(result.get(key), value, where, problems)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _merge_rules(base: Mapping[str, Any], patch: Any, problems: list[str]) -> dict[str, Any]:
    if not isinstance(patch, Mapping):
        problems.append(f"diff 'rules' must be a mapping, got {type(patch).__name__}")
        return dict(base)
    result = dict(base)
    for name, rule_patch in patch.items():
        if rule_patch is None:
            if name in result:
                del result[name]
            else:
                problems.append(f"rule {name!r} is removed, but the base manifest doesn't have it")
        elif isinstance(rule_patch, Mapping):
            result[name] = _merge_patch(result.get(name), rule_patch, f"rules.{name}", problems)
        else:
            problems.append(
                f"rule {name!r} in the diff must be a mapping or null, "
                f"got {type(rule_patch).__name__}"
            )
    return result


def _leaf_paths(patch: Any, prefix: Path) -> list[Path]:
    """The paths a merge patch writes: each non-mapping value (including null) is a leaf."""
    if isinstance(patch, Mapping):
        return [p for key, value in patch.items() for p in _leaf_paths(value, (*prefix, key))]
    return [prefix]


def _at(doc: Any, path: Path) -> Any:
    for key in path:
        if not isinstance(doc, Mapping) or key not in doc:
            return _MISSING
        doc = doc[key]
    return doc


def _as_dict(manifest: Mapping[str, Any] | FirmwareManifest) -> dict[str, Any]:
    if isinstance(manifest, FirmwareManifest):
        return manifest.model_dump(mode="json")
    return copy.deepcopy(dict(manifest))


def _touched(diff: Mapping[str, Any]) -> list[Path]:
    return [p for key in MERGEABLE_KEYS if key in diff for p in _leaf_paths(diff[key], (key,))]


def apply_overlay(
    manifest: Mapping[str, Any] | FirmwareManifest,
    diff: Mapping[str, Any] | None,
    *,
    version: int | None = None,
) -> MergeResult:
    """The manifest with the overlay's `rules`/`params` diff merged in (module docstring).

    `version`, if given, becomes the merged manifest's version (the candidate's number).
    Raises `MergeError` listing every problem; never returns an invalid manifest.
    """
    base = _as_dict(manifest)
    diff = diff or {}
    if not isinstance(diff, Mapping):
        raise MergeError([f"diff must be a mapping, got {type(diff).__name__}"])

    problems = [
        f"diff key {key!r} can't change through an overlay; only rules and params merge (D3)"
        for key in diff
        if key not in MERGEABLE_KEYS
    ]
    problems += list(_non_finite(diff, ()))
    merged = copy.deepcopy(base)
    if "rules" in diff:
        merged["rules"] = _merge_rules(base.get("rules") or {}, diff["rules"], problems)
    if "params" in diff:
        if isinstance(diff["params"], Mapping):
            merged["params"] = _merge_patch(base.get("params"), diff["params"], "params", problems)
        else:
            problems.append(f"diff 'params' must be a mapping, got {type(diff['params']).__name__}")
    if version is not None:
        merged["version"] = version
    if problems:
        raise MergeError(problems)

    try:
        dumped = FirmwareManifest.model_validate(merged).model_dump(mode="json")
    except ValueError as exc:  # pydantic's ValidationError is a ValueError
        errors = exc.errors() if hasattr(exc, "errors") else [{"msg": str(exc)}]
        raise MergeError([f"merged manifest is invalid: {err['msg']}" for err in errors]) from exc

    old, new = base.get("rules") or {}, dumped["rules"]
    params = diff.get("params") or {}
    return MergeResult(
        manifest=dumped,
        added_rules=tuple(sorted(set(new) - set(old))),
        changed_rules=tuple(sorted(r for r in set(new) & set(old) if new[r] != old[r])),
        removed_rules=tuple(sorted(set(old) - set(new))),
        changed_params=tuple(
            _dotted(p)
            for p in _leaf_paths(params, ("params",))
            if p != ("params",) and _at(base, p) != _at(dumped, p)
        ),
    )


def _overlaps(path: str, name: str) -> bool:
    return path == name or path.startswith(name + ".") or name.startswith(path + ".")


def config_touches_safety(
    result: MergeResult, *, safety_rules: Iterable[str] = (), safety_params: Iterable[str] = ()
) -> bool:
    """True if the merge added, changed or removed a safety rule, or changed a safety param.

    For `touches_safety` on overlays and candidates (D5a, K17; visibility only). The device
    teammate supplies the names. `safety_params` are dotted paths without the `params.`
    prefix (e.g. `"limits.torque"`); a change above or below such a path counts.
    """
    rules = set(safety_rules)
    touched_rules = {*result.added_rules, *result.changed_rules, *result.removed_rules}
    if rules & touched_rules:
        return True
    changed = [p.removeprefix("params.") for p in result.changed_params]
    return any(_overlaps(p, name) for p in changed for name in safety_params)


@dataclass(frozen=True)
class Compatibility:
    """`base_compatible`'s answer. Truthy when the merge may go ahead."""

    ok: bool
    why: str
    conflicts: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return self.ok


def base_compatible(
    overlay_base: Mapping[str, Any] | FirmwareManifest | None,
    assigned: Mapping[str, Any] | FirmwareManifest,
    diff: Mapping[str, Any] | None,
) -> Compatibility:
    """May `diff` (made against `overlay_base`) be merged onto `assigned`?

    Yes only if every key the diff writes has the same value (or is absent) in both
    manifests. Otherwise merging could undo a fix the assigned version promoted (a lost
    update), so the batch skips the overlay with an event naming the conflicts. An unknown
    base (None) is not compatible: fail closed.
    """
    if overlay_base is None:
        return Compatibility(False, "the overlay's base manifest is unknown; not merging")
    base, now = _as_dict(overlay_base), _as_dict(assigned)
    touched = _touched(diff) if isinstance(diff, Mapping) else []
    conflicts = tuple(_dotted(p) for p in touched if _at(base, p) != _at(now, p))
    bv, av = base.get("version"), now.get("version")
    if conflicts:
        return Compatibility(
            False,
            f"assigned v{av} changed {', '.join(conflicts)} since the overlay's base v{bv}",
            conflicts,
        )
    return Compatibility(True, f"base v{bv} and assigned v{av} agree on what the diff touches")


def _get(overlay: Any, key: str) -> Any:
    if isinstance(overlay, Mapping):
        return overlay.get(key)
    return getattr(overlay, key, None)


def classify_patch(overlay: Any) -> frozenset[str]:
    """What kinds of change an overlay carries, for `by_patch_kind` dispatch (BUILD §3).

    `overlay` is a contracts `Overlay` or a mapping with `diff` and `code_patch`.
    - `"rules"` / `"params"`: the diff has a non-empty `rules` / `params` (merge, D3).
    - `"harness"`: the diff has a non-empty `harness` key. Not in APIs §2.5; there is no
      stage for it and `apply_overlay` rejects it, so the dispatcher says so.
    - `"code"`: `code_patch` is a non-blank string (coding agent, D21).
    - Any other non-empty diff key is returned as-is (e.g. `"policy"`), so the caller can
      say "no stage for policy" instead of dropping it silently.
    An overlay with nothing in it returns an empty set.
    """
    kinds: set[str] = set()
    diff = _get(overlay, "diff")
    if isinstance(diff, Mapping):
        kinds.update(key for key, value in diff.items() if value not in (None, {}, [], ""))
    code_patch = _get(overlay, "code_patch")
    if isinstance(code_patch, str) and code_patch.strip():
        kinds.add("code")
    return frozenset(kinds)
