"""FakeArm: one simulated device wired to the cloud (SimDevice + DeviceClient + Outbox + Visor).

Everything the arm keeps on disk lives under `state_dir`: the outbox in `outbox/`, the
visor's state in `visor.json` and the Fleet Doctor applier's in `doctor.json`. Building a
new `FakeArm` (or a new `Visor`) on the same directory and `SimDevice` is how tests
simulate a restart.

The Fleet Doctor (`doctor.py`) rides along: `doctor` is the healer's L1 trigger hook, and
on every tick `applier` runs its mark-good timer and takes any proposal the check-in
points at (L2). A proposal overlay that was marked good is reported `held`
`proposal_hold_after_s` later, as the scenarios do for the healer's own overlays.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import datetime, timedelta
from pathlib import Path

import httpx

from fake_device.client import DeviceClient
from fake_device.doctor import ProposalApplier, TriggerHook
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
        self.doctor = TriggerHook(self.client)
        self.applier = ProposalApplier(
            self.client, self.device, self.outbox, state_path=state_dir / "doctor.json"
        )
        self.proposal_hold_after_s = 1200.0
        """A marked-good proposal overlay is reported `held` this long after (20 min)."""
        self._holds: dict[str, datetime] = {}  # proposal overlay id -> when to report held
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
        self._doctor_tick(now, result)
        return result

    def _doctor_tick(self, now: datetime, result: TickResult) -> None:
        """Fleet Doctor L2: the mark-good timer first, then a new proposal, then send."""
        ended = self.applier.tick(now)
        if ended is not None and ended[1] == "good":
            self._holds[ended[0]] = now + timedelta(seconds=self.proposal_hold_after_s)
        resp = result.response
        if resp is not None and resp.proposal is not None:
            self.applier.handle(resp.proposal, now, frozen=resp.frozen)
        for overlay_id, due in list(self._holds.items()):
            if now < due:
                continue
            del self._holds[overlay_id]
            if self.device.active_overlay_id() == overlay_id:  # not replaced meanwhile
                reason = "no recurrence for 20 min"
                held = OverlayStatus(status="held", reason=reason, occurred_at=now)
                self.outbox.add_overlay_status(overlay_id, held)
                self.device.overlay_held = True
        if len(self.outbox):
            self.outbox.drain(self.client, now)

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
