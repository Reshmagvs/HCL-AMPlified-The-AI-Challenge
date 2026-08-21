"""Shared test fixtures.

Every test runs against a throwaway SQLite file and the mock provider, so the
suite needs no network and no API key. ``LODESTAR`` environment variables are
set before any application module is imported, because ``get_settings`` is
cached for the life of the process.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

os.environ.setdefault("LLM_PROVIDER", "mock")
os.environ.setdefault("GEMINI_API_KEY", "")

_TEST_DB = BACKEND_DIR / "tests" / "_test_lodestar.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB.as_posix()}"

# Discovered subjects are the one piece of application state that lives on disk
# outside the database. Tests clear it between cases, so it must never point at
# a real installation -- a suite run once deleted every subject a user had built.
_TEST_GENERATED = BACKEND_DIR / "tests" / "_test_generated"
os.environ["GENERATED_DIR"] = str(_TEST_GENERATED)


def _remove_database() -> None:
    """Delete the test database and its WAL sidecars, best effort.

    Windows keeps a handle open until the SQLAlchemy pool is disposed, and even
    then antivirus can hold it briefly -- a leftover file is harmless because
    the next session recreates it, so failure here must never fail the suite.
    """
    for suffix in ("", "-wal", "-shm"):
        try:
            Path(str(_TEST_DB) + suffix).unlink(missing_ok=True)
        except PermissionError:
            pass


@pytest.fixture(scope="session", autouse=True)
def _clean_database() -> Iterator[None]:
    """Start each session from an empty database and leave nothing behind."""
    _remove_database()
    yield
    from app.db import engine

    engine.dispose()
    _remove_database()


@pytest.fixture(scope="session")
def client():
    """A TestClient with the application lifespan actually executed."""
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(scope="session")
def graph():
    from app.core.skill_graph import load_graph

    return load_graph()


@pytest.fixture(scope="session")
def catalog():
    from app.core.retrieval import load_catalog

    return load_catalog()
