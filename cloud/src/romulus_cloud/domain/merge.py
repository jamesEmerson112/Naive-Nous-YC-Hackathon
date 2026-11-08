"""Deterministic merge of an overlay's diff into a firmware manifest (v0 plan §7.3, D3).

The batch job builds a candidate manifest as `apply_overlay(assigned_manifest, overlay.diff)`.
No LLM is involved: the LLM writes prose and skills only, never merged config.

Merge semantics (JSON Merge Patch, RFC 7386, restricted to `rules` and `params`):

- The diff is a partial manifest: `{"rules": {...}, "params": {...}}` (APIs §2.5,
  `Overlay.diff`). Either key may be missing. `None` or `{}` changes nothing.
- **Any other top-level key is a `MergeError`.** `version`, `device_class`, `code_rev` and
  `policy` never change through an overlay (D3); a harness or code change takes another
  path (`classify_patch`).
- **Rules** are keyed by rule name:
  - `{"x": {...}}` where rule `x` exists: the fields are merged into it. A field set to
    `null` removes that field, e.g. `{"below": null, "above": 900}` flips a threshold.
    Fields not named keep their base value.
  - `{"x": {...}}` where rule `x` doesn't exist: adds rule `x` (it must be complete).
  - `{"x": null}`: removes rule `x`. Removing a rule the base doesn't have is a
    `MergeError` (the overlay was made against a different base).
- **Params** deep-merge: nested mappings merge key by key, any other value (number,
  string, list) replaces the base value, and `null` removes the key. Removing a key the
  base doesn't have is a `MergeError`, as for rules.
- The merged manifest is validated as a `FirmwareManifest` (which runs
  `rules.validate_rules`), so every merge result can go straight to `rules.evaluate`.
  An invalid result (e.g. a new rule without `action`) is a `MergeError` listing the
  problems.
- Inputs are never mutated. The result is the manifest dumped with
  `model_dump(mode="json")`, the same form `firmware_response` serves and hashes.

Pure: imports only `romulus_contracts` and the standard library.
"""

import copy
from collections.abc import Mapping
from typing import Any

from romulus_contracts import FirmwareManifest

MERGEABLE_KEYS = ("rules", "params")
"""The only top-level diff keys `apply_overlay` merges into a manifest (D3)."""

PATCH_KINDS = ("rules", "params", "harness", "code")
"""Known patch kinds for `by_patch_kind` dispatch (BUILD §3)."""


class MergeError(ValueError):
    """The diff can't be merged into the manifest. `problems` lists every reason."""

    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("cannot merge overlay: " + "; ".join(problems))


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


def apply_overlay(
    manifest: Mapping[str, Any] | FirmwareManifest,
    diff: Mapping[str, Any] | None,
    *,
    version: int | None = None,
) -> dict[str, Any]:
    """The manifest with the overlay's `rules`/`params` diff merged in (module docstring).

    `version`, if given, becomes the merged manifest's version (the candidate's number).
    Raises `MergeError` listing every problem; never returns an invalid manifest.
    """
    base = (
        manifest.model_dump(mode="json")
        if isinstance(manifest, FirmwareManifest)
        else copy.deepcopy(dict(manifest))
    )
    diff = diff or {}
    if not isinstance(diff, Mapping):
        raise MergeError([f"diff must be a mapping, got {type(diff).__name__}"])

    problems = [
        f"diff key {key!r} can't change through an overlay; only rules and params merge (D3)"
        for key in diff
        if key not in MERGEABLE_KEYS
    ]
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
        return FirmwareManifest.model_validate(merged).model_dump(mode="json")
    except ValueError as exc:  # pydantic's ValidationError is a ValueError
        errors = exc.errors() if hasattr(exc, "errors") else [{"msg": str(exc)}]
        raise MergeError([f"merged manifest is invalid: {err['msg']}" for err in errors]) from exc


def _get(overlay: Any, key: str) -> Any:
    if isinstance(overlay, Mapping):
        return overlay.get(key)
    return getattr(overlay, key, None)


def classify_patch(overlay: Any) -> frozenset[str]:
    """What kinds of change an overlay carries, for `by_patch_kind` dispatch (BUILD §3).

    `overlay` is a contracts `Overlay` or a mapping with `diff` and `code_patch`.
    - `"rules"` / `"params"`: the diff has a non-empty `rules` / `params` (merge, D3).
    - `"harness"`: the diff has a non-empty `harness` key *(inferred: not in APIs §2.5
      yet; the batch writes a skill for it)*.
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
