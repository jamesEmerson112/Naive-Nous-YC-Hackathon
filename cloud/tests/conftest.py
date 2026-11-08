"""Shared fixtures for every cloud test.

- One throwaway database per pytest session, on the shared `romulus-testdb` container
  (`TEST_DATABASE_URL` is the admin URL). Created lazily, only if a test needs the DB,
  and dropped at the end, so parallel sessions never collide.
- Each test runs inside an outer transaction that is rolled back afterwards. Code under
  test may call `session.commit()`; with `join_transaction_mode="create_savepoint"` that
  only releases a savepoint, so nothing leaks between tests.
- Settings never read `.env` and every adapter is forced to fake mode, so a real key in
  `.env` or the shell can't make a test call out.
"""

import os
import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, make_url, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from romulus_cloud import db
from romulus_cloud.api.deps import get_session
from romulus_cloud.main import create_app
from romulus_cloud.settings import Settings

ADMIN_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://romulus:romulus@localhost:55432/postgres"
)
NO_DB_URL = "postgresql+psycopg://unused:unused@127.0.0.1:1/unused"

FAKE_MODES = {
    "naive_mode": "fake",
    "llm_mode": "fake",
    "llm_provider": "fake",
    "coder_provider": "fake",
}


@pytest.fixture
def make_settings(tmp_path) -> Callable[..., Settings]:
    """Build test settings: no `.env`, fake adapters, tmp data dirs. Overrides win."""

    def make(**overrides) -> Settings:
        values = {
            "database_url": NO_DB_URL,
            "blob_dir": str(tmp_path / "blobs"),
            "export_dir": str(tmp_path / "exports"),
            **overrides,
            **FAKE_MODES,
        }
        return Settings(_env_file=None, **values)

    return make


@pytest.fixture
def settings(make_settings) -> Settings:
    return make_settings()


@pytest.fixture
def now() -> datetime:
    """A fixed, tz-aware instant for tests that pass `now` around."""
    return datetime(2026, 11, 7, 22, 0, 0, tzinfo=UTC)


@pytest.fixture(scope="session")
def test_database_url() -> Iterator[str]:
    name = f"romulus_test_{os.getpid()}_{uuid.uuid4().hex[:8]}"
    admin = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{name}"'))
    except OperationalError as exc:
        raise RuntimeError(
            f"Test Postgres not reachable at {ADMIN_URL}. Run `make test`, or "
            "`docker start romulus-testdb`."
        ) from exc
    url = make_url(ADMIN_URL).set(database=name).render_as_string(hide_password=False)
    try:
        yield url
    finally:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


@pytest.fixture(scope="session")
def engine(test_database_url) -> Iterator[Engine]:
    eng = db.make_engine(test_database_url)
    db.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def db_session(engine) -> Iterator[Session]:
    conn = engine.connect()
    outer = conn.begin()
    session = Session(bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False)
    try:
        yield session
    finally:
        session.close()
        outer.rollback()
        conn.close()


@pytest.fixture
def app(settings, engine, db_session, test_database_url) -> FastAPI:
    """The real app on the test DB; every request shares the test's `db_session`."""
    application = create_app(
        settings.model_copy(update={"database_url": test_database_url}), engine
    )

    def _session() -> Iterator[Session]:
        yield db_session

    application.dependency_overrides[get_session] = _session
    return application


@pytest.fixture
def client(app) -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c
