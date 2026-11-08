"""SimDevice: the state of one fake arm, implementing the visor's `Device` interface.

It runs nothing real. "Installing" switches version numbers, health is derived from a few
flags, and metrics come from a seeded RNG so runs are repeatable. Its reflexes are real,
though: `reflexes(signals)` runs `romulus_contracts.rules.evaluate` over the running
manifest's rules with the active overlay merged on top, exactly as the gate's replay does.

It also fakes the Fleet Doctor's `doctor.ProposalHost` (parked for a consult, snapshot,
apply, verify, health, mark-good), kept apart from the visor's interface.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

from romulus_contracts import (
    CONTRACT_VERSION,
    CheckIn,
    Fired,
    FirmwareManifest,
    Overlay,
    Proposal,
    Running,
    evaluate,
)


def merge_rules(rules: dict[str, dict], diff: dict[str, Any]) -> dict[str, dict]:
    """`rules` with an overlay diff's partial rule entries merged on top, per rule."""
    merged = {name: dict(rule) for name, rule in rules.items()}
    for name, patch in (diff.get("rules") or {}).items():
        merged[name] = {**merged.get(name, {}), **patch}
    return merged


@dataclass
class SimDevice:
    device_id: str
    canary: bool = False
    seed: int = 0
    mcu_build: str = "stm32-sim-0001"

    firmware: int | None = None
    harness: int | None = None
    code_rev: str | None = None
    manifest: FirmwareManifest | None = None
    overlay: Overlay | None = None
    overlay_held: bool = False
    """The overlay passed its watch (reported `held`): health is `ok` again."""
    parked: bool = False
    """Parked with torque off (a `park` incident): health is `failing` until repaired."""
    consult_parked: bool = False
    """Parked for a Fleet Doctor consult (the healer gave up and asked the cloud): safe, no
    tasks, health `degraded`, until a proposal overlay is marked good or the wait ends."""
    task_running: bool = False
    """A task is running, so the proposal applier won't touch the code."""
    proposal_verify_ok: bool = True
    """Sim knob: whether verify passes after applying a proposal."""
    fault_recurs: bool = False
    """Sim knob: the fault is back, so health is `failing` (e.g. under a proposal overlay)."""

    bad_versions: dict[int, str] = field(default_factory=dict)
    """Firmware versions that are bad here (bad-candidate mode, flow F4), version -> mode:

    - `"install"`: the post-install health check fails, so the visor rolls back at once.
    - `"late"`: the post-install check passes, then every check-in reports `failing`
      until the cloud assigns another version.
    """
    bad_next: str | None = None
    """Mark the next new firmware version installed as bad with this mode, whatever its
    number (a version the arm already ran, e.g. a rollback target, is never marked)."""

    _stored: dict[tuple[str, Any], Any] = field(default_factory=dict)
    """Versions kept "on disk" (kind, version) -> payload, so rollback needs no cloud."""

    def __post_init__(self) -> None:
        self.rng = random.Random(f"{self.device_id}:{self.seed}")

    # -- the visor's Device interface -------------------------------------------------

    def running(self) -> Running | None:
        if self.firmware is None and self.harness is None and self.code_rev is None:
            return None  # first boot
        return Running(
            firmware=self.firmware,
            harness=self.harness,
            code_rev=self.code_rev,
            mcu_build=self.mcu_build,
        )

    def health(self, during_install: bool = False) -> str:
        mode = self.bad_versions.get(self.firmware)
        if self.parked or mode == "install" or (mode == "late" and not during_install):
            return "failing"
        if self.fault_recurs:
            return "failing"
        if self.consult_parked:
            return "degraded"  # safe and waiting for the Doctor, but not working
        if self.overlay is not None and not self.overlay_held:
            return "degraded"  # an overlay under watch (contracts README, I0 item 1)
        return "ok"

    def metrics(self) -> dict[str, float]:
        failing = self.health() == "failing"
        return {
            "task_success_rate": round(
                self.rng.uniform(0.1, 0.4) if failing else self.rng.uniform(0.85, 0.98), 3
            ),
            "servo_temp_c_max": round(self.rng.uniform(36.0, 46.0), 1),
            "bus_voltage_v": round(self.rng.uniform(11.8, 12.3), 2),
        }

    def checkin_payload(self) -> CheckIn:
        return CheckIn(
            running=self.running(),
            overlay_id=self.overlay.overlay_id if self.overlay else None,
            health=self.health(),
            metrics=self.metrics(),
            contract_version=CONTRACT_VERSION,
            # An optional extra field: a fake arm the cloud hasn't seeded registers as `sim`,
            # never as a real arm (the cloud defaults a silent device to `unknown`).
            kind="sim",
        )

    def install(self, kind: str, version: Any, payload: Any) -> None:
        if kind not in ("firmware", "code", "harness"):
            raise ValueError(f"unknown install kind {kind!r}")
        new = (kind, version) not in self._stored
        self._stored[(kind, version)] = payload
        # Only a version this arm never ran can be the bad candidate: the gate rolling the
        # canary back to its old firmware must work even while bad mode is armed.
        if kind == "firmware" and self.bad_next and new:
            self.bad_versions[version] = self.bad_next
            self.bad_next = None
        self._switch(kind, version)

    def rollback(self, kind: str, version: Any) -> None:
        self._switch(kind, version)

    def _switch(self, kind: str, version: Any) -> None:
        if kind == "firmware":
            self.firmware, self.manifest = version, self._stored.get((kind, version))
        elif kind == "code":
            self.code_rev = version
        else:
            self.harness = version

    def health_check(self) -> str:
        return self.health(during_install=True)

    def drop_overlay(self) -> None:
        self.overlay = None
        self.overlay_held = False

    # -- the healer's side ------------------------------------------------------------

    def apply_overlay(self, overlay: Overlay) -> None:
        self.overlay = overlay
        self.overlay_held = False

    def rules(self) -> dict[str, dict]:
        """The rules in force: the running manifest's, with the overlay's diff on top."""
        base = self.manifest.rules if self.manifest else {}
        merged = merge_rules(base, self.overlay.diff if self.overlay else {})
        # A partial patch for a rule the manifest doesn't have isn't a usable rule.
        return {name: r for name, r in merged.items() if "signal" in r and "action" in r}

    def reflexes(self, signals: dict[str, Any]) -> list[Fired]:
        return evaluate(self.rules(), signals)

    # -- the Fleet Doctor's side (doctor.ProposalHost) --------------------------------

    def park_for_consult(self) -> None:
        self.consult_parked = True

    def parked_and_idle(self) -> bool:
        return (self.parked or self.consult_parked) and not self.task_running

    def active_overlay_id(self) -> str | None:
        return self.overlay.overlay_id if self.overlay else None

    def snapshot(self) -> tuple[Overlay | None, bool]:
        return self.overlay, self.overlay_held

    def restore(self, snapshot: tuple[Overlay | None, bool]) -> None:
        self.overlay, self.overlay_held = snapshot

    def verify(self, proposal: Proposal) -> tuple[bool, str]:
        check = proposal.repro_test or "the verify scripts"
        if not self.proposal_verify_ok:
            return False, f"verify failed: {check} (sim)"
        return True, f"verify passed: {check} (sim)"

    def healthy(self) -> bool:
        return self.health() != "failing"

    def mark_good(self, overlay_id: str) -> None:
        self.consult_parked = False  # the fix held for the mark-good window: back to work
