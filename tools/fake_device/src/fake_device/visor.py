"""Reference visor: follow the cloud's assignments without ever bricking the arm (D7, D8).

Each `tick(now)`:

1. Check in with what the device runs (APIs §3.1). If the cloud can't be reached, keep
   running the current version and stop here; reports stay queued in the outbox (D8).
2. If the response says `drop_overlay`, drop the active overlay.
3. For firmware, then the code package named by the assigned manifest's `code_rev`, then
   the harness: if the assigned version differs from the running one, pull it, check the
   hash, install it, run the health check, and keep it or roll back to the last good
   version (D7). Each attempt queues an `InstallResult`: `installed`, `rolled_back`,
   `hash_mismatch`, or `invalid` (hash fine, content failed validation; nothing installed).
   A version that failed is not retried until the cloud assigns another.
4. While `frozen` (kill switch), only a move back to the last good version is installed.
5. Drain the outbox.

The visor only needs the small `Device` interface below. `sim.SimDevice` fakes it; on the
Pi, implement it over the real app (switch a `current` symlink, restart the app, run the
tier 1-3 checks). Standard library + httpx + romulus-contracts only, Python 3.11+.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

import httpx
from romulus_contracts import CheckIn, CheckInResponse, FirmwareManifest, InstallResult

from fake_device.client import DeviceClient, HashMismatch, InvalidPackage
from fake_device.outbox import Outbox

log = logging.getLogger(__name__)

KINDS = ("firmware", "code", "harness")


class Device(Protocol):
    """What the visor needs from the device it manages."""

    def checkin_payload(self) -> CheckIn:
        """What the device runs right now, its overlay, health and metrics."""

    def install(self, kind: str, version: Any, payload: Any) -> None:
        """Switch to `version`, keeping the previous one for `rollback`.

        `payload` is a `FirmwareManifest` for firmware, the package bytes otherwise.
        """

    def rollback(self, kind: str) -> None:
        """Switch back to what ran before the last `install` of this kind."""

    def health_check(self) -> str:
        """Run the post-install checks now: `ok`, `degraded` or `failing`."""

    def drop_overlay(self) -> None:
        """Remove the active local patch, if any."""


@dataclass
class TickResult:
    online: bool
    response: CheckInResponse | None = None
    installs: list[InstallResult] = field(default_factory=list)


def _running_versions(checkin: CheckIn) -> dict[str, Any]:
    r = checkin.running
    return {
        "firmware": r.firmware if r else None,
        "code": r.code_rev if r else None,
        "harness": r.harness if r else None,
    }


class Visor:
    def __init__(self, client: DeviceClient, device: Device, outbox: Outbox) -> None:
        self.client = client
        self.device = device
        self.outbox = outbox
        # Whatever runs at boot is the last good version until an install proves otherwise.
        self.last_good: dict[str, Any] = _running_versions(device.checkin_payload())
        self.rejected: dict[str, Any] = {}  # kind -> the version that failed here
        self._rejected_for_firmware: Any = None  # the firmware assignment those failures were under
        self._manifests: dict[int, FirmwareManifest] = {}

    def tick(self, now: datetime) -> TickResult:
        try:
            resp = self.client.checkin(self.device.checkin_payload())
        except (httpx.TransportError, httpx.HTTPStatusError) as exc:
            log.info("%s: check-in failed (%s); staying on the current version", self.id, exc)
            self.outbox.drain(self.client, now)
            return TickResult(online=False)

        if resp.drop_overlay:
            self.device.drop_overlay()
        installs = self._follow(resp, now)
        for result in installs:
            self.outbox.add_install(result)
        self.outbox.drain(self.client, now)
        return TickResult(online=True, response=resp, installs=installs)

    @property
    def id(self) -> str:
        return self.client.device_id

    def _follow(self, resp: CheckInResponse, now: datetime) -> list[InstallResult]:
        results: list[InstallResult] = []
        running = _running_versions(self.device.checkin_payload())
        fw = resp.assigned.firmware
        if fw != self._rejected_for_firmware:
            # A new assignment: anything that failed before may be tried again.
            self.rejected.clear()
            self._rejected_for_firmware = fw

        if fw is not None and fw != running["firmware"]:
            result = self._install("firmware", fw, resp.frozen, now)
            if result is not None:
                results.append(result)
            if result is None or result.result != "installed":
                return results  # keep the code that matches the firmware we still run

        if fw is not None:
            manifest = self._manifest(fw)
            rev = manifest.code_rev if manifest else None
            if rev is not None and rev != running["code"]:
                result = self._install("code", rev, resp.frozen, now)
                if result is not None:
                    results.append(result)

        hv = resp.assigned.harness
        if hv is not None and hv != running["harness"]:
            result = self._install("harness", hv, resp.frozen, now)
            if result is not None:
                results.append(result)
        return results

    def _manifest(self, version: int) -> FirmwareManifest | None:
        if version not in self._manifests:
            try:
                self._manifests[version] = self.client.pull_firmware(version)
            except (HashMismatch, InvalidPackage, httpx.TransportError, httpx.HTTPStatusError):
                return None
        return self._manifests[version]

    def _pull(self, kind: str, version: Any) -> Any:
        if kind == "firmware":
            self._manifests[version] = self.client.pull_firmware(version)
            return self._manifests[version]
        if kind == "code":
            return self.client.pull_code(version)
        return self.client.pull_harness(version)

    def _install(self, kind: str, version: Any, frozen: bool, now: datetime):
        if self.rejected.get(kind) == version:
            return None
        if frozen and version != self.last_good[kind]:
            log.info("%s: frozen; not installing %s %s", self.id, kind, version)
            return None
        try:
            payload = self._pull(kind, version)
        except (HashMismatch, InvalidPackage) as exc:
            self.rejected[kind] = version
            result = "hash_mismatch" if isinstance(exc, HashMismatch) else "invalid"
            return InstallResult(
                type=kind, version=version, result=result, detail=str(exc), occurred_at=now
            )
        except (httpx.TransportError, httpx.HTTPStatusError) as exc:
            log.info("%s: pull of %s %s failed (%s); will retry", self.id, kind, version, exc)
            return None

        if kind in ("firmware", "code"):
            self.device.drop_overlay()  # D7: a new version replaces the overlay
        self.device.install(kind, version, payload)
        health = self.device.health_check()
        if health == "ok":
            self.last_good[kind] = version
            return InstallResult(
                type=kind,
                version=version,
                result="installed",
                detail="health ok after install",
                occurred_at=now,
            )
        self.device.rollback(kind)
        self.rejected[kind] = version
        return InstallResult(
            type=kind,
            version=version,
            result="rolled_back",
            detail=f"health {health} after install; back on {kind} {self.last_good[kind]}",
            occurred_at=now,
        )
