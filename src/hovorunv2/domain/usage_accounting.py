"""Append-only accounting receipts and stable-subject aggregate counters."""

from datetime import UTC, datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from hovorunv2.domain.base import Base


class UsageAggregateDB(Base):
    """Nondeletable accounting retained across user/group generation changes."""

    __tablename__ = "usage_aggregates"

    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[str] = mapped_column(String(32), nullable=False, default="telegram")
    subject_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    bucket: Mapped[str] = mapped_column(String(160), nullable=False)
    unit: Mapped[str] = mapped_column(String(32), nullable=False)
    window_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    window_start: Mapped[int] = mapped_column(BigInteger, nullable=False)
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "platform",
            "subject_kind",
            "telegram_id",
            "bucket",
            "unit",
            "window_seconds",
            "window_start",
            name="uq_usage_aggregate_window",
        ),
        CheckConstraint("platform = 'telegram'", name="ck_usage_aggregate_platform"),
        CheckConstraint(
            "(subject_kind = 'user' AND telegram_id > 0) OR (subject_kind = 'group' AND telegram_id < 0)",
            name="ck_usage_aggregate_subject",
        ),
        CheckConstraint("unit IN ('requests', 'resource_units', 'tokens')", name="ck_usage_aggregate_unit"),
        CheckConstraint(
            "window_seconds > 0 AND window_start >= 0 AND window_start % window_seconds = 0 AND amount > 0",
            name="ck_usage_aggregate_positive",
        ),
    )


class UsageAccountingEventDB(Base):
    """Technical replay receipt; no message, payload, profile or executable job fields."""

    __tablename__ = "usage_accounting_events"

    event_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    __table_args__ = (
        CheckConstraint("length(event_id) BETWEEN 1 AND 128", name="ck_usage_accounting_event_id"),
        CheckConstraint("length(fingerprint) = 64", name="ck_usage_accounting_fingerprint"),
    )
