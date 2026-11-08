"""Datasets, snapshots and exports as pure functions (telemetry §5–6, §10; flow F8).

"Narrow loop, general data" (D16): every failure becomes training data. `services/datasets.py`
loads rows into the plain dicts these functions take, and writes what they return.

- `parse_filter(raw)`: validate a dataset's saved filter (unknown keys are refused, so a
  typo can't silently match everything).
- `matches_recording(f, rec, labels)` / `matches_incident(f, inc)`: does a row match.
- `snapshot_manifest(...)`: the canonical JSON and sha256 that pin a dataset version.
- `regression_case(inc)`: the replay case one resolved incident becomes, or None.
- `sharegpt(lines)`: an agent-trace JSONL file as Hermes-style `conversations`.
- `trajectory(run)` / `dataset_card(...)`: a labelled run as one Hermes Agent trajectory
  (`completed` = the verifier's verdict) and the card that goes with the export.
- `redact(value)`: strip obvious secrets, home-directory paths and emails (telemetry §10).
- `canonical(obj)` / `jsonl(rows)`: byte-identical output for identical input, so the
  same snapshot always produces the same export file (F8).

Pure: imports only the standard library; nothing reads a clock.
"""

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

PURPOSES = ("firmware", "harness", "policy")
TRAJECTORIES = "trajectories"  # fleet learning: labelled runs as Hermes trajectories
FORMATS = ("regression_corpus", "traces_jsonl", TRAJECTORIES)
CUT_FORMATS = {"lerobot": "the LeRobot export is cut for this weekend (P2, build plan K1)"}
FILTER_KEYS = ("kind", "labels", "device", "since", "until")


# --------------------------------------------------------------------------- filters


@dataclass(frozen=True)
class Filter:
    """A dataset's saved query over recordings. Empty fields match everything."""

    kinds: tuple[str, ...] = ()
    labels: tuple[str, ...] = ()  # every label must be present
    device: str | None = None
    since: datetime | None = None
    until: datetime | None = None

    @property
    def recording_only(self) -> bool:
        """True when the filter names something only recordings have (kind or labels)."""
        return bool(self.kinds or self.labels)


def _strings(value: Any, key: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
        raise ValueError(f"filter.{key} must be a string or a list of non-empty strings")
    return tuple(sorted({v.strip() for v in value}))


def _time(value: Any, key: str) -> datetime | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ValueError(f"filter.{key} must be an ISO 8601 time")
    try:
        at = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"filter.{key} is not an ISO 8601 time: {value!r}") from exc
    if at.tzinfo is None:
        raise ValueError(f"filter.{key} needs a time zone (e.g. a trailing Z): {value!r}")
    return at


def parse_filter(raw: Mapping[str, Any] | None) -> Filter:
    """`{"kind": "incident_window", "labels": ["kind:gripper_stall"], "since": "…Z"}` →
    `Filter`. Raises ValueError with a sentence for unknown keys or bad values."""
    raw = dict(raw or {})
    unknown = sorted(set(raw) - set(FILTER_KEYS))
    if unknown:
        raise ValueError(f"unknown filter keys {unknown}; known: {list(FILTER_KEYS)}")
    device = raw.get("device")
    if device is not None and (not isinstance(device, str) or not device.strip()):
        raise ValueError("filter.device must be a device id")
    f = Filter(
        kinds=_strings(raw.get("kind"), "kind"),
        labels=_strings(raw.get("labels"), "labels"),
        device=device.strip() if device else None,
        since=_time(raw.get("since"), "since"),
        until=_time(raw.get("until"), "until"),
    )
    if f.since and f.until and f.since > f.until:
        raise ValueError("filter.since is after filter.until")
    return f


def _in_window(f: Filter, at: datetime | None) -> bool:
    if at is None:
        return f.since is None and f.until is None
    return (f.since is None or at >= f.since) and (f.until is None or at <= f.until)


def matches_recording(f: Filter, rec: Mapping[str, Any], labels: Iterable[str]) -> bool:
    """`rec` has `kind`, `device_id` and `at` (when it started, else when it arrived)."""
    if f.kinds and rec["kind"] not in f.kinds:
        return False
    if f.device and rec["device_id"] != f.device:
        return False
    if f.labels and not set(f.labels) <= set(labels):
        return False
    return _in_window(f, rec.get("at"))


def matches_incident(f: Filter, inc: Mapping[str, Any]) -> bool:
    """An incident joins a snapshot on its own only when the filter names nothing that only
    recordings have; otherwise it joins through a matching recording *(inferred)*."""
    if f.recording_only:
        return False
    if f.device and inc["device_id"] != f.device:
        return False
    return _in_window(f, inc.get("occurred_at"))


# --------------------------------------------------------------------------- snapshots


def canonical(obj: Any) -> bytes:
    """JSON with sorted keys and no spaces: the same value always gives the same bytes."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def snapshot_manifest(
    dataset_id: str,
    n: int,
    filter_: Mapping[str, Any],
    recordings: Iterable[Mapping[str, Any]],
    incident_ids: Iterable[str],
    context: Mapping[str, Mapping[str, Any]] | None = None,
) -> tuple[dict[str, Any], str]:
    """The frozen contents of a dataset version and their sha256.

    `recordings` are dicts with at least `id` and `sha256` (plus whatever an export needs:
    kind, device, incident). `context` is the per-incident state exports read (overlay
    status, lock-in, work order), frozen now so a later change can't alter an export.
    Lists are sorted, so the hash doesn't depend on the order rows were read in.
    """
    recs = {r["id"]: dict(r) for r in recordings}
    manifest = {
        "dataset_id": dataset_id,
        "n": n,
        "filter": dict(filter_),
        "recordings": [recs[k] for k in sorted(recs)],
        "incident_ids": sorted(set(incident_ids)),
        "context": {k: dict(v) for k, v in sorted((context or {}).items())},
    }
    return manifest, sha256(canonical(manifest))


# --------------------------------------------------------------------------- regression corpus


# Fixes a person or a part makes; a firmware rule can never fire them.
HARDWARE_ACTIONS = ("park", "torque_off", "replace_part", "order_part", "work_order")


def regression_case(inc: Mapping[str, Any]) -> dict[str, Any] | None:
    """The replay case a past incident becomes (telemetry §6.1), or None.

    A case needs a resolved incident, the action that fixed it and some signals to replay.
    Left out, because no firmware rule can fire their fix *(inferred)*:
    - incidents fixed by a code patch (`restart_app`); code candidates replay device tests (M8);
    - incidents fixed by a hardware action (`HARDWARE_ACTIONS`) or escalated to a work order.
    `services.datasets.sync_regression_cases` applies the same rule in SQL; keep them in step.
    """
    action = (inc.get("resolving_action") or "").strip()
    signals = inc.get("signals") or {}
    if not inc.get("resolved") or not action or not isinstance(signals, Mapping) or not signals:
        return None
    if inc.get("code_patch") or inc.get("work_order") or action.lower() in HARDWARE_ACTIONS:
        return None
    return {
        "incident_id": inc["incident_id"],
        "kind": inc.get("kind") or "",
        "signals": dict(signals),
        "expected_action": action,
    }


# --------------------------------------------------------------------------- traces

_ROLES = {"user": "human", "human": "human", "assistant": "gpt", "gpt": "gpt"}


def _turn(msg: Any) -> dict[str, Any]:
    if not isinstance(msg, dict):
        return {"from": "raw", "value": msg if isinstance(msg, str) else json.dumps(msg)}
    role = str(msg.get("role") or msg.get("from") or "raw")
    value = msg.get("content", msg.get("value", ""))
    turn: dict[str, Any] = {
        "from": _ROLES.get(role, role),
        "value": value if isinstance(value, str) else json.dumps(value, sort_keys=True),
    }
    if msg.get("tool_calls"):
        turn["tool_calls"] = msg["tool_calls"]
    if msg.get("name"):
        turn["name"] = msg["name"]
    return turn


def sharegpt(text: str) -> list[dict[str, Any]]:
    """One agent-trace JSONL file → `[{from, value, tool_calls?}]` (Hermes trajectory shape).

    Roles map user → human and assistant → gpt; others (system, tool) keep their name. A
    line that isn't JSON is kept as `{"from": "raw", "value": line}`, never dropped.
    """
    turns = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            msg = line
        turns.append(_turn(msg))
    return turns


# Dict keys whose whole value is a secret, at any depth (tool-call arguments included).
SECRET_KEY = re.compile(
    r"(?i)(api[_-]?key|token(?!s)|secret|passw(or)?d|authorization|cookie|credential|"
    r"(^|[_-])key$|private[_-]?key)"
)
_NAME = (
    r"[\w-]*(?:api[_-]?key|token(?!s)|secret|passw(?:or)?d|authorization|cookie|credential)[\w-]*"
)
R = "[REDACTED]"
_SECRETS = [
    # "name": "value"  → the whole quoted value, spaces included
    (re.compile(rf'(?i)("{_NAME}"\s*:\s*")(?:[^"\\]|\\.)*(")'), rf"\1{R}\2"),
    # name: value / name=value  → the rest of the line (over-redacting is the safe side)
    (re.compile(rf"(?i)\b({_NAME})(\s*[:=]\s*\"?)(?!\[REDACTED\])[^\n\"]+"), rf"\1\2{R}"),
    (re.compile(r"(?i)\b(bearer|basic|token)\s+[A-Za-z0-9._~+/=-]{8,}"), rf"\1 {R}"),
    # user:password@ in URLs (postgres://romulus:hunter2@db)
    (re.compile(r"(://[^/\s:@]+:)[^/\s@]+@"), rf"\1{R}@"),
    (re.compile(r"\b(?:sk|rk|pk|nk)_(?:live|test)_[A-Za-z0-9]{8,}"), R),  # Stripe, Naïve
    (re.compile(r"\bwhsec_[A-Za-z0-9]{16,}"), R),  # Stripe webhook secret
    (re.compile(r"\bmcp_[A-Za-z0-9_-]{12,}"), R),  # Naïve MCP bearer
    (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"), R),  # OpenAI-style
    (re.compile(r"\b(?:hf|gh[pousr]|xox[abp])_[A-Za-z0-9_-]{16,}"), R),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), R),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S), R),
    # home directories name a person; keep the rest of the path for debugging
    (re.compile(r"(?:/Users|/home)/[^/\s\"']+"), "~"),
    (re.compile(r"(?i)[A-Z]:\\Users\\[^\\\s\"']+"), "~"),
    (re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"), "[EMAIL]"),
]


def redact_text(text: str) -> str:
    for pattern, repl in _SECRETS:
        text = pattern.sub(repl, text)
    return text


def redact(value: Any) -> Any:
    """`value` with obvious secrets removed (telemetry §10), at any depth.

    A dict value whose key names a secret (`api_key`, `token`, `Authorization`, …) is
    replaced whole, whatever its type; every other string is scrubbed with the patterns
    above (keys, bearer tokens, URL passwords, home paths, emails).
    """
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {
            k: R
            if isinstance(k, str) and SECRET_KEY.search(k) and v not in (None, "")
            else redact(v)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


# --------------------------------------------------------------------------- files


def jsonl(rows: Iterable[Any]) -> bytes:
    return b"".join(canonical(r) + b"\n" for r in rows)


# --------------------------------------------------------------------------- trajectories
# Fleet learning (docs/recursive-self-improvement/design.md §3): one row per labelled run a
# verifier passed or failed, in Hermes Agent's trajectory format (docs/raw/hermes-llms-full.txt
# "Trajectory Format"): `{conversations, timestamp, model, completed, metadata}`, roles
# system/human/gpt/tool, calls as inline <tool_call> blocks, results as <tool_response>.

LABELLED_VERDICTS = ("pass", "fail")
CARD_VERDICTS = ("pass", "fail", "unverified", "excluded")
HERMES_SYSTEM = (
    "You are a function calling AI model. You are provided with function signatures within "
    "<tools> </tools> XML tags. You may call one or more functions to assist with the user "
    "query. If available tools are not relevant in assisting with user query, just respond "
    "in natural conversational language. Don't make assumptions about what values to plug "
    "into functions. After calling & executing the functions, you will be provided with "
    "function results within <tool_response> </tool_response> XML tags. Here are the "
    "available tools:\n<tools>\n{tools}\n</tools>\nFor each function call return a JSON "
    "object, with the following pydantic model json schema for each:\n{{'title': "
    "'FunctionCall', 'type': 'object', 'properties': {{'name': {{'title': 'Name', 'type': "
    "'string'}}, 'arguments': {{'title': 'Arguments', 'type': 'object'}}}}, 'required': "
    "['name', 'arguments']}}\nEach function call should be enclosed within <tool_call> "
    "</tool_call> XML tags.\nExample:\n<tool_call>\n{{'name': <function-name>,'arguments': "
    "<args-dict>}}\n</tool_call>"
)


def _json_type(v: Any) -> str:
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, int | float):
        return "number"
    if isinstance(v, dict):
        return "object"
    if isinstance(v, list):
        return "array"
    return "string"


def tools_of(steps: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The tools a run called, in order of first use, with the argument names it passed. The
    cloud has no tool schemas, so this is what the run shows, not the healer's full list."""
    tools: dict[str, dict[str, Any]] = {}
    for s in steps:
        name = str(s.get("tool") or "unknown")
        t = tools.setdefault(
            name,
            {
                "name": name,
                "description": f"Heal-agent tool `{name}` (as called in this run)",
                "parameters": {"type": "object", "properties": {}},
                "required": None,
            },
        )
        for k, v in sorted((s.get("args") or {}).items()):
            t["parameters"]["properties"].setdefault(str(k), {"type": _json_type(v)})
    return list(tools.values())


def _think(thought: str) -> str:
    thought = (thought or "").strip()
    return f"<think>\n{thought}\n</think>\n" if thought else "<think>\n</think>\n"


def _dumps(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False)


def trajectory(run: Mapping[str, Any]) -> dict[str, Any]:
    """One labelled run (`LabelledRun.to_dict()`) → one Hermes trajectory row.

    `completed` is the verdict (pass → true, fail → false), never the agent's own word; the
    last `gpt` turn is the agent's own final report, so the label never leaks into the text.
    """
    steps = list(run.get("steps") or [])
    turns: list[dict[str, str]] = [
        {"from": "system", "value": HERMES_SYSTEM.format(tools=_dumps(tools_of(steps)))},
        {
            "from": "human",
            "value": run.get("problem")
            or f"{run.get('device_id')} reported {run.get('kind')}. Find the cause and fix it.",
        },
    ]
    for i, s in enumerate(steps, 1):
        name = str(s.get("tool") or "unknown")
        call = {"name": name, "arguments": dict(s.get("args") or {})}
        turns.append(
            {
                "from": "gpt",
                "value": _think(str(s.get("thought") or ""))
                + f"<tool_call>\n{_dumps(call)}\n</tool_call>",
            }
        )
        result = {"tool_call_id": f"call_{i}", "name": name, "content": str(s.get("effect") or "")}
        turns.append(
            {"from": "tool", "value": f"<tool_response>\n{_dumps(result)}\n</tool_response>"}
        )
    final = str(run.get("final") or "").strip() or "(the agent wrote no final report)"
    turns.append({"from": "gpt", "value": _think("") + final})
    return {
        "conversations": turns,
        "timestamp": run.get("started_at"),
        "model": run.get("model") or "unknown",
        "completed": run.get("verdict") == "pass",
        "metadata": {
            "labelled_run": run.get("id"),
            "run_id": run.get("run_id"),
            "incident_id": run.get("incident_id"),
            "device_id": run.get("device_id"),
            "device_class": run.get("device_class"),
            "device_kind": run.get("device_kind"),
            "sim_fleet": run.get("device_kind") == "sim",
            "kind": run.get("kind"),
            "verdict": run.get("verdict"),
            "verifier": run.get("verifier"),
            "versions": dict(run.get("versions") or {}),
            "source": run.get("source"),
        },
    }


def _row(cells: Iterable[Any]) -> str:
    return "| " + " | ".join(str(c).replace("|", "\\|") for c in cells) + " |"


VERDICT_RULES = (
    (
        "excluded",
        "a rehearsal (`agent_runs.sim`), a run still going, or hardware (work order, park)",
    ),
    (
        "fail",
        "the bench check failed; the agent left it broken; the lock-in rolled back; the device "
        "reverted the overlay; the gate rolled back a candidate built from it; the same kind "
        "came back on the device inside the window",
    ),
    (
        "pass",
        "the bench check passed (`score.json`); the gate promoted a candidate built from it; "
        "the overlay held on the device (mark-good timer)",
    ),
    ("unverified", "everything else, including a device that says fixed with nothing behind it"),
)


def dataset_card(name: str, n: int, snapshot_sha256: str, runs: list[Mapping[str, Any]]) -> str:
    """`DATASET_CARD.md` for a trajectories export: what's in it, how each row was labelled,
    what was left out and why, and a SIM FLEET notice when any run is from a sim device."""
    total = {v: sum(1 for r in runs if r.get("verdict") == v) for v in CARD_VERDICTS}
    sims = [r for r in runs if r.get("device_kind") == "sim"]
    out = [f"# {name} v{n}: heal trajectories", ""]
    if sims:
        out += [
            f"> **SIM FLEET.** {len(sims)} of {len(runs)} runs come from simulated devices "
            '(device kind `sim`). Their rows say `"sim_fleet": true`; don\'t read them as '
            "real-world behaviour.",
            "",
        ]
    out += [
        f"- Snapshot sha256: `{snapshot_sha256}`",
        f"- `trajectories.jsonl`: {total['pass'] + total['fail']} rows in Hermes Agent's "
        "trajectory format (`conversations`, `timestamp`, `model`, `completed`, `metadata`).",
        "- **`completed` is a verifier's verdict** (`pass` → true, `fail` → false), never the "
        "agent's own opinion of its run.",
        "",
        "## Verdicts",
        "",
        _row(["Verdict", "Runs", "In trajectories.jsonl"]),
        _row(["---", "---:", "---"]),
        _row(["pass", total["pass"], "yes, `completed: true`"]),
        _row(["fail", total["fail"], "yes, `completed: false`"]),
        _row(["unverified", total["unverified"], "no: nothing independent checked it"]),
        _row(["excluded", total["excluded"], "no: rehearsal, still running, or hardware"]),
        "",
        "## By device",
        "",
        _row(["Device", "Class", "Kind", *CARD_VERDICTS]),
        _row(["---", "---", "---", *(["---:"] * len(CARD_VERDICTS))]),
    ]
    devices: dict[tuple, dict[str, int]] = {}
    kinds: dict[str, dict[str, int]] = {}
    for r in runs:
        key = (str(r.get("device_id")), str(r.get("device_class")), str(r.get("device_kind")))
        devices.setdefault(key, dict.fromkeys(CARD_VERDICTS, 0))[r["verdict"]] += 1
        kinds.setdefault(str(r.get("kind")), dict.fromkeys(CARD_VERDICTS, 0))[r["verdict"]] += 1
    for (dev, cls, kind), c in sorted(devices.items()):
        out.append(_row([dev, cls, "sim (SIM)" if kind == "sim" else kind, *c.values()]))
    out += ["", "## By fault kind", "", _row(["Fault kind", *CARD_VERDICTS])]
    out.append(_row(["---", *(["---:"] * len(CARD_VERDICTS))]))
    for kind, c in sorted(kinds.items()):
        out.append(_row([kind, *c.values()]))
    out += ["", "## How a verdict is given (first matching row wins)", ""]
    out += [_row(["Verdict", "When"]), _row(["---", "---"])]
    out += [_row(rule) for rule in VERDICT_RULES]
    out += ["", "## Not exported", ""]
    left = [r for r in runs if r.get("verdict") not in LABELLED_VERDICTS]
    if not left:
        out.append("Nothing: every run in the snapshot has a verifier's verdict.")
    for r in left:
        out.append(f"- `{r.get('id')}` ({r.get('kind')}, {r['verdict']}): {r.get('verifier')}")
    out += [
        "",
        "## Redaction",
        "",
        "Every string in every row went through `redact()`: secret-named keys, bearer tokens, "
        "API keys, URL passwords, home-directory paths and email addresses are replaced.",
        "",
    ]
    return redact_text("\n".join(out))
