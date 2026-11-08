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
from romulus_cloud.services.seed import seed

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
def count_events(db_session: Session) -> Callable[..., int]:
    """`count_events("install.reported", {"device": "arm-1"})` → how many such events exist."""

    def count(kind: str, refs: dict[str, Any] | None = None) -> int:
        stmt = select(func.count()).select_from(Event).where(Event.kind == kind)
        if refs:
            stmt = stmt.where(Event.refs.contains(refs))
        return db_session.scalar(stmt) or 0

    return count
