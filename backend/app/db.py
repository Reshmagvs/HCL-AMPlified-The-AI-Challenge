"""Database engine, schema creation and the FastAPI session dependency.

SQLite is the default store. Two pragmas matter for this workload:

* ``journal_mode=WAL`` -- the app writes an ``Event`` row on nearly every
  request while the dashboard reads concurrently. Without WAL, SQLite's default
  rollback journal takes a global write lock and concurrent path generations
  raise "database is locked".
* ``foreign_keys=ON`` -- SQLite ignores foreign keys unless asked, which would
  let orphaned ``PathItem`` rows accumulate silently.

Both are applied per-connection via a SQLAlchemy ``connect`` event so pooled
connections created later in the process get them too.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine

from app.config import get_settings

logger = logging.getLogger(__name__)

_settings = get_settings()
_is_sqlite = _settings.database_url.startswith("sqlite")

engine: Engine = create_engine(
    _settings.database_url,
    echo=False,
    connect_args={"check_same_thread": False, "timeout": 30} if _is_sqlite else {},
)


@event.listens_for(engine, "connect")
def _apply_sqlite_pragmas(dbapi_connection, _connection_record) -> None:
    """Enable WAL and foreign keys on every new SQLite connection."""
    if not _is_sqlite:
        return
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA busy_timeout=30000")
    cursor.close()


def init_db() -> None:
    """Create every table declared on SQLModel's metadata."""
    import app.models  # noqa: F401  -- registers the tables as a side effect

    SQLModel.metadata.create_all(engine)
    logger.info("database ready at %s", _settings.database_url)


def get_session() -> Iterator[Session]:
    """FastAPI dependency yielding a scoped session."""
    with Session(engine) as session:
        yield session
