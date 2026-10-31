"""SQLAlchemy model and normalization for shared public handles."""

import re
from datetime import UTC, datetime

from sqlalchemy import BigInteger, DateTime, Enum, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from hovorunv2.domain.base import Base
from hovorunv2.domain.enums import Platform, TargetKind

_HANDLE_PATTERN = re.compile(r"^[a-zA-Z0-9_]{1,64}$")


class InvalidHandleError(ValueError):
    """Raised when a public handle has invalid format."""

    def __init__(self, handle: str) -> None:
        """Record the offending handle on the error."""
        super().__init__(f"Invalid public handle format: {handle}")
        self.handle = handle


class UnknownHandleError(ValueError):
    """Raised when an unknown username cannot be resolved to any numeric identity."""

    def __init__(self, handle: str) -> None:
        """Record the unresolved handle on the error."""
        super().__init__(f"Unknown public handle: {handle}")
        self.handle = handle


def normalize_handle(handle: str | None) -> str | None:
    """Normalize a public handle for storage and comparison.

    Args:
        handle: Raw handle text, optionally with a leading @.

    Returns:
        Lowercased handle without a leading @, or None when the input is empty.
    """
    if not handle:
        return None
    cleaned = handle.strip().lstrip("@")
    if not cleaned:
        return None
    return cleaned.lower()


def validate_handle(handle: str) -> bool:
    """Return whether a public handle matches the allowed format.

    Args:
        handle: Raw handle text, optionally with a leading @.

    Returns:
        True when the stripped handle is alphanumeric or underscore only, 1-64 characters long.
    """
    cleaned = handle.strip().lstrip("@")
    return bool(_HANDLE_PATTERN.fullmatch(cleaned))


class PublicHandleDB(Base):
    """Shared active public-handle registry model."""

    __tablename__ = "public_handles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    handle_normalized: Mapped[str] = mapped_column(String(64), nullable=False)
    handle_display: Mapped[str] = mapped_column(String(64), nullable=False)
    target_kind: Mapped[TargetKind] = mapped_column(
        Enum(TargetKind, native_enum=False, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
    )
    target_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    platform: Mapped[Platform] = mapped_column(
        Enum(Platform, native_enum=False, values_callable=lambda x: [e.value for e in x]),
        default=Platform.TELEGRAM,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )

    __table_args__ = (
        Index("uq_public_handles_handle", "platform", "handle_normalized", unique=True),
        Index("uq_public_handles_target", "platform", "target_kind", "target_id", unique=True),
    )
