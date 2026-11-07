"""Base class for all domain models."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Base for SQLAlchemy models."""
