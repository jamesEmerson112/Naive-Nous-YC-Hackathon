"""Firmware manifest (APIs §2.1, D3), harness metadata (§2.2, D4) and the firmware pull (§3.2)."""

import hashlib
import json
from collections.abc import Mapping
from pathlib import PurePosixPath
from typing import Any

from pydantic import field_validator

from romulus_contracts.common import CloudModel, DeviceModel, Sha256, Version
from romulus_contracts.rules import validate_rules


class Policy(DeviceModel):
    """The policy the firmware runs: `{"name": "pick_cube", "rev": "hf:org/act@3f9e"}`."""

    name: str
    rev: str


class FirmwareManifest(DeviceModel):
    """One immutable firmware version: code version + reflex rules + params + policy (D3).

    `rules` are validated with `rules.validate_rules`, so any manifest that parses can be
    passed straight to `rules.evaluate`. `code_rev` names a code version in the platform's
    registry (pull it from `GET /v1/code/{rev}/package`), not a git ref (APIs §2.10).
    """

    version: Version
    device_class: str
    code_rev: str
    rules: dict[str, dict[str, Any]] = {}
    params: dict[str, Any] = {}
    policy: Policy | None = None

    @field_validator("rules")
    @classmethod
    def _rules_are_valid(cls, rules: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
        problems = validate_rules(rules)
        if problems:
            raise ValueError("; ".join(problems))
        return rules


def canonical_json(value: Any) -> bytes:
    """The canonical JSON bytes both sides hash: sorted keys, no whitespace, UTF-8, no NaN.

    Note that 900 and 900.0 are different JSON and hash differently: hash the object
    exactly as sent or received.
    """
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def manifest_sha256(manifest: Mapping[str, Any] | FirmwareManifest) -> str:
    """sha256 of a manifest's canonical JSON (APIs §3.2).

    On the device, pass the `manifest` object from the response **as parsed JSON**
    (a dict), not a re-dumped model, and compare with the response's `sha256`.
    A `FirmwareManifest` is dumped with `model_dump(mode="json")` first, which includes
    defaults and extra fields.
    """
    if isinstance(manifest, FirmwareManifest):
        manifest = manifest.model_dump(mode="json")
    return hashlib.sha256(canonical_json(manifest)).hexdigest()


class FirmwareResponse(CloudModel):
    """Response of `GET /v1/firmware/{version}` (APIs §3.2)."""

    manifest: FirmwareManifest
    sha256: Sha256


class HarnessMeta(DeviceModel):
    """`harness.json` at the root of a harness `.tar.gz` (APIs §2.2, D4).

    The tarball's other contents belong to the device team. The cloud's batch job only
    adds markdown files under `knowledge_dir`, so that path must stay inside the package.
    """

    version: Version
    framework: str
    knowledge_dir: str = "skills/"

    @field_validator("knowledge_dir")
    @classmethod
    def _knowledge_dir_is_relative(cls, value: str) -> str:
        path = PurePosixPath(value)
        if not value or path.is_absolute() or ".." in path.parts or "\\" in value:
            raise ValueError(
                "knowledge_dir must be a relative path inside the package, without '..'"
            )
        return value
