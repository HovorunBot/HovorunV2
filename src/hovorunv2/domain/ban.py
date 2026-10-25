"""Independent stable-subject access restrictions and their mutation history."""

from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import BigInteger, CheckConstraint, DateTime, Index, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from hovorunv2.domain.base import Base


class BanKind(StrEnum):
    """Independent personal and full restrictions."""

    PERSONAL = "personal"
    FULL = "full"


class BanDB(Base):
    """Restriction bound to numeric identity so fresh generations cannot evade it."""

    __tablename__ = "bans"

    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[str] = mapped_column(String(32), nullable=False, default="telegram")
    target_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    internal_reason: Mapped[str] = mapped_column(String, nullable=False)
    public_explanation: Mapped[str | None] = mapped_column(String, nullable=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint("target_kind IN ('user', 'group')", name="ck_bans_target"),
        CheckConstraint(
            "kind IN ('personal', 'full') AND (target_kind = 'user' OR kind = 'full')", name="ck_bans_kind"
        ),
        Index(
            "uq_bans_active",
            "platform",
            "target_kind",
            "telegram_id",
            "kind",
            unique=True,
            sqlite_where=text("revoked_at IS NULL AND deleted_at IS NULL"),
        ),
    )
