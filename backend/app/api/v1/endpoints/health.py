"""Service liveness and database readiness endpoints."""

from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import Settings
from app.db.session import get_engine

router = APIRouter()


@router.get("/health", summary="Check API liveness")
def health(request: Request) -> dict[str, str]:
    """Return a lightweight liveness response without requiring PostgreSQL."""

    settings: Settings = request.app.state.settings
    return {"status": "ok", "service": settings.app_name}


@router.get("/ready", summary="Check API and PostgreSQL readiness")
def ready(request: Request) -> dict[str, Any]:
    """Verify that the configured PostgreSQL database accepts a connection."""

    settings: Settings = request.app.state.settings
    try:
        engine = get_engine(settings)
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except (SQLAlchemyError, OSError, RuntimeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="PostgreSQL is not ready",
        ) from exc

    return {"status": "ok", "service": settings.app_name, "database": "ok"}
