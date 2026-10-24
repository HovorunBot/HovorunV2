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
        """Initialize with invalid handle."""
        super().__init__(f"Invalid public handle format: {handle}")
        self.handle = handle


class UnknownHandleError(ValueError):
    """Raised when an unknown username cannot be resolved to any numeric identity."""

    def __init__(self, handle: str) -> None:
        """Initialize with unresolved handle."""
        super().__init__(f"Unknown public handle: {handle}")
        self.handle = handle


def normalize_handle(handle: str | None) -> str | None:
    """Normalize public handle by stripping @ and lowercasing."""
    if not handle:
        return None
    cleaned = handle.strip().lstrip("@")
    if not cleaned:
        return None
    return cleaned.lower()


def validate_handle(handle: str) -> bool:
    """Validate public handle format (alphanumeric and underscore, 1-64 chars)."""
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
