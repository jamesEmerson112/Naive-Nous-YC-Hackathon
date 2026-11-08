"""Datasets, snapshots and exports as pure functions (telemetry §5–6, §10; flow F8).

"Narrow loop, general data" (D16): every failure becomes training data. `services/datasets.py`
loads rows into the plain dicts these functions take, and writes what they return.

- `parse_filter(raw)`: validate a dataset's saved filter (unknown keys are refused, so a
  typo can't silently match everything).
- `matches_recording(f, rec, labels)` / `matches_incident(f, inc)`: does a row match.
- `snapshot_manifest(...)`: the canonical JSON and sha256 that pin a dataset version.
- `regression_case(inc)`: the replay case one resolved incident becomes, or None.
- `sharegpt(lines)`: an agent-trace JSONL file as Hermes-style `conversations`.
- `redact(value)`: strip obvious secrets, home-directory paths and emails (telemetry §10).
- `canonical(obj)` / `jsonl(rows)`: byte-identical output for identical input, so the
  same snapshot always produces the same export file (F8).

Pure: imports only the standard library. Time arrives as values; nothing reads a clock.
"""

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

PURPOSES = ("firmware", "harness", "policy")
FORMATS = ("regression_corpus", "traces_jsonl")
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
    recordings: Iterable[tuple[str, str]],
    incident_ids: Iterable[str],
) -> tuple[dict[str, Any], str]:
    """The frozen contents of a dataset version and their sha256.

    `recordings` is (id, sha256) pairs; both lists are sorted, so the hash doesn't depend on
    the order rows were read in.
    """
    manifest = {
        "dataset_id": dataset_id,
        "n": n,
        "filter": dict(filter_),
        "recordings": [{"id": rid, "sha256": sha} for rid, sha in sorted(set(recordings))],
        "incident_ids": sorted(set(incident_ids)),
    }
    return manifest, sha256(canonical(manifest))


# --------------------------------------------------------------------------- regression corpus


def regression_case(inc: Mapping[str, Any]) -> dict[str, Any] | None:
    """The replay case a past incident becomes (telemetry §6.1), or None.

    A case needs a resolved incident, the action that fixed it and some signals to replay.
    Incidents fixed by a code patch are left out: the rules can't fire `restart_app`, so
    they would fail every firmware candidate; code candidates replay the device tests (M8)
    *(inferred)*.
    """
    action = (inc.get("resolving_action") or "").strip()
    signals = inc.get("signals") or {}
    if not inc.get("resolved") or not action or not signals or inc.get("code_patch"):
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


_SECRETS = [
    # key = value / "key": "value" for names that usually hold secrets
    (
        re.compile(
            r"(?i)\b([\w-]*(?:api[_-]?key|token|secret|password|passwd)[\w-]*)"
            r"(\"?\s*[:=]\s*\"?)([^\s\"',}]+)"
        ),
        r"\1\2[REDACTED]",
    ),
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}"), r"\1 [REDACTED]"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"), "[REDACTED]"),
    (re.compile(r"\b(?:hf|gh[pousr]|xox[abp])_[A-Za-z0-9_-]{16,}"), "[REDACTED]"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[REDACTED]"),
    (
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
        "[REDACTED]",
    ),
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
    """Every string inside `value` (dicts, lists, nested) with obvious secrets removed."""
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {k: redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


# --------------------------------------------------------------------------- files


def jsonl(rows: Iterable[Any]) -> bytes:
    return b"".join(canonical(r) + b"\n" for r in rows)
