"""Declarative base for SQLAlchemy persistence models."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Shared declarative base; models register here for metadata/Alembic."""
