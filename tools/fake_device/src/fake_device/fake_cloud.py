"""FakeCloud: an in-memory stand-in for the cloud's device API (APIs §3), for tests.

It answers through `httpx.MockTransport`, so no sockets: `FakeCloud().http()` returns an
`httpx.Client` you can hand to `DeviceClient`. It validates every body with the contract
models and records what arrived, which is all the fake device's tests need. The device
side (the Pi app) can use it the same way to test its visor and outbox without the cloud.

Knobs: `offline` (every request raises `httpx.ConnectError`), `fail_status` (every
request gets that status), `corrupt` (paths whose advertised sha256 is wrong),
`doctor_timeout` (a Doctor ask raises `httpx.ReadTimeout`).

Fleet Doctor routes (3.8-3.10), with the real cloud's errors: an ask gets `advice`
(canned, the contracts example), or 409 while `frozen`. A proposal set up with
`offer(device_id, proposal)` is pointed at by that device's check-ins (with its sha256)
until a result for it arrives or it expires by `clock`, and served with `X-Sha256` like
the real cloud (`proposal_body_and_sha256`); after a result or expiry the GET is 410, and
while `frozen` it is 409.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import re
import tarfile
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError

from fake_device.scenarios import D1_CODE_PATCH
from romulus_contracts import (
    SHA256_HEADER,
    AgentRunReport,
    CheckIn,
    DoctorAsk,
    IncidentReport,
    InstallResult,
    OverlayStatus,
    Proposal,
    ProposalResult,
    RecordingMeta,
    firmware_response,
    is_supported,
    manifest_sha256,
    proposal_body_and_sha256,
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
    ("POST", re.compile(_DEV + r"/doctor/ask"), "doctor_ask"),
    ("GET", re.compile(_DEV + r"/proposals/(?P<proposal>[^/]+)"), "proposal"),
    ("POST", re.compile(_DEV + r"/proposals/(?P<proposal>[^/]+)/result"), "proposal_result"),
    ("PUT", re.compile(_DEV + r"/agent-runs/(?P<run>[^/]+)"), "agent_run"),
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

CANNED_ADVICE: dict[str, Any] = {
    "ask_id": "ask_5b9e2f7a1c3d4e8f9a0b1c2d3e4f5a6b",
    "hypotheses": [
        {
            "claim": "The threshold isn't the bug: SPEC.md says never open the gripper because "
            "of the ultrasonic reading while holding, so any proximity release in the HOLDING "
            "branch is wrong.",
            "how_to_check": "Read SPEC.md's holding rule, remove the release branch in step(), "
            "and run d1_hand_trace: the gripper should stay closed for the whole trace.",
        },
        {
            "claim": "Each lower threshold only moves the release point, because the hand keeps "
            "approaching (31, 18, 12 mm).",
            "how_to_check": "Compare the dist_mm at each release with the threshold you set; if "
            "it always releases just under it, stop tuning the number.",
        },
    ],
    "model": "fake-templated",
}
"""What every Doctor ask gets, as in packages/contracts/examples/doctor_advice.json."""


def canned_proposal(
    now: datetime | None = None, ttl_s: float = 3600, **changes: Any
) -> dict[str, Any]:
    """The D1 fix as a proposal (packages/contracts/examples/proposal.json), for a seeded
    device on firmware 1, harness 1, code `c0de001` with no overlay. It expires `ttl_s`
    after `now` (default: the real time now). `changes` replace top-level fields; pass
    `base={...}` to change the base."""
    expires = (now or datetime.now(UTC)) + timedelta(seconds=ttl_s)
    proposal = {
        "proposal_id": "prop_8c2e4a6b0d1f4e3a9b7c5d2e1f0a3b4c",
        "incident_id": "6d0a4c2e-9b1f-4e3a-8c5d-7f2e1a0b9c84",
        "base": {"firmware": 1, "harness": 1, "code_rev": "c0de001", "overlay_id": None},
        "code_patch": D1_CODE_PATCH,
        "repro_test": "tests/test_hold_proximity.py::"
        "test_hand_near_while_holding_keeps_gripper_closed",
        "why": "SPEC.md forbids opening the gripper on the ultrasonic reading while holding.",
        "checks": {"stub": False, "fails_before": True, "passes_after": True},
        "expires_at": expires.astimezone(UTC).isoformat().replace("+00:00", "Z"),
    }
    return {**proposal, **changes}


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
        self.agent_runs: dict[
            tuple[str, str], dict[str, Any]
        ] = {}  # (device, run_id) -> newest body

        # Fleet Doctor
        self.advice: dict[str, Any] = dict(CANNED_ADVICE)
        self.asks: list[tuple[str, dict[str, Any]]] = []  # (device, DoctorAsk body)
        self.proposals: dict[str, tuple[str, dict[str, Any]]] = {}  # id -> (device, proposal)
        self.offers: dict[str, str] = {}  # device -> the proposal its check-ins point at
        self.proposal_results: dict[str, tuple[str, dict[str, Any]]] = {}  # id -> (device, body)

        self.offline = False
        self.fail_status: int | None = None
        self.corrupt: set[str] = set()
        self.doctor_timeout = False
        self.clock: Callable[[], datetime] = lambda: datetime.now(UTC)
        """The cloud's time, for proposal expiry. Tests set it."""

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

    def offer(self, device_id: str, proposal: dict[str, Any] | None = None) -> dict[str, Any]:
        """Offer `proposal` (default `canned_proposal`, expiring an hour after `clock()`) to
        `device_id`: its check-ins point at it until a result arrives or it expires.
        Returns the proposal."""
        proposal = proposal or canned_proposal(now=self.clock())
        self.proposals[proposal["proposal_id"]] = (device_id, proposal)
        self.offers[device_id] = proposal["proposal_id"]
        return proposal

    def pointer(self, device_id: str) -> dict[str, Any] | None:
        """The check-in's `proposal` for `device_id`, with the sha256 of the served body."""
        pid = self.offers.get(device_id)
        if pid is None or self.frozen or self._expired(pid):
            return None
        proposal = self.proposals[pid][1]
        _, sha = proposal_body_and_sha256(proposal)
        keys = ("proposal_id", "incident_id", "expires_at")
        return {**{k: proposal[k] for k in keys}, "sha256": sha}

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
            200,
            json={
                "assigned": assigned,
                "drop_overlay": drop,
                "frozen": self.frozen,
                "proposal": self.pointer(device),
            },
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

    def _agent_run(self, request, device, run):
        raw, report = self._parse(request, AgentRunReport)
        if report.run_id != run:
            return _error(422, "invalid_payload", "run_id in the body doesn't match the URL")
        created = (device, run) not in self.agent_runs
        self.agent_runs[(device, run)] = raw
        return httpx.Response(200, json={"run_id": run, "created": created, "stored": True})

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

    # -- Fleet Doctor (3.8-3.10) ------------------------------------------------------

    def _expired(self, proposal_id: str) -> bool:
        body = self.proposals[proposal_id][1]
        return Proposal.model_validate(body).expires_at <= self.clock()

    def _doctor_ask(self, request, device):
        if self.doctor_timeout:
            raise httpx.ReadTimeout("fake doctor timed out", request=request)
        raw, _ = self._parse(request, DoctorAsk)
        if self.frozen:
            return _error(409, "fleet_frozen", "the kill switch is on")
        self.asks.append((device, raw))
        return httpx.Response(200, json={**self.advice, "ask_id": f"ask_fake{len(self.asks)}"})

    def _proposal(self, request, device, proposal):
        owner, body = self.proposals.get(proposal, (None, None))
        if owner != device:
            return _error(404, "unknown_proposal", proposal)
        if self.frozen:
            return _error(409, "fleet_frozen", "the kill switch is on")
        if proposal in self.proposal_results or self._expired(proposal):
            return _error(410, "proposal_gone", f"{proposal} is no longer offered")
        content, sha = proposal_body_and_sha256(body)
        if request.url.path in self.corrupt:
            sha = "0" * 64
        headers = {SHA256_HEADER: sha, "Content-Type": "application/json"}
        return httpx.Response(200, content=content, headers=headers)

    def _proposal_result(self, request, device, proposal):
        raw, result = self._parse(request, ProposalResult)
        if self.proposals.get(proposal, (None,))[0] != device:
            return _error(404, "unknown_proposal", proposal)
        if proposal in self.proposal_results:
            _, first = self.proposal_results[proposal]
            same = (first["result"], first.get("overlay_id")) == (result.result, result.overlay_id)
            return httpx.Response(200) if same else _error(409, "proposal_result_conflict")
        self.proposal_results[proposal] = (device, raw)
        if self.offers.get(device) == proposal:
            del self.offers[device]  # the cloud has the answer: stop pointing at it
        return httpx.Response(204)
