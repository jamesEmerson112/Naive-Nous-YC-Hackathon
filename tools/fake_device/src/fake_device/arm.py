"""FakeArm: one simulated device wired to the cloud (SimDevice + DeviceClient + Outbox + Visor)."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

import httpx
from romulus_contracts import IncidentReport, OverlayStatus

from fake_device.client import DeviceClient
from fake_device.outbox import Outbox
from fake_device.recordings import Recording
from fake_device.sim import SimDevice
from fake_device.visor import TickResult, Visor


class FakeArm:
    def __init__(
        self,
        http: httpx.Client,
        device_id: str,
        outbox_dir: str | Path,
        canary: bool = False,
        seed: int = 0,
    ) -> None:
        self.device = SimDevice(device_id, canary=canary, seed=seed)
        self.client = DeviceClient(http, device_id)
        self.outbox = Outbox(outbox_dir)
        self.visor = Visor(self.client, self.device, self.outbox)

    @property
    def device_id(self) -> str:
        return self.device.device_id

    def tick(self, now: datetime) -> TickResult:
        """One visor cycle: check in, follow assignments, drain the outbox."""
        return self.visor.tick(now)

    def report_incident(
        self, now: datetime, incident: IncidentReport, recordings: Iterable[Recording] = ()
    ) -> None:
        """Queue the incident, then its recordings (APIs §3: incident first), and try to send."""
        self.outbox.add_incident(incident)
        for meta, data in recordings:
            self.outbox.add_recording(meta, data)
        self.outbox.drain(self.client, now)

    def report_overlay(self, now: datetime, overlay_id: str, status: OverlayStatus) -> None:
        self.outbox.add_overlay_status(overlay_id, status)
        self.outbox.drain(self.client, now)
