"""Fleet Doctor L3: the typed device-tool contract (fleet-doctor interfaces §11, PR 8). **Draft.**

- **Not wired to any route.** No route, socket, lease or relay uses these models. D27 deferred
  L3 live sessions; only this contract is built now (fleet-doctor architecture §9).
- **Two tiers.** `read` tools only look. `act` tools change the arm. When L3 is built, every act
  tool needs its extra grant (`ToolSpec.grant`, e.g. `act:flash`) on top of the lease, and the
  device's own policy must also pass it; the device can still refuse.
- **No raw shell, ever.** Typed tools, not prompts, set the limits. No tool runs an arbitrary
  program (architecture §9); a test enforces it. Ids, revs and names are pattern-bound and
  never start with `-`, so none can pass for an option.
- **Schemas.** `params` is a closed JSON Schema object (`additionalProperties: false`), so a
  call can't carry extra arguments. `returns` is open, like every device-sent payload.
  `validate_args` checks a call's args against `params` by hand (no `jsonschema` dependency):
  types, required and unknown keys, enums, ranges, lengths, patterns and array items. Patterns
  must match the whole string (`re.fullmatch`), so a trailing newline never slips past `$`.
- **What the schemas can't enforce (device side).**
  - `read_file.path` passes a strict relative-path pattern, but the device must still resolve
    it and refuse anything outside `CODE_ROOT`, symlinks included.
  - Free-text args (`park.reason`, `read_logs.grep`) are data only and must never reach a
    shell; `grep` is a plain substring match, not a regex or a program argument.
  - `code_patch` goes through `codepatch` (`summarize_code_patch`, `touches_safety`) before
    it's applied, like any overlay.
  - `run_tests_scratch` runs sandboxed: no network, and no writes outside the scratch copy.
- **Not final.** The catalog is final only after Ishaan's h3 tool list (handoff c); names,
  params and returns may change until then.

These are new, optional models, so `CONTRACT_VERSION` stays "0.1" (D28).
"""

import math
import re
from typing import Any, Literal, Self

from pydantic import model_validator

from romulus_contracts.codepatch import CODE_ROOT
from romulus_contracts.common import CloudModel, DeviceModel, OpenDict
from romulus_contracts.doctor import Text

ToolTier = Literal["read", "act"]
"""`read` only looks; `act` changes the arm and needs an extra grant plus device policy."""


class ToolSpec(CloudModel):
    """One tool in the catalog. `params` and `returns` are JSON Schema objects.

    `grant` is the extra permission an act tool needs on top of the lease (`act:<what>`); a read
    tool has none.
    """

    name: str
    tier: ToolTier
    params: dict[str, Any]
    returns: dict[str, Any]
    summary: str
    grant: str | None = None

    @model_validator(mode="after")
    def _act_tools_need_a_grant(self) -> Self:
        if self.tier == "act" and not (self.grant or "").startswith("act:"):
            raise ValueError("an act tool needs a grant named 'act:<what>'")
        if self.tier == "read" and self.grant is not None:
            raise ValueError("a read tool has no grant")
        return self


class ToolCall(CloudModel):
    """One call to a device tool. `lease_id` names the L3 lease it runs under (none yet)."""

    call_id: str
    name: str
    args: OpenDict = {}
    lease_id: str | None = None


class ToolResult(DeviceModel):
    """The device's answer to a `ToolCall`. `result` follows the tool's `returns` schema when
    `ok`; otherwise `error` says why (a non-string error is stringified, null reads as "")."""

    call_id: str
    ok: bool
    result: Any = None
    error: Text = ""


# --- the catalog ---------------------------------------------------------------------------


def _params(properties: dict[str, Any], required: tuple[str, ...] = ()) -> dict[str, Any]:
    # Closed: a call may not carry arguments the tool doesn't declare.
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


def _returns(properties: dict[str, Any], required: tuple[str, ...] = ()) -> dict[str, Any]:
    # Open: a newer device may return more fields.
    return {"type": "object", "properties": properties, "required": list(required)}


_STR = {"type": "string"}
_INT = {"type": "integer"}
_BOOL = {"type": "boolean"}
_LINES = {"type": "array", "items": {"type": ["string", "object"]}}
_SINCE_S = {"type": "number", "minimum": 0, "maximum": 86400, "description": "look back seconds"}
_SHA = {"type": "string", "pattern": "^[0-9a-f]{64}$"}
_REL_PATH = {
    "type": "string",
    "minLength": 1,
    "maxLength": 512,
    # Starts with [A-Za-z0-9_] (so no `/`, `~`, `.` or `-` first) and uses only [A-Za-z0-9_./-]
    # (so no backslash, `:`, NUL, space or newline). The lookaheads also refuse `//`, a trailing
    # `/`, a `.` or `..` segment, and a `.git` segment anywhere. The device still resolves the
    # path and refuses anything outside CODE_ROOT, symlinks included (the schema can't check that).
    "pattern": (
        r"^(?!.*//)(?!.*/$)(?!(.*/)?\.\.?(/|$))(?!(.*/)?\.git(/|$))"
        r"[A-Za-z0-9_][A-Za-z0-9_./-]*$"
    ),
    "description": f"path relative to {CODE_ROOT}/, e.g. control/hold.py",
}
_ID = {
    "type": "string",
    "pattern": r"^[A-Za-z0-9_][A-Za-z0-9._-]{0,63}$",
    "description": "an id or rev: 1-64 of [A-Za-z0-9._-], never starting with '-' or '.'",
}
# A short lowercase name (camera, process, board); never starts with '-'.
_NAME = {"type": "string", "pattern": r"^[a-z0-9_][a-z0-9_-]{0,31}$"}
_CODE_PATCH = {
    "type": "string",
    "minLength": 1,
    "maxLength": 200_000,
    "description": f"unified diff relative to {CODE_ROOT}/ (the code_patch format, codepatch)",
}
_TEST_IDS = {
    "type": "array",
    "maxItems": 50,
    # No leading '-' (a pytest option) and no '..' (a path out of the scratch copy).
    "items": {
        "type": "string",
        "pattern": r"^(?!.*\.\.)[A-Za-z0-9_][A-Za-z0-9_./:\[\]-]{0,255}$",
    },
    "description": "pytest node ids; all tests when omitted",
}
_HEALTH = {"type": "string", "enum": ["ok", "degraded", "failing"]}
_CHECKS = {
    "type": "array",
    "items": _returns({"name": _STR, "ok": _BOOL, "detail": _STR}, ("name", "ok")),
}

TOOL_CATALOG: tuple[ToolSpec, ...] = (
    # --- read: look only ---
    ToolSpec(
        name="read_logs",
        tier="read",
        summary="Recent device log lines, newest last, optionally filtered by a plain substring.",
        params=_params(
            {
                "since_s": _SINCE_S,
                "max_lines": {"type": "integer", "minimum": 1, "maximum": 2000},
                "grep": {"type": "string", "minLength": 1, "maxLength": 200},
            },
            ("since_s", "max_lines"),
        ),
        returns=_returns({"lines": _LINES, "truncated": _BOOL}, ("lines", "truncated")),
    ),
    ToolSpec(
        name="read_audit",
        tier="read",
        summary="Recent entries of the device's audit file (what the healer and visor did).",
        params=_params(
            {"since_s": _SINCE_S, "max_entries": {"type": "integer", "minimum": 1, "maximum": 500}},
            ("since_s", "max_entries"),
        ),
        returns=_returns(
            {"entries": {"type": "array", "items": {"type": "object"}}, "truncated": _BOOL},
            ("entries", "truncated"),
        ),
    ),
    ToolSpec(
        name="grab_frame",
        tier="read",
        summary="One still frame from an arm camera, as base64 JPEG.",
        params=_params(
            {
                "camera": _NAME,
                "max_width": {"type": "integer", "minimum": 64, "maximum": 1920},
            }
        ),
        returns=_returns(
            {
                "camera": _STR,
                "jpeg_b64": _STR,
                "width": _INT,
                "height": _INT,
                "captured_at": {"type": "string", "format": "date-time"},
            },
            ("jpeg_b64", "width", "height", "captured_at"),
        ),
    ),
    ToolSpec(
        name="stack_dump",
        tier="read",
        summary="Stacks of a named device process, or the STM32 pod's saved HardFault capture.",
        params=_params({"target": _NAME}, ("target",)),
        returns=_returns(
            {
                "target": _STR,
                "threads": {
                    "type": "array",
                    "items": _returns(
                        {"name": _STR, "frames": {"type": "array", "items": _STR}}, ("name",)
                    ),
                },
                "fault": {"type": "object"},
            },
            ("target", "threads"),
        ),
    ),
    ToolSpec(
        name="list_devices",
        tier="read",
        summary="Hardware on this arm (servos, cameras, sensor pod) and whether each answers.",
        params=_params({}),
        returns=_returns(
            {
                "devices": {
                    "type": "array",
                    "items": _returns(
                        {"id": _STR, "kind": _STR, "port": _STR, "ok": _BOOL}, ("id", "kind", "ok")
                    ),
                }
            },
            ("devices",),
        ),
    ),
    ToolSpec(
        name="read_file",
        tier="read",
        summary=f"Read one file of the running device code, by path relative to {CODE_ROOT}/.",
        params=_params(
            {"path": _REL_PATH, "max_bytes": {"type": "integer", "minimum": 1, "maximum": 262144}},
            ("path", "max_bytes"),
        ),
        returns=_returns(
            {"path": _STR, "content": _STR, "size": _INT, "truncated": _BOOL, "sha256": _SHA},
            ("path", "content", "size", "truncated"),
        ),
    ),
    ToolSpec(
        name="run_health",
        tier="read",
        summary="Run the device's health checks (all tiers, or up to one tier) and report each.",
        params=_params({"tier": {"type": "integer", "minimum": 1, "maximum": 3}}),
        returns=_returns({"health": _HEALTH, "checks": _CHECKS}, ("health", "checks")),
    ),
    ToolSpec(
        name="run_tests_scratch",
        tier="read",
        summary=(
            f"Run the device test suite on a throwaway copy of {CODE_ROOT}/, optionally with a "
            "code_patch applied first. Sandboxed: no network, no writes outside the scratch copy, "
            "and the live code is never touched."
        ),
        params=_params(
            {
                "code_patch": _CODE_PATCH,
                "tests": _TEST_IDS,
                "timeout_s": {"type": "number", "minimum": 1, "maximum": 600},
            },
            ("timeout_s",),
        ),
        returns=_returns(
            {
                "passed": _INT,
                "failed": _INT,
                "errors": _INT,
                "patch_applied": _BOOL,
                "output_tail": _LINES,
            },
            ("passed", "failed", "errors"),
        ),
    ),
    # --- act: change the arm; each needs an extra grant and the device's own policy ---
    ToolSpec(
        name="apply_overlay",
        tier="act",
        grant="act:overlay",
        summary=(
            "Apply a code_patch as an overlay. It reverts on its own if verify or health fails, "
            "or if the lease ends before the overlay is confirmed."
        ),
        params=_params(
            {"code_patch": _CODE_PATCH, "proposal_id": _ID},
            ("code_patch",),
        ),
        returns=_returns({"overlay_id": _STR, "applied": _BOOL, "reason": _STR}, ("applied",)),
    ),
    ToolSpec(
        name="park",
        tier="act",
        grant="act:park",
        summary="Move the arm to its safe pose and hold there.",
        params=_params(
            {"reason": {"type": "string", "minLength": 1, "maxLength": 500}}, ("reason",)
        ),
        returns=_returns({"parked": _BOOL, "detail": _STR}, ("parked",)),
    ),
    ToolSpec(
        name="set_mode",
        tier="act",
        grant="act:mode",
        summary="Switch the arm between normal and safe mode (safe: slow, reduced workspace).",
        params=_params({"mode": {"type": "string", "enum": ["normal", "safe"]}}, ("mode",)),
        returns=_returns({"mode": _STR, "changed": _BOOL}, ("mode", "changed")),
    ),
    ToolSpec(
        name="rollback",
        tier="act",
        grant="act:rollback",
        summary=(
            "Roll back the active overlay, code, harness or firmware to a given version, or to "
            "the last known-good one."
        ),
        params=_params(
            {
                "target": {"type": "string", "enum": ["overlay", "code", "harness", "firmware"]},
                "to": _ID | {"description": "a version or code_rev; default: last known-good"},
            },
            ("target",),
        ),
        returns=_returns(
            {"rolled_back": _BOOL, "running": {"type": "object"}, "detail": _STR}, ("rolled_back",)
        ),
    ),
    ToolSpec(
        name="build_and_flash",
        tier="act",
        grant="act:flash",
        summary=(
            "Build the STM32 firmware from a registered code_rev and flash it; the board goes "
            "back to its last verified image if the new one fails to verify."
        ),
        params=_params(
            {
                "code_rev": _ID,
                "board": _NAME,
            },
            ("code_rev",),
        ),
        returns=_returns(
            {"mcu_build": _STR, "flashed": _BOOL, "verified": _BOOL, "rolled_back": _BOOL},
            ("flashed", "verified"),
        ),
    ),
)

_BY_NAME = {spec.name: spec for spec in TOOL_CATALOG}


def tool(name: str) -> ToolSpec:
    """The catalog entry for `name`; `KeyError` if there's no such tool."""
    try:
        return _BY_NAME[name]
    except KeyError:
        raise KeyError(f"unknown tool {name!r}; known: {sorted(_BY_NAME)}") from None


# --- argument checks -----------------------------------------------------------------------


def validate_args(spec: ToolSpec, args: Any) -> list[str]:
    """Every problem with `args` against `spec.params`, as sentences; `[]` means valid.

    A hand-written subset of JSON Schema, enough for this catalog: `type` (one or a list),
    `enum`, `minimum`/`maximum`, `minLength`/`maxLength`, `pattern`, `required`, `properties`,
    `additionalProperties: false`, `items` and `maxItems`. Booleans are never numbers, and a
    whole float (`5.0`) counts as an integer, as in JSON Schema. Unlike JSON Schema's search
    semantics, a `pattern` must match the whole string (`re.fullmatch`), so "x\\n" never passes
    an anchored `^...$`.
    """
    problems: list[str] = []
    _check(args, spec.params, "args", problems)
    return problems


def _is_type(value: Any, name: str) -> bool:
    if name == "string":
        return isinstance(value, str)
    if name == "boolean":
        return isinstance(value, bool)
    if name == "object":
        return isinstance(value, dict)
    if name == "array":
        return isinstance(value, list | tuple)
    if name == "null":
        return value is None
    if isinstance(value, bool) or not isinstance(value, int | float):
        return False
    if isinstance(value, float) and not math.isfinite(value):
        return False
    if name == "integer":
        return isinstance(value, int) or value.is_integer()
    return name == "number"


def _check(value: Any, schema: dict[str, Any], where: str, problems: list[str]) -> None:
    types = schema.get("type")
    if types is not None:
        names = [types] if isinstance(types, str) else list(types)
        if not any(_is_type(value, n) for n in names):
            problems.append(f"{where}: expected {' or '.join(names)}, got {type(value).__name__}")
            return

    if "enum" in schema and not any(value == e and type(value) is type(e) for e in schema["enum"]):
        problems.append(f"{where}: {value!r} is not one of {schema['enum']}")

    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            problems.append(f"{where}: shorter than {schema['minLength']} characters")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            problems.append(f"{where}: longer than {schema['maxLength']} characters")
        if "pattern" in schema and not re.fullmatch(schema["pattern"], value):
            problems.append(f"{where}: {value!r} does not match {schema['pattern']}")
    elif isinstance(value, int | float) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            problems.append(f"{where}: {value} is below the minimum {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            problems.append(f"{where}: {value} is above the maximum {schema['maximum']}")
    elif isinstance(value, dict):
        properties = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in value:
                problems.append(f"{where}: missing required {key!r}")
        for key, item in value.items():
            if key in properties:
                _check(item, properties[key], f"{where}.{key}", problems)
            elif schema.get("additionalProperties") is False:
                problems.append(f"{where}: unknown argument {key!r}")
    elif isinstance(value, list | tuple):
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            problems.append(f"{where}: more than {schema['maxItems']} items")
        if "items" in schema:
            for i, item in enumerate(value):
                _check(item, schema["items"], f"{where}[{i}]", problems)
