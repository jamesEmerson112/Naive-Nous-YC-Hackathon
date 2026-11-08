"""Reference visor: follow the cloud's assignments without ever bricking the arm (D7, D8).

Each `tick(now)`:

0. If an install is unfinished (see below), finish it before anything else.
1. Check in with what the device runs (APIs §3.1). If the cloud can't be reached, keep
   running the current version and stop here; reports stay queued in the outbox (D8).
2. If the response says `drop_overlay`, drop the active overlay.
3. Firmware, then the code package named by the assigned manifest's `code_rev` (only once
   that firmware runs), then the harness (always, whatever happened to the firmware): if
   the assigned version differs from the running one, pull it, check the hash, install it,
   run the health check, and keep it or roll back to the last good version (D7). The check
   passes when health is not `failing` and no worse than just before the install, so a
   device that was already `degraded` can still take updates. Each attempt queues an
   `InstallResult`: `installed`, `rolled_back`, `hash_mismatch`, or `invalid` (hash fine,
   content failed validation; nothing installed). A version that failed is not retried
   until the cloud changes the firmware assignment.
4. A new firmware or code version replaces the active overlay (D7), but only once it has
   passed its health check; the drop is reported as overlay status `superseded`. On
   rollback the overlay stays.
5. While `frozen` (kill switch), only a move back to the last good version is installed.
6. Drain the outbox.

**Crash safety.** `last_good` and a `pending` marker (the install in progress, with the
health before it) live in a small JSON state file, written atomically. If the process or
the power dies between `install` and the health check, the next start sees the marker and
health-checks the new version or rolls back to `last_good` before trusting anything. An
exception inside `install` or the health check also rolls back, and a rollback that fails
keeps the marker so it is retried (next tick, or next start) before any other install.

The visor only needs the small `Device` interface below. `sim.SimDevice` fakes it; on the
Pi, implement it over the real app (keep versions on disk, switch a `current` symlink,
restart the app, run the tier 1-3 checks). Standard library + httpx + romulus-contracts
only, Python 3.11+.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

import httpx

from fake_device.client import DeviceClient, HashMismatch, InvalidPackage
from fake_device.outbox import Outbox, atomic_write
from romulus_contracts import (
    CheckIn,
    CheckInResponse,
    FirmwareManifest,
    InstallResult,
    OverlayStatus,
)

log = logging.getLogger(__name__)

KINDS = ("firmware", "code", "harness")
HEALTH_RANK = {"ok": 0, "degraded": 1, "failing": 2}
_FIRST = object()  # no check-in seen yet


class Device(Protocol):
    """What the visor needs from the device it manages."""

    def checkin_payload(self) -> CheckIn:
        """What the device runs right now, its overlay, health and metrics."""

    def install(self, kind: str, version: Any, payload: Any) -> None:
        """Store `version` on disk and switch to it.

        `payload` is a `FirmwareManifest` for firmware, the package bytes otherwise.
        """

    def rollback(self, kind: str, version: Any) -> None:
        """Switch back to `version` (the last good one, already on disk; None = nothing)."""

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


def passes(after: str, before: str) -> bool:
    """The post-install check: never `failing`, and no worse than just before the install.

    So a device already `degraded` (an overlay under watch) can still take updates, but a
    device that is `failing` can't turn a new version into its last good one.
    """
    return after != "failing" and HEALTH_RANK.get(after, 2) <= HEALTH_RANK.get(before, 2)


class Visor:
    def __init__(
        self, client: DeviceClient, device: Device, outbox: Outbox, state_path: str | Path
    ) -> None:
        self.client = client
        self.device = device
        self.outbox = outbox
        self.state_path = Path(state_path)
        self.rejected: dict[str, Any] = {}  # kind -> the version that failed here
        self._rejected_under: Any = _FIRST  # the firmware assignment those failures were under
        self._manifests: dict[int, FirmwareManifest] = {}

        state = self._load()
        # Very first start (no state file): whatever runs now is the last good version.
        self.last_good: dict[str, Any] = state["last_good"] if state else self._running()
        self.pending: dict[str, Any] | None = state["pending"] if state else None
        if state is None:
            self._save()

    @property
    def id(self) -> str:
        return self.client.device_id

    # -- state file -------------------------------------------------------------------

    def _load(self) -> dict[str, Any] | None:
        try:
            return json.loads(self.state_path.read_text())
        except FileNotFoundError:
            return None

    def _save(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        state = {"last_good": self.last_good, "pending": self.pending}
        atomic_write(self.state_path, json.dumps(state).encode())

    # -- the loop ---------------------------------------------------------------------

    def recover(self, now: datetime) -> InstallResult | None:
        """Finish an unfinished install: one a crash interrupted, or a failed rollback.

        A crash mid-install: keep the new version if it passes the health check, else roll
        back. A rollback that failed: try it again. Returns the result once resolved.
        """
        if self.pending is None:
            return None
        kind, version, before = (self.pending[k] for k in ("kind", "version", "health_before"))
        if self.pending.get("rolling_back"):
            result = self._roll_back(kind, version, now, self.pending.get("why", "rollback"))
        else:
            health = self._health()
            if self._running()[kind] == version and passes(health, before):
                result = self._keep(kind, version, now, f"health {health} after a restart")
            else:
                why = f"install interrupted by a restart; health {health} (was {before})"
                result = self._roll_back(kind, version, now, why)
        if result is not None:
            self.outbox.add_install(result)
        return result

    def tick(self, now: datetime) -> TickResult:
        if self.pending is not None:
            self.recover(now)  # first thing after a start, and until a failed rollback works
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

    def _running(self) -> dict[str, Any]:
        return _running_versions(self.device.checkin_payload())

    def _health(self) -> str:
        try:
            return self.device.health_check()
        except Exception:
            log.exception("%s: health check raised", self.id)
            return "failing"

    def _follow(self, resp: CheckInResponse, now: datetime) -> list[InstallResult]:
        fw, hv = resp.assigned.firmware, resp.assigned.harness
        if self._rejected_under is not _FIRST and fw != self._rejected_under:
            # A new assignment: anything that failed before may be tried again. (Not on the
            # first check-in, so a version `recover` just rolled back isn't retried at once.)
            self.rejected.clear()
        self._rejected_under = fw

        results: list[InstallResult] = []
        if fw is not None and fw != self._running()["firmware"]:
            result = self._install("firmware", fw, resp.frozen, now)
            if result is not None:
                results.append(result)

        # The code package belongs to the firmware: only once the assigned firmware runs.
        if fw is not None and self._running()["firmware"] == fw:
            manifest = self._manifest(fw)
            rev = manifest.code_rev if manifest else None
            if rev is not None and rev != self._running()["code"]:
                result = self._install("code", rev, resp.frozen, now)
                if result is not None:
                    results.append(result)

        # The harness doesn't depend on the firmware: always try it.
        if hv is not None and hv != self._running()["harness"]:
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

    # -- one install ------------------------------------------------------------------

    def _install(self, kind: str, version: Any, frozen: bool, now: datetime):
        if self.pending is not None:
            return None  # an unfinished install or rollback comes first (see `recover`)
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

        before = self._health()
        self.pending = {"kind": kind, "version": version, "health_before": before}
        self._save()  # from here on, a crash is finished by `recover` on the next start
        try:
            self.device.install(kind, version, payload)
            health = self.device.health_check()
        except Exception as exc:
            log.exception("%s: installing %s %s failed", self.id, kind, version)
            return self._roll_back(kind, version, now, f"install failed: {exc}")
        if passes(health, before):
            return self._keep(kind, version, now, f"health {health} after install")
        why = f"health {health} after install (was {before})"
        return self._roll_back(kind, version, now, why)

    def _keep(self, kind: str, version: Any, now: datetime, detail: str) -> InstallResult:
        self.last_good[kind] = version
        self.pending = None
        self._save()
        if kind in ("firmware", "code"):
            self._drop_overlay_for(kind, version, now)
        return InstallResult(
            type=kind, version=version, result="installed", detail=detail, occurred_at=now
        )

    def _roll_back(self, kind: str, version: Any, now: datetime, why: str) -> InstallResult | None:
        """Switch back to last good; report `rolled_back` once that worked.

        If the rollback itself fails, the pending marker stays (flagged `rolling_back`), so
        the next tick, or the next start, tries again before installing anything else.
        """
        good = self.last_good[kind]
        self.rejected[kind] = version
        try:
            self.device.rollback(kind, good)
        except Exception:
            log.exception("%s: rollback of %s to %s failed; will retry", self.id, kind, good)
            self.pending = {**(self.pending or {}), "rolling_back": True, "why": why}
            self._save()
            return None
        self.pending = None
        self._save()
        return InstallResult(
            type=kind,
            version=version,
            result="rolled_back",
            detail=f"{why}; back on {kind} {good}",
            occurred_at=now,
        )

    def _drop_overlay_for(self, kind: str, version: Any, now: datetime) -> None:
        """D7: the new, healthy version replaces the overlay. Tell the cloud."""
        overlay_id = self.device.checkin_payload().overlay_id
        if overlay_id is None:
            return
        self.device.drop_overlay()
        status = OverlayStatus(
            status="superseded", reason=f"replaced by {kind} {version} (D7)", occurred_at=now
        )
        self.outbox.add_overlay_status(overlay_id, status)
