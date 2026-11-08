"""SQLAlchemy engine and sessions; `create_all()` on startup, no migrations (architecture §7).

Transaction rule: services never commit. Routes and worker steps commit once at the end,
so a write and its Event land together or not at all.
"""

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from romulus_cloud.models import Base

# Any constant works; it only serialises concurrent create_all calls from api and worker.
_SCHEMA_LOCK = 7_301_917


def make_engine(url: str) -> Engine:
    return create_engine(url, pool_pre_ping=True)


def make_sessionmaker(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


def create_all(engine: Engine) -> None:
    """Create missing tables. Safe to call from api and worker at the same time."""
    with engine.begin() as conn:
        conn.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _SCHEMA_LOCK})
        Base.metadata.create_all(conn)


def drop_all(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _SCHEMA_LOCK})
        Base.metadata.drop_all(conn)
