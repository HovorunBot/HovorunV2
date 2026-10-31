"""Durable authoritative feature emergency stop state with a monotonically changing epoch."""

from datetime import UTC, datetime

from sqlalchemy import BigInteger, Boolean, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from hovorunv2.domain.base import Base


class FeatureEmergencyDB(Base):
    """One authoritative row per feature; every transition advances the execution epoch."""

    __tablename__ = "feature_emergency_states"

    id: Mapped[int] = mapped_column(primary_key=True)
    feature_key: Mapped[str] = mapped_column(String(96), nullable=False, unique=True)
    disabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    reason: Mapped[str | None] = mapped_column(String(512), nullable=True)
    public_reason: Mapped[str | None] = mapped_column(String(512), nullable=True)
    epoch: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
