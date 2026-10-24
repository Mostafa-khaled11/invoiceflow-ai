"""SQLAlchemy connection layer.

The engine is intentionally created lazily so the health endpoint remains useful
when the database is unavailable or not configured.
"""

from collections.abc import Generator
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session

from app.core.config import Settings


@lru_cache(maxsize=4)
def _create_engine(database_url: str) -> Engine:
    return create_engine(database_url, pool_pre_ping=True)


def get_engine(settings: Settings) -> Engine:
    """Build or retrieve the SQLAlchemy engine for the configured database."""

    if not settings.database_url:
        raise RuntimeError("DATABASE_URL is not configured")
    return _create_engine(settings.database_url)


def get_db_session(settings: Settings) -> Generator[Session, None, None]:
    """Yield a SQLAlchemy session for future API routes."""

    with Session(get_engine(settings)) as session:
        yield session
