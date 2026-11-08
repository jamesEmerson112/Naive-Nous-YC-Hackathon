"""Print the JSON Schema of the device-facing contract models.

    python -m romulus_contracts.schema             # every model
    python -m romulus_contracts.schema CheckIn     # one model

Useful on the device side to validate payloads without this package, or to constrain
a small model's output (llama-server accepts a JSON Schema).
"""

import json
import sys

from pydantic import BaseModel

from romulus_contracts.common import ErrorResponse
from romulus_contracts.device import CheckIn, CheckInResponse, InstallResult
from romulus_contracts.incident import IncidentAccepted, IncidentReport, OverlayStatus
from romulus_contracts.manifest import FirmwareManifest, FirmwareResponse, HarnessMeta
from romulus_contracts.telemetry import RecordingAccepted, RecordingMeta
from romulus_contracts.version import CONTRACT_VERSION

MODELS: dict[str, type[BaseModel]] = {
    # device -> cloud
    "CheckIn": CheckIn,
    "InstallResult": InstallResult,
    "IncidentReport": IncidentReport,
    "OverlayStatus": OverlayStatus,
    "RecordingMeta": RecordingMeta,
    # cloud -> device
    "CheckInResponse": CheckInResponse,
    "FirmwareResponse": FirmwareResponse,
    "IncidentAccepted": IncidentAccepted,
    "RecordingAccepted": RecordingAccepted,
    "ErrorResponse": ErrorResponse,
    # artifacts the device pulls
    "FirmwareManifest": FirmwareManifest,
    "HarnessMeta": HarnessMeta,
}


def schemas(names: list[str] | None = None) -> dict:
    """`{"contract_version": ..., "models": {name: JSON Schema}}` for the named models."""
    selected = names or list(MODELS)
    unknown = [n for n in selected if n not in MODELS]
    if unknown:
        raise KeyError(f"unknown model(s) {unknown}; known: {sorted(MODELS)}")
    return {
        "contract_version": CONTRACT_VERSION,
        "models": {n: MODELS[n].model_json_schema() for n in selected},
    }


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    try:
        out = schemas(args)
    except KeyError as e:
        print(e.args[0], file=sys.stderr)
        return 2
    print(json.dumps(out, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
