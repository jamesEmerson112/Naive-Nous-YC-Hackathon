"""DeviceClient: one method per device-facing route (APIs §3), using the contract models.

It takes any `httpx.Client` whose `base_url` points at the cloud, so the same code runs
against the real API, FastAPI's `TestClient` (an `httpx.Client`) and `httpx.MockTransport`.
It does no retrying and no queueing: errors surface as `httpx.TransportError` (cloud
unreachable) or `httpx.HTTPStatusError` (the cloud answered with 4xx/5xx), and the outbox
decides what to do with them. Downloads are hash-checked here and raise `HashMismatch`.

Standard library + httpx + romulus-contracts only, Python 3.11+, so it can be copied into
the Pi app as is.
"""

from __future__ import annotations

import hashlib
from typing import Any

import httpx
from pydantic import BaseModel
from romulus_contracts import (
    CONTRACT_VERSION,
    SHA256_HEADER,
    CheckIn,
    CheckInResponse,
    FirmwareManifest,
    FirmwareResponse,
    IncidentAccepted,
    IncidentReport,
    InstallResult,
    OverlayStatus,
    RecordingAccepted,
    RecordingMeta,
    manifest_sha256,
)


class HashMismatch(Exception):
    """A pulled firmware manifest or package doesn't hash to the sha256 the cloud sent."""

    def __init__(self, what: str, expected: str, actual: str) -> None:
        super().__init__(f"{what}: sha256 mismatch: expected {expected}, got {actual}")
        self.what = what
        self.expected = expected
        self.actual = actual


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def body(model: BaseModel) -> dict[str, Any]:
    """The JSON body for a device-sent model, with `contract_version` on every request.

    APIs §2.3 requires it on check-ins; on other requests it rides as an extra field
    (contracts README, proposed for I0).
    """
    data = model.model_dump(mode="json")
    data.setdefault("contract_version", CONTRACT_VERSION)
    return data


class DeviceClient:
    """Talks to the cloud as one device. Thin: one method per route, no state."""

    def __init__(self, http: httpx.Client, device_id: str) -> None:
        self.http = http
        self.device_id = device_id

    def _send(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        resp = self.http.request(method, path, **kwargs)
        resp.raise_for_status()
        return resp

    # 3.1
    def checkin(self, checkin: CheckIn) -> CheckInResponse:
        resp = self._send("POST", f"/v1/devices/{self.device_id}/checkin", json=body(checkin))
        return CheckInResponse.model_validate(resp.json())

    # 3.2
    def pull_firmware(self, version: int) -> FirmwareManifest:
        """Fetch a manifest and check its hash over the JSON *as received* (APIs §3.2)."""
        data = self._send("GET", f"/v1/firmware/{version}").json()
        actual = manifest_sha256(data["manifest"])
        expected = str(data.get("sha256", "")).lower()
        if actual != expected:
            raise HashMismatch(f"firmware {version}", expected, actual)
        return FirmwareResponse.model_validate(data).manifest

    def _pull_package(self, what: str, path: str) -> bytes:
        resp = self._send("GET", path)
        expected = resp.headers.get(SHA256_HEADER, "").lower()
        actual = sha256_hex(resp.content)
        if actual != expected:
            raise HashMismatch(what, expected, actual)
        return resp.content

    # 3.3
    def pull_harness(self, version: int) -> bytes:
        """The harness `.tar.gz`, checked against its `X-Sha256` header."""
        return self._pull_package(f"harness {version}", f"/v1/harness/{version}/package")

    # 3.3c
    def pull_code(self, rev: str) -> bytes:
        """The code package (`.tar.gz` of `device/`), checked against its `X-Sha256` header."""
        return self._pull_package(f"code {rev}", f"/v1/code/{rev}/package")

    # 3.4
    def report_install(self, result: InstallResult) -> None:
        self._send("POST", f"/v1/devices/{self.device_id}/installs", json=body(result))

    # 3.5
    def report_incident(self, incident: IncidentReport) -> IncidentAccepted:
        """`201` when new, `200` when the cloud already has this `incident_id`."""
        resp = self._send("POST", f"/v1/devices/{self.device_id}/incidents", json=body(incident))
        return IncidentAccepted.model_validate(resp.json())

    # 3.6
    def report_overlay(self, overlay_id: str, status: OverlayStatus) -> None:
        path = f"/v1/devices/{self.device_id}/overlays/{overlay_id}"
        self._send("POST", path, json=body(status))

    # 3.7a
    def create_recording(self, meta: RecordingMeta) -> RecordingAccepted:
        resp = self._send("POST", f"/v1/devices/{self.device_id}/recordings", json=body(meta))
        return RecordingAccepted.model_validate(resp.json())

    # 3.7b
    def upload_blob(self, recording_id: str, data: bytes) -> None:
        headers = {SHA256_HEADER: sha256_hex(data), "Content-Type": "application/octet-stream"}
        self._send("PUT", f"/v1/recordings/{recording_id}/blob", content=data, headers=headers)

    def upload_recording(self, meta: RecordingMeta, data: bytes) -> RecordingAccepted:
        """Metadata first, then the blob only if the cloud doesn't have that sha256 yet."""
        accepted = self.create_recording(meta)
        if accepted.upload == "needed":
            self.upload_blob(str(meta.recording_id), data)
        return accepted
