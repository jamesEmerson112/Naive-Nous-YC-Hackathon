"""Shared fixtures for every cloud test.

- One throwaway database per pytest session, on the shared `romulus-testdb` container
  (`TEST_DATABASE_URL` is the admin URL). Created lazily, only if a test needs the DB,
  and dropped at the end, so parallel sessions never collide.
- Each test gets one connection with an outer transaction that is rolled back afterwards.
  Every session in the test (`db_session`, each request's session, worker steps run via
  `sessions`) joins it with `join_transaction_mode="create_savepoint"`, so `commit()` only
  releases a savepoint: they all see each other's writes and nothing leaks between tests.
- Test settings read neither `.env` nor the process environment, every adapter is forced
  to fake, and an autouse fixture unsets every settings variable for the test, so a real
  key in `.env` or the shell can't make a test call out.
"""

import os
import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource
from sqlalchemy import Connection, Engine, create_engine, make_url, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from romulus_cloud import db
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


class _TestSettings(Settings):
    """Settings built from constructor arguments only: no env vars, no `.env`."""

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (init_settings,)


@pytest.fixture(autouse=True)
def _no_settings_env(monkeypatch):
    """Unset every settings variable (and FORCE), so code that calls `Settings()` directly
    can't pick up an exported shell variable either."""
    for name in [*Settings.model_fields, "force"]:
        monkeypatch.delenv(name.upper(), raising=False)


@pytest.fixture
def make_settings(tmp_path) -> Callable[..., Settings]:
    """Build test settings: no env, no `.env`, fake adapters, tmp data dirs. Overrides win,
    except that adapter modes are always fake."""

    def make(**overrides) -> Settings:
        values = {
            "database_url": NO_DB_URL,
            "blob_dir": str(tmp_path / "blobs"),
            "export_dir": str(tmp_path / "exports"),
            **overrides,
            **FAKE_MODES,
        }
        return _TestSettings(**values)

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
def db_connection(engine) -> Iterator[Connection]:
    """The test's one connection, inside an outer transaction rolled back at the end."""
    conn = engine.connect()
    outer = conn.begin()
    try:
        yield conn
    finally:
        outer.rollback()
        conn.close()


@pytest.fixture
def sessions(db_connection) -> Callable[[], Session]:
    """A sessionmaker stand-in bound to the test's transaction. Pass it to
    `worker.run_tick`; the `app` fixture also uses it for every request."""

    def make() -> Session:
        return Session(
            bind=db_connection, join_transaction_mode="create_savepoint", expire_on_commit=False
        )

    return make


@pytest.fixture
def db_session(sessions) -> Iterator[Session]:
    session = sessions()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def app(settings, engine, sessions, test_database_url) -> FastAPI:
    """The real app on the test DB; each request's session joins the test's transaction."""
    application = create_app(
        settings.model_copy(update={"database_url": test_database_url}), engine
    )
    application.state.sessionmaker = sessions
    return application


@pytest.fixture
def client(app) -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c
