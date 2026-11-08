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

    bad_versions: set[int] = field(default_factory=set)
    """Firmware versions that fail their health check here (bad-candidate mode, flow F4)."""
    bad_next: bool = False
    """Mark the next firmware version installed as bad, whatever its number."""

    _previous: dict[str, tuple[Any, Any]] = field(default_factory=dict)

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

    def health(self) -> str:
        if self.parked or self.firmware in self.bad_versions:
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
        if kind == "firmware":
            self._previous[kind] = (self.firmware, self.manifest)
            self.firmware, self.manifest = version, payload
            if self.bad_next:
                self.bad_versions.add(version)
                self.bad_next = False
        elif kind == "code":
            self._previous[kind] = (self.code_rev, None)
            self.code_rev = version
        elif kind == "harness":
            self._previous[kind] = (self.harness, None)
            self.harness = version
        else:
            raise ValueError(f"unknown install kind {kind!r}")

    def rollback(self, kind: str) -> None:
        version, extra = self._previous.pop(kind, (None, None))
        if kind == "firmware":
            self.firmware, self.manifest = version, extra
        elif kind == "code":
            self.code_rev = version
        else:
            self.harness = version

    def health_check(self) -> str:
        return self.health()

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
