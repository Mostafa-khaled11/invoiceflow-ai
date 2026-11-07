"""Shared fixtures for backend tests.

Postgres is used when `TEST_DATABASE_URL` is configured (the documented
integration-setup pattern); otherwise a file-backed SQLite database inside
the per-test temporary directory keeps the full suite runnable offline.
Both variants create the schema per test so no repository fixtures persist.
"""

import os
import secrets
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from app.core.config import Settings
from app.db.base import Base
from app.main import create_app
from app.models.identity import ApplicationRole, Role, UserAccountStatus
from app.services.identity_persistence import (
    assign_role,
    create_session,
    create_user_account,
    hash_session_token,
)
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session


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
def authenticated_client(
    app_settings: Settings, engine: Engine
) -> Iterator[TestClient]:
    """Return a client with an explicitly seeded synthetic Reviewer identity."""

    raw_token = secrets.token_urlsafe(32)
    now = datetime.now(UTC)
    with Session(engine) as session:
        account = create_user_account(
            session,
            email="reviewer@example.test",
            display_name="Synthetic Reviewer",
            status=UserAccountStatus.ACTIVE,
        )
        role = Role(
            name=ApplicationRole.REVIEWER.value,
            description="Synthetic test reviewer",
        )
        session.add(role)
        session.flush()
        assign_role(
            session,
            user_id=account.id,
            role=ApplicationRole.REVIEWER,
        )
        create_session(
            session,
            user_id=account.id,
            token_hash=hash_session_token(raw_token),
            created_at=now,
            last_activity_at=now,
            idle_expires_at=now + timedelta(hours=1),
            absolute_expires_at=now + timedelta(hours=8),
        )
        session.commit()

    with TestClient(create_app(app_settings)) as test_client:
        test_client.cookies.set(app_settings.auth_session_cookie_name, raw_token)
        yield test_client


@pytest.fixture
def client(authenticated_client: TestClient) -> TestClient:
    """Backward-compatible alias for the explicit synthetic Reviewer client."""

    return authenticated_client


@pytest.fixture
def anonymous_client(app_settings: Settings) -> Iterator[TestClient]:
    """Return an unauthenticated client for authorization boundary tests."""

    with TestClient(create_app(app_settings)) as test_client:
        yield test_client
