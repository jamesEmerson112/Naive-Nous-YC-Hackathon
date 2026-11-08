"""FakeCloud: an in-memory stand-in for the cloud's device API (APIs §3), for tests.

It answers through `httpx.MockTransport`, so no sockets: `FakeCloud().http()` returns an
`httpx.Client` you can hand to `DeviceClient`. It validates every body with the contract
models and records what arrived, which is all the fake device's tests need. The device
side (the Pi app) can use it the same way to test its visor and outbox without the cloud.

Knobs: `offline` (every request raises `httpx.ConnectError`), `fail_status` (every
request gets that status), `corrupt` (paths whose advertised sha256 is wrong).
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import re
import tarfile
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError

from romulus_contracts import (
    SHA256_HEADER,
    CheckIn,
    IncidentReport,
    InstallResult,
    OverlayStatus,
    RecordingMeta,
    firmware_response,
    is_supported,
    manifest_sha256,
)

_DEV = r"/v1/devices/(?P<device>[^/]+)"
ROUTES: list[tuple[str, re.Pattern[str], str]] = [
    ("POST", re.compile(_DEV + r"/checkin"), "checkin"),
    ("GET", re.compile(r"/v1/firmware/(?P<version>\d+)"), "firmware"),
    ("GET", re.compile(r"/v1/harness/(?P<version>\d+)/package"), "harness"),
    ("GET", re.compile(r"/v1/code/(?P<rev>[^/]+)/package"), "code"),
    ("POST", re.compile(_DEV + r"/installs"), "installs"),
    ("POST", re.compile(_DEV + r"/incidents"), "incidents"),
    ("POST", re.compile(_DEV + r"/overlays/(?P<overlay>[^/]+)"), "overlays"),
    ("POST", re.compile(_DEV + r"/recordings"), "recordings"),
    ("PUT", re.compile(r"/v1/recordings/(?P<recording>[^/]+)/blob"), "blob"),
]


V1_MANIFEST: dict[str, Any] = {
    "version": 1,
    "device_class": "so101",
    "code_rev": "c0de001",
    "rules": {
        "camera_occluded": {"signal": "brightness", "below": 0.2, "action": "safe_pose"},
        "gripper_stall": {"signal": "gripper_current_ma", "above": 900, "action": "backoff_retry"},
    },
    "params": {"max_speed": 0.6},
    "policy": {"name": "pick_cube", "rev": "hf:org/act@3f9e"},
}
"""Firmware v1, as in packages/contracts/examples/firmware_response.json."""


def tarball(files: dict[str, bytes]) -> bytes:
    """A deterministic `.tar.gz` of `{path: content}` (same bytes every time)."""
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as tar:
        for name, data in sorted(files.items()):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return gzip.compress(raw.getvalue(), mtime=0)


def _error(status: int, code: str, detail: str = "") -> httpx.Response:
    return httpx.Response(status, json={"error": code, "detail": detail})


class FakeCloud:
    def __init__(self) -> None:
        self.firmware: dict[int, dict[str, Any]] = {}
        self.harness: dict[int, bytes] = {}
        self.code: dict[str, bytes] = {}
        self.assigned: dict[str, dict[str, int | None]] = {}
        self.frozen = False
        self.drop_overlay: set[str] = set()  # devices told once to drop their overlay

        self.requests: list[tuple[str, str]] = []  # (method, path) that reached the server
        self.checkins: dict[str, list[dict[str, Any]]] = {}
        self.installs: list[tuple[str, dict[str, Any]]] = []
        self.incidents: dict[str, dict[str, Any]] = {}
        self.overlays: list[tuple[str, str, dict[str, Any]]] = []
        self.recordings: dict[str, dict[str, Any]] = {}
        self.blobs: dict[str, bytes] = {}

        self.offline = False
        self.fail_status: int | None = None
        self.corrupt: set[str] = set()

    # -- setup ------------------------------------------------------------------------

    @classmethod
    def seeded(cls, devices: tuple[str, ...] = ("sim-1", "sim-2", "sim-3")) -> FakeCloud:
        """Like `make seed`: firmware v1, harness v1 and code `c0de001`, assigned to `devices`."""
        cloud = cls()
        cloud.add_firmware(V1_MANIFEST)
        harness_json = b'{"version": 1, "framework": "mini-swe-agent", "knowledge_dir": "skills/"}'
        cloud.add_harness(
            1, tarball({"harness.json": harness_json, "skills/README.md": b"# skills"})
        )
        cloud.add_code("c0de001", tarball({"device/README.md": b"fake device code c0de001"}))
        for device in devices:
            cloud.assign(device, firmware=1, harness=1)
        return cloud

    def add_firmware(self, manifest: dict[str, Any]) -> None:
        self.firmware[manifest["version"]] = manifest

    def add_harness(self, version: int, data: bytes) -> None:
        self.harness[version] = data

    def add_code(self, rev: str, data: bytes) -> None:
        self.code[rev] = data

    def assign(self, device_id: str, firmware: int | None = None, harness: int | None = None):
        self.assigned[device_id] = {"firmware": firmware, "harness": harness}

    def http(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handle), base_url="http://cloud")

    def paths(self, method: str | None = None) -> list[str]:
        """Paths of the requests that reached the server, in order (optionally one method)."""
        return [p for m, p in self.requests if method is None or m == method]

    # -- the transport ----------------------------------------------------------------

    def handle(self, request: httpx.Request) -> httpx.Response:
        if self.offline:
            raise httpx.ConnectError("fake cloud is offline", request=request)
        path = request.url.path
        self.requests.append((request.method, path))
        if self.fail_status is not None:
            return _error(self.fail_status, "fake_failure", "FakeCloud.fail_status is set")
        for method, pattern, name in ROUTES:
            match = pattern.fullmatch(path)
            if match and method == request.method:
                try:
                    return getattr(self, f"_{name}")(request, **match.groupdict())
                except ValidationError as exc:
                    return _error(422, "invalid_payload", str(exc))
        return _error(404, "not_found", f"no route {request.method} {path}")

    @staticmethod
    def _parse(request: httpx.Request, model: type[BaseModel]) -> tuple[dict[str, Any], Any]:
        raw = json.loads(request.content)
        return raw, model.model_validate(raw)

    def _checkin(self, request, device):
        raw, _ = self._parse(request, CheckIn)
        if not is_supported(raw.get("contract_version")):
            return _error(400, "unsupported_contract_version", str(raw.get("contract_version")))
        self.checkins.setdefault(device, []).append(raw)
        assigned = self.assigned.get(device, {"firmware": None, "harness": None})
        drop = device in self.drop_overlay
        self.drop_overlay.discard(device)
        return httpx.Response(
            200, json={"assigned": assigned, "drop_overlay": drop, "frozen": self.frozen}
        )

    def _firmware(self, request, version):
        manifest = self.firmware.get(int(version))
        if manifest is None:
            return _error(404, "not_found", f"no firmware {version}")
        try:
            served = firmware_response(manifest)  # what the real cloud serves
        except ValidationError:
            # A manifest this contracts version can't parse (e.g. a newer rule shape),
            # served with a correct hash: the device must report `invalid`.
            served = {"manifest": manifest, "sha256": manifest_sha256(manifest)}
        if request.url.path in self.corrupt:
            served["sha256"] = "0" * 64
        return httpx.Response(200, json=served)

    def _package(self, request, data: bytes | None):
        if data is None:
            return _error(404, "not_found", request.url.path)
        sha = hashlib.sha256(data).hexdigest()
        if request.url.path in self.corrupt:
            sha = "0" * 64
        return httpx.Response(200, content=data, headers={SHA256_HEADER: sha})

    def _harness(self, request, version):
        return self._package(request, self.harness.get(int(version)))

    def _code(self, request, rev):
        return self._package(request, self.code.get(rev))

    def _installs(self, request, device):
        raw, _ = self._parse(request, InstallResult)
        self.installs.append((device, raw))
        return httpx.Response(204)

    def _incidents(self, request, device):
        raw, incident = self._parse(request, IncidentReport)
        key = str(incident.incident_id)
        if key in self.incidents:
            return httpx.Response(200, json={"incident_id": key})
        self.incidents[key] = {"device": device, **raw}
        return httpx.Response(201, json={"incident_id": key})

    def _overlays(self, request, device, overlay):
        raw, _ = self._parse(request, OverlayStatus)
        self.overlays.append((device, overlay, raw))
        return httpx.Response(204)

    def _recordings(self, request, device):
        raw, meta = self._parse(request, RecordingMeta)
        key = str(meta.recording_id)
        self.recordings.setdefault(key, {"device": device, **raw})
        upload = "exists" if meta.sha256 in self.blobs else "needed"
        return httpx.Response(201, json={"recording_id": key, "upload": upload})

    def _blob(self, request, recording):
        meta = self.recordings.get(recording)
        if meta is None:
            return _error(404, "not_found", f"no recording {recording}")
        actual = hashlib.sha256(request.content).hexdigest()
        if actual != request.headers.get(SHA256_HEADER) or actual != meta["sha256"]:
            return _error(409, "sha256_mismatch", f"got {actual}")
        self.blobs[actual] = request.content
        return httpx.Response(204)
