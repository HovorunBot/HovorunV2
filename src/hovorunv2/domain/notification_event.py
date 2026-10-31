"""Immutable notification events and per-recipient delivery receipts."""

from datetime import UTC, datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from hovorunv2.domain.base import Base


class NotificationEventDB(Base):
    """One immutable logical event; the event key is the idempotency identity."""

    __tablename__ = "notification_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_key: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    introduction: Mapped[str] = mapped_column(String(96), nullable=False)
    release_identity: Mapped[str] = mapped_column(String(96), nullable=False)
    body_ref: Mapped[str | None] = mapped_column(String(32), nullable=True)
    body_markup: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )


class NotificationReceiptDB(Base):
    """One recipient's operational outcome; no message contents are retained."""

    __tablename__ = "notification_receipts"
    __table_args__ = (
        CheckConstraint("(user_id IS NULL) <> (group_id IS NULL)", name="ck_notification_receipt_one_subject"),
        UniqueConstraint("event_id", "user_id", name="uq_notification_receipt_event_user"),
        UniqueConstraint("event_id", "group_id", name="uq_notification_receipt_event_group"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("notification_events.id"), nullable=False)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    group_id: Mapped[int | None] = mapped_column(ForeignKey("chats.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
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
