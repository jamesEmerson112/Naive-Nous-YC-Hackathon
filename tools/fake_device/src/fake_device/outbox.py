"""On-disk outbox for device reports: queue now, send when the cloud answers (D8, APIs §3).

Installs, incidents, overlay statuses and recordings go into a directory as one JSON file
each, named by a sequence number, so the queue survives restarts and power cuts.
`drain()` sends them oldest first:

- **Order.** One queue for every kind, sent strictly in order: if an entry can't be sent
  yet, nothing behind it is sent either. That keeps the order per kind and sends an
  incident before its recordings (APIs §3).
- **Retry** on connection errors, timeouts, 5xx, 408 and 429, with exponential backoff
  (2, 4, 8 … up to `max_backoff_s` seconds). A resend is harmless because incident and
  recording ids are made on the device before queueing (D8).
- **Dead-letter** on any other 4xx (the cloud will never accept it): move the entry to
  `dead/` with the error attached, log the cloud's `detail`, and carry on, so one bad entry
  can't block the queue forever (contracts README, outbox rule).
- **Delete** an entry only after the cloud confirms it.

Standard library + httpx + romulus-contracts only, Python 3.11+, so it can be copied into
the Pi app as is.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from fake_device.client import DeviceClient, body
from romulus_contracts import IncidentReport, InstallResult, OverlayStatus, RecordingMeta

log = logging.getLogger(__name__)

RETRYABLE_STATUS = {408, 429}


def _reason(exc: Exception) -> str:
    """A short reason for the log and the dead-letter file, with the cloud's `detail`."""
    if isinstance(exc, httpx.HTTPStatusError):
        try:
            err = exc.response.json()
            return f"{exc.response.status_code} {err.get('error')}: {err.get('detail')}"
        except ValueError:
            return f"{exc.response.status_code} {exc.response.text[:200]}"
    return f"{type(exc).__name__}: {exc}"


def _retryable(exc: Exception) -> bool:
    if isinstance(exc, httpx.TransportError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        return status >= 500 or status in RETRYABLE_STATUS
    return False


class Outbox:
    def __init__(self, directory: str | Path, max_backoff_s: float = 60) -> None:
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.max_backoff_s = max_backoff_s
        self.failures = 0
        self.retry_at: datetime | None = None
        existing = [int(p.name.split(".", 1)[0]) for p in self._entries()]
        self._next_seq = max(existing, default=0) + 1

    # -- queueing ---------------------------------------------------------------------

    def add_install(self, result: InstallResult) -> None:
        self._add("install", body(result))

    def add_incident(self, incident: IncidentReport) -> None:
        self._add("incident", body(incident))

    def add_overlay_status(self, overlay_id: str, status: OverlayStatus) -> None:
        self._add("overlay", body(status), overlay_id=overlay_id)

    def add_recording(self, meta: RecordingMeta, data: bytes) -> None:
        """Queue the metadata and keep a copy of the blob next to it until it's uploaded."""
        self._add("recording", body(meta), blob=data)

    def _add(self, kind: str, payload: dict[str, Any], blob: bytes | None = None, **extra) -> None:
        stem = f"{self._next_seq:08d}.{kind}"
        self._next_seq += 1
        if blob is not None:
            # Blob first: a JSON entry never exists without its blob.
            self._write(self.dir / f"{stem}.bin", blob)
        entry = {"kind": kind, "payload": payload, **extra}
        self._write(self.dir / f"{stem}.json", json.dumps(entry).encode())

    @staticmethod
    def _write(path: Path, data: bytes) -> None:
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, path)  # atomic: readers see the whole file or nothing

    # -- reading ----------------------------------------------------------------------

    def _entries(self) -> list[Path]:
        return sorted(self.dir.glob("*.json"))

    def pending(self) -> list[dict[str, Any]]:
        """The queued entries, oldest first: `{"kind", "payload", ...}`."""
        return [json.loads(p.read_text()) for p in self._entries()]

    def __len__(self) -> int:
        return len(self._entries())

    # -- sending ----------------------------------------------------------------------

    def drain(self, client: DeviceClient, now: datetime) -> int:
        """Send queued entries in order until the queue is empty or the cloud is unreachable.

        Returns how many were sent. Does nothing while backing off after a failure.
        """
        if self.retry_at is not None and now < self.retry_at:
            return 0
        sent = 0
        for path in self._entries():
            try:
                self._send(client, path)
            except (httpx.TransportError, httpx.HTTPStatusError, ValueError, KeyError) as exc:
                # ValueError covers a corrupt file and a payload the models reject.
                if not _retryable(exc):
                    reason = _reason(exc)
                    log.warning("outbox: dead-lettering %s: %s", path.name, reason)
                    self._dead_letter(path, reason)
                    continue
                self.failures += 1
                backoff = min(2.0**self.failures, self.max_backoff_s)
                self.retry_at = now + timedelta(seconds=backoff)
                log.info("outbox: %s not sent (%s); retrying in %.0f s", path.name, exc, backoff)
                return sent
            self._remove(path)
            sent += 1
        self.failures = 0
        self.retry_at = None
        return sent

    def _send(self, client: DeviceClient, path: Path) -> None:
        entry = json.loads(path.read_text())  # a corrupt file raises ValueError -> dropped
        kind, payload = entry["kind"], entry["payload"]
        if kind == "install":
            client.report_install(InstallResult.model_validate(payload))
        elif kind == "incident":
            client.report_incident(IncidentReport.model_validate(payload))
        elif kind == "overlay":
            client.report_overlay(entry["overlay_id"], OverlayStatus.model_validate(payload))
        elif kind == "recording":
            data = path.with_suffix(".bin").read_bytes()
            client.upload_recording(RecordingMeta.model_validate(payload), data)
        else:
            raise ValueError(f"unknown outbox entry kind {kind!r}")

    def dead_letters(self) -> list[dict[str, Any]]:
        """Entries the cloud refused for good, oldest first, each with its `error`."""
        return [json.loads(p.read_text()) for p in sorted((self.dir / "dead").glob("*.json"))]

    def _dead_letter(self, path: Path, reason: str) -> None:
        dead = self.dir / "dead"
        dead.mkdir(exist_ok=True)
        try:
            entry = json.loads(path.read_text())
        except ValueError:
            entry = {"raw": path.read_text(errors="replace")}
        entry["error"] = reason
        self._write(dead / path.name, json.dumps(entry).encode())
        blob = path.with_suffix(".bin")
        if blob.exists():
            os.replace(blob, dead / blob.name)
        path.unlink(missing_ok=True)

    @staticmethod
    def _remove(path: Path) -> None:
        path.with_suffix(".bin").unlink(missing_ok=True)
        path.unlink(missing_ok=True)
