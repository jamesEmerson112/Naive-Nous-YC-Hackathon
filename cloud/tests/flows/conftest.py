"""Shared setup for flow tests: the seeded fleet and the contract example payloads."""

import copy
import json
from collections.abc import Callable
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
