"""Durable per-subject notification category preferences."""

from datetime import UTC, datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from hovorunv2.domain.base import Base


class NotificationPreferenceDB(Base):
    """One active override per subject/category; absence means the enabled default."""

    __tablename__ = "notification_preferences"
    __table_args__ = (
        CheckConstraint(
            "(user_id IS NOT NULL AND group_id IS NULL) OR (user_id IS NULL AND group_id IS NOT NULL)",
            name="ck_notification_preference_one_subject",
        ),
        CheckConstraint(
            "category IN ('release', 'beta', 'feature_status', 'announcement')",
            name="ck_notification_preference_category",
        ),
        Index(
            "uq_notification_preference_user_active",
            "user_id",
            "category",
            unique=True,
            postgresql_where=text("user_id IS NOT NULL AND deleted_at IS NULL"),
        ),
        Index(
            "uq_notification_preference_group_active",
            "group_id",
            "category",
            unique=True,
            postgresql_where=text("group_id IS NOT NULL AND deleted_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    group_id: Mapped[int | None] = mapped_column(ForeignKey("chats.id"), nullable=True)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
