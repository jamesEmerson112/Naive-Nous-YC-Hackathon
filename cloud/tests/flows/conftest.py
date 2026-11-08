"""Shared setup for flow tests: the seeded fleet and the contract example payloads."""

import copy
import json
from collections.abc import Callable
from datetime import datetime, timedelta
from functools import partial
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from romulus_cloud.adapters.blobstore import build_blobstore
from romulus_cloud.models import Event
from romulus_cloud.services import registry
from romulus_cloud.services.seed import FIRMWARE_V1, seed

EXAMPLES = Path(__file__).resolve().parents[3] / "packages" / "contracts" / "examples"


@pytest.fixture
def example() -> Callable[[str], dict[str, Any]]:
    """`example("checkin")` → a fresh copy of packages/contracts/examples/checkin.json."""

    def load(name: str) -> dict[str, Any]:
        return copy.deepcopy(json.loads((EXAMPLES / f"{name}.json").read_text()))

    return load


@pytest.fixture
def seeded(db_session: Session, settings) -> Session:
    """The `make seed` fleet (code c0de001, firmware v1, harness v1, arm-1, sim-1..3),
    written into the test's transaction and into the same blob dir the app reads."""
    seed(db_session, build_blobstore(settings.blob_dir))
    db_session.commit()
    return db_session


@pytest.fixture
def v2(seeded: Session, settings) -> Session:
    """On top of the seed: firmware v2, harness v2 and code c0de002 exist (nothing assigned)."""
    store = build_blobstore(settings.blob_dir)
    kw = {"actor": "human:test", "actor_class": "human"}
    registry.create_firmware(seeded, FIRMWARE_V1 | {"version": 2}, **kw)
    meta = b'{"version": 2, "framework": "mini-swe-agent"}'
    registry.create_harness(seeded, store, registry.build_tarball({"harness.json": meta}), **kw)
    code = registry.build_tarball({"device/README.md": b"c0de002"})
    registry.create_code(seeded, store, code, rev="c0de002", **kw)
    seeded.commit()
    return seeded


@pytest.fixture
def count_events(db_session: Session) -> Callable[..., int]:
    """`count_events("install.reported", {"device": "arm-1"})` → how many such events exist."""

    def count(kind: str, refs: dict[str, Any] | None = None) -> int:
        stmt = select(func.count()).select_from(Event).where(Event.kind == kind)
        if refs:
            stmt = stmt.where(Event.refs.contains(refs))
        return db_session.scalar(stmt) or 0

    return count


# --- M3: a controllable server clock, the fake fleet and the worker steps ------------------


class FakeClock:
    """The server's clock in flow tests. Set it with `at(t)`; every read moves it on 1 ms,
    so two requests in one tick stay ordered (a check-in before its install report)."""

    def __init__(self, start: datetime) -> None:
        self.now = start

    def at(self, t: datetime) -> datetime:
        self.now = t
        return t

    def __call__(self) -> datetime:
        self.now += timedelta(milliseconds=1)
        return self.now


@pytest.fixture
def clock(monkeypatch, now) -> FakeClock:
    """Routes stamp `received_at` (check-ins, installs, incidents) and fleet presses with this
    clock instead of the wall clock, so they line up with the `now` given to worker steps."""
    fake = FakeClock(now)
    monkeypatch.setattr("romulus_cloud.api.device.utcnow", fake)
    monkeypatch.setattr("romulus_cloud.api.ops.utcnow", fake)
    monkeypatch.setattr("romulus_cloud.api.doctor.utcnow", fake)  # Fleet Doctor L1 (PR 3)
    monkeypatch.setattr("romulus_cloud.api.proposals.utcnow", fake)  # proposals (PR 4)
    return fake


@pytest.fixture
def arms(device_http, seeded, tmp_path, clock) -> dict[str, Any]:
    """The seeded fake fleet as unmodified `fake_device` arms: sim-1, sim-2 (canary), sim-3."""
    from fake_device.arm import FakeArm

    return {
        f"sim-{i}": FakeArm(device_http, f"sim-{i}", tmp_path / f"sim-{i}", canary=i == 2)
        for i in (1, 2, 3)
    }


@pytest.fixture
def tick_arms(arms, clock) -> Callable[[datetime], dict[str, list[tuple]]]:
    """`tick_arms(t)`: every arm runs one visor cycle at `t`; returns each arm's installs."""

    def tick(t: datetime) -> dict[str, list[tuple]]:
        clock.at(t)
        return {
            name: [(r.type, r.version, r.result) for r in arm.tick(t).installs]
            for name, arm in arms.items()
        }

    return tick


@pytest.fixture
def work(sessions, settings, clock) -> Callable[..., dict[str, str]]:
    """`work(t)`: one worker tick at `t` (gate.tick, then batch.maybe_run, as in
    `worker.STEPS`) through `run_tick`, so each step commits on its own. Fails on an error."""
    from romulus_cloud.services import batch, gate
    from romulus_cloud.worker import Step, run_tick

    def run(t: datetime, *, tools: Any = None) -> dict[str, str]:
        clock.at(t)
        steps = [
            Step("gate.tick", partial(gate.tick, settings=settings)),
            Step("batch.maybe_run", partial(batch.maybe_run, settings=settings, tools=tools)),
        ]
        results = run_tick(steps, sessions, t, {})
        assert set(results.values()) == {"ok"}, results
        return results

    return run
