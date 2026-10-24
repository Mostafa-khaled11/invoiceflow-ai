"""Shared fixtures for backend tests.

Postgres is used when `TEST_DATABASE_URL` is configured (the documented
integration-setup pattern); otherwise a file-backed SQLite database inside
the per-test temporary directory keeps the full suite runnable offline.
Both variants create the schema per test so no repository fixtures persist.
"""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from app.core.config import Settings
from app.db.base import Base
from app.main import create_app
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine


@pytest.fixture
def database_url(tmp_path: Path) -> str:
    """Return the database URL for this test (Postgres when configured).

    The URL string is reused verbatim for both engine creation and app
    settings; `str(engine.url)` would mask the password and break auth.
    """

    database_url = os.getenv("TEST_DATABASE_URL")
    if database_url:
        return database_url
    database_path = tmp_path / "invoiceflow_test.db"
    return f"sqlite+pysqlite:///{database_path.as_posix()}"


@pytest.fixture
def engine(database_url: str) -> Iterator[Engine]:
    """Create an isolated engine with the invoice schema for one test."""

    engine = create_engine(database_url, pool_pre_ping=True)

    Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture
def storage_root(tmp_path: Path) -> Path:
    """Return a private per-test storage root (never inside the repo)."""

    return tmp_path / "invoice-storage"


@pytest.fixture
def app_settings(
    database_url: str,
    engine: Engine,
    storage_root: Path,
) -> Settings:
    """Return app settings wired to the isolated database and storage."""

    return Settings(
        database_url=database_url,
        invoice_storage_path=storage_root,
    )


@pytest.fixture
def client(app_settings: Settings) -> TestClient:
    """Return a test client for the fully wired application."""

    return TestClient(create_app(app_settings))
