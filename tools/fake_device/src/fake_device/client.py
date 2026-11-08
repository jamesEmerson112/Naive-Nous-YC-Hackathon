"""DeviceClient: one method per device-facing route (APIs §3), using the contract models.

It takes any `httpx.Client` whose `base_url` points at the cloud, so the same code runs
against the real API, FastAPI's `TestClient` (an `httpx.Client`) and `httpx.MockTransport`.
It does no retrying and no queueing: errors surface as `httpx.TransportError` (cloud
unreachable) or `httpx.HTTPStatusError` (the cloud answered with 4xx/5xx), and the outbox
decides what to do with them. Downloads are hash-checked here and raise `HashMismatch`;
a download whose hash is fine but whose content fails validation raises `InvalidPackage`.

One exception: `ask_doctor` (Fleet Doctor L1) never raises. The healer calls it mid-heal
and must carry on without an answer, so any error, non-200 or timeout returns None.

Standard library + httpx + romulus-contracts only, Python 3.11+, so it can be copied into
the Pi app as is.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import tarfile
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError

from romulus_contracts import (
    CONTRACT_VERSION,
    SHA256_HEADER,
    CheckIn,
    CheckInResponse,
    DoctorAdvice,
    DoctorAsk,
    FirmwareManifest,
    HarnessMeta,
    IncidentAccepted,
    IncidentReport,
    InstallResult,
    OverlayStatus,
    Proposal,
    ProposalResult,
    RecordingAccepted,
    RecordingMeta,
    manifest_sha256,
)

log = logging.getLogger(__name__)


class HashMismatch(Exception):
    """A pulled manifest, package or proposal doesn't hash to the sha256 the cloud sent."""

    def __init__(self, what: str, expected: str, actual: str) -> None:
        super().__init__(f"{what}: sha256 mismatch: expected {expected}, got {actual}")
        self.what = what
        self.expected = expected
        self.actual = actual


class InvalidPackage(Exception):
    """The hash matched, but the manifest or package failed validation (report `invalid`)."""


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def body(model: BaseModel) -> dict[str, Any]:
    """The JSON body for a device-sent model, with `contract_version` on every request.

    APIs §2.3 requires it on check-ins; on other requests it rides as an extra field
    (contracts README, proposed for I0).
    """
    data = model.model_dump(mode="json")
    if data.get("contract_version") is None:
        data["contract_version"] = CONTRACT_VERSION
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
        actual = manifest_sha256(data.get("manifest"))
        expected = str(data.get("sha256", "")).lower()
        if actual != expected:
            raise HashMismatch(f"firmware {version}", expected, actual)
        try:
            manifest = FirmwareManifest.model_validate(data["manifest"])
        except ValidationError as exc:
            raise InvalidPackage(f"firmware {version}: {exc}") from exc
        if manifest.version != version:
            raise InvalidPackage(f"firmware {version}: manifest says version {manifest.version}")
        return manifest

    def _pull_package(self, what: str, path: str) -> bytes:
        resp = self._send("GET", path)
        expected = resp.headers.get(SHA256_HEADER, "").lower()
        actual = sha256_hex(resp.content)
        if actual != expected:
            raise HashMismatch(what, expected, actual)
        return resp.content

    # 3.3
    def pull_harness(self, version: int) -> bytes:
        """The harness `.tar.gz`, checked against its `X-Sha256` header and its `harness.json`."""
        data = self._pull_package(f"harness {version}", f"/v1/harness/{version}/package")
        try:
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as tar:
                member = tar.extractfile("harness.json")
                meta = HarnessMeta.model_validate(json.load(member))
        except (tarfile.TarError, KeyError, ValueError, TypeError) as exc:
            raise InvalidPackage(f"harness {version}: no valid harness.json ({exc})") from exc
        if meta.version != version:
            raise InvalidPackage(f"harness {version}: harness.json says version {meta.version}")
        return data

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

    # 3.8 (Fleet Doctor L1)
    def ask_doctor(self, ask: dict[str, Any], *, timeout_s: float) -> dict[str, Any] | None:
        """Ask the Fleet Doctor for hypotheses mid-heal. A direct call, not the outbox.

        `ask` is a `DoctorAsk` as a dict. Returns the `DoctorAdvice` as a dict
        (`{"ask_id", "hypotheses": [{"claim", "how_to_check"}], "model"}`), or **None on any
        error**: unreachable, timeout, non-200 (503 doctor off, 409 frozen, 429 cap, 5xx),
        or a body that isn't advice. The caller's circuit breaker trips on None.

        `timeout_s` is httpx's per-phase timeout (connect, write, read), so a slow LLM
        answer is cut off by the read timeout. Pass the ask's `budget_s`.
        """
        path = f"/v1/devices/{self.device_id}/doctor/ask"
        try:
            payload = body(DoctorAsk.model_validate(ask))
            resp = self.http.request("POST", path, json=payload, timeout=timeout_s)
            if resp.status_code != 200:
                log.info("doctor ask: no answer (%s %s)", resp.status_code, resp.text[:200])
                return None
            return DoctorAdvice.model_validate(resp.json()).model_dump(mode="json")
        except Exception as exc:  # any error at all means "no answer"
            log.info("doctor ask: no answer (%s: %s)", type(exc).__name__, exc)
            return None

    # 3.9 (Fleet Doctor L2)
    def get_proposal(
        self, proposal_id: str, *, expected_sha256: str | None = None
    ) -> tuple[dict[str, Any], str]:
        """Pull a proposal and check its hash before anything trusts it. Returns (body, sha256).

        - `sha256(response bytes)` must equal the response's `X-Sha256` header, as for
          harness and code packages, and also `expected_sha256` (the check-in pointer's
          `sha256`) when given. Otherwise `HashMismatch`.
        - A body that hashes fine but isn't a valid `Proposal` for this id: `InvalidPackage`.
        - The cloud answering 404 or 410 (gone, expired) raises `httpx.HTTPStatusError`.
        """
        resp = self._send("GET", f"/v1/devices/{self.device_id}/proposals/{proposal_id}")
        actual = sha256_hex(resp.content)
        header = resp.headers.get(SHA256_HEADER, "").lower()
        if actual != header:
            raise HashMismatch(f"proposal {proposal_id}", header, actual)
        if expected_sha256 is not None and actual != expected_sha256.lower():
            what = f"proposal {proposal_id} (check-in pointer)"
            raise HashMismatch(what, expected_sha256, actual)
        try:
            data = json.loads(resp.content)
            proposal = Proposal.model_validate(data)
        except ValueError as exc:  # bad JSON or a ValidationError
            raise InvalidPackage(f"proposal {proposal_id}: {exc}") from exc
        if proposal.proposal_id != proposal_id:
            raise InvalidPackage(f"proposal {proposal_id}: body is {proposal.proposal_id}")
        return data, actual

    # 3.10 (Fleet Doctor L2), sent from the outbox
    def report_proposal_result(self, proposal_id: str, result: ProposalResult) -> None:
        path = f"/v1/devices/{self.device_id}/proposals/{proposal_id}/result"
        self._send("POST", path, json=body(result))
