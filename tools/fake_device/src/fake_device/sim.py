"""SimDevice: the state of one fake arm, implementing the visor's `Device` interface.

It runs nothing real. "Installing" switches version numbers, health is derived from a few
flags, and metrics come from a seeded RNG so runs are repeatable. Its reflexes are real,
though: `reflexes(signals)` runs `romulus_contracts.rules.evaluate` over the running
manifest's rules with the active overlay merged on top, exactly as the gate's replay does.
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

    bad_versions: dict[int, str] = field(default_factory=dict)
    """Firmware versions that are bad here (bad-candidate mode, flow F4), version -> mode:

    - `"install"`: the post-install health check fails, so the visor rolls back at once.
    - `"late"`: the post-install check passes, then every check-in reports `failing`
      until the cloud assigns another version.
    """
    bad_next: str | None = None
    """Mark the next firmware version installed as bad with this mode, whatever its number."""

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
        )

    def install(self, kind: str, version: Any, payload: Any) -> None:
        if kind not in ("firmware", "code", "harness"):
            raise ValueError(f"unknown install kind {kind!r}")
        self._stored[(kind, version)] = payload
        if kind == "firmware" and self.bad_next:
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
