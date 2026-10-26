"""Recoverable beta entitlements, independent of trust and selected activation."""

from datetime import UTC, datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from hovorunv2.domain.base import Base

ALL_BETA = "*"


class BetaEntitlementDB(Base):
    """Grant one feature or all beta features to one lifecycle identity."""

    __tablename__ = "beta_entitlements"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    group_id: Mapped[int | None] = mapped_column(ForeignKey("chats.id"), nullable=True)
    feature: Mapped[str] = mapped_column(String(128), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "(user_id IS NOT NULL AND group_id IS NULL) OR (user_id IS NULL AND group_id IS NOT NULL)",
            name="ck_beta_entitlements_one_subject",
        ),
        CheckConstraint("group_id IS NULL OR feature <> '*'", name="ck_beta_entitlements_group_feature"),
        CheckConstraint("length(feature) > 0", name="ck_beta_entitlements_feature"),
        Index(
            "uq_beta_entitlements_user_active",
            "user_id",
            "feature",
            unique=True,
            sqlite_where=text("revoked_at IS NULL AND deleted_at IS NULL AND user_id IS NOT NULL"),
        ),
        Index(
            "uq_beta_entitlements_group_active",
            "group_id",
            "feature",
            unique=True,
            sqlite_where=text("revoked_at IS NULL AND deleted_at IS NULL AND group_id IS NOT NULL"),
        ),
    )
