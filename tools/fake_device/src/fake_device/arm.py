"""FakeArm: one simulated device wired to the cloud (SimDevice + DeviceClient + Outbox + Visor).

Everything the arm keeps on disk lives under `state_dir`: the outbox in `outbox/` and the
visor's state in `visor.json`. Building a new `FakeArm` (or a new `Visor`) on the same
directory and `SimDevice` is how tests simulate a restart.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

import httpx

from fake_device.client import DeviceClient
from fake_device.outbox import Outbox
from fake_device.recordings import Recording
from fake_device.sim import SimDevice
from fake_device.visor import TickResult, Visor
from romulus_contracts import Agent, IncidentReport, Outcome, OverlayStatus, Running


class FakeArm:
    def __init__(
        self,
        http: httpx.Client,
        device_id: str,
        state_dir: str | Path,
        canary: bool = False,
        seed: int = 0,
        device: SimDevice | None = None,
    ) -> None:
        state_dir = Path(state_dir)
        self.device = device or SimDevice(device_id, canary=canary, seed=seed)
        self.client = DeviceClient(http, device_id)
        self.outbox = Outbox(state_dir / "outbox")
        self.visor = Visor(self.client, self.device, self.outbox, state_dir / "visor.json")
        self._repeated: set[int] = set()

    @property
    def device_id(self) -> str:
        return self.device.device_id

    def tick(self, now: datetime) -> TickResult:
        """One visor cycle: check in, follow assignments, drain the outbox.

        A device on a "late" bad version also repeats its last fault once (flow F4's
        "canary repeated camera_occluded after install").
        """
        result = self.visor.tick(now)
        fw = self.device.firmware
        if self.device.bad_versions.get(fw) == "late" and fw not in self._repeated:
            self._repeated.add(fw)
            self.report_incident(now, self._repeat_incident(now))
        return result

    def _repeat_incident(self, now: datetime) -> IncidentReport:
        return IncidentReport(
            incident_id=uuid.uuid4(),
            occurred_at=now,
            kind="camera_occluded",
            summary="Camera occlusion again right after installing a new firmware",
            running=Running(firmware=self.device.firmware, harness=self.device.harness),
            agent=Agent(reasoning="Same fault as before, on the new firmware.", actions=["park"]),
            outcome=Outcome(resolved=False, resolving_action="park", suspected_cause="software"),
            signals={"brightness": [0.4, 0.1, 0.1]},
        )

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
