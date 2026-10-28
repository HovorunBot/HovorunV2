"""Recoverable usage-policy overrides bound to exact user or group identities."""

from datetime import UTC, datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, and_, column
from sqlalchemy.orm import Mapped, mapped_column

from hovorunv2.domain.base import Base


class UsageOverrideDB(Base):
    """Ordinary usage settings; application safety caps are never stored here."""

    __tablename__ = "usage_overrides"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    group_id: Mapped[int | None] = mapped_column(ForeignKey("chats.id"), nullable=True)
    bucket: Mapped[str] = mapped_column(String(160), nullable=False)
    unit: Mapped[str] = mapped_column(String(32), nullable=False)
    window_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    limit: Mapped[int] = mapped_column(Integer, nullable=False)
    burst: Mapped[int] = mapped_column(Integer, nullable=False)
    concurrency: Mapped[int] = mapped_column(Integer, nullable=False)
    pending: Mapped[int] = mapped_column(Integer, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    __table_args__ = (
        CheckConstraint("(user_id IS NULL) <> (group_id IS NULL)", name="ck_usage_override_one_subject"),
        CheckConstraint("unit IN ('requests', 'resource_units', 'tokens')", name="ck_usage_override_unit"),
        CheckConstraint(
            "(bucket LIKE 'feature:%' AND length(bucket) > 8) OR (bucket LIKE 'family:%' AND length(bucket) > 7)",
            name="ck_usage_override_bucket",
        ),
        CheckConstraint(
            'window_seconds > 0 AND "limit" > 0 AND burst > 0 AND burst <= "limit" AND concurrency > 0 AND pending > 0',
            name="ck_usage_override_positive",
        ),
        Index(
            "uq_usage_override_user_active",
            "user_id",
            "bucket",
            "unit",
            "window_seconds",
            unique=True,
            postgresql_where=and_(column("deleted_at").is_(None), column("user_id").is_not(None)),
        ),
        Index(
            "uq_usage_override_group_active",
            "group_id",
            "bucket",
            "unit",
            "window_seconds",
            unique=True,
            postgresql_where=and_(column("deleted_at").is_(None), column("group_id").is_not(None)),
        ),
    )
