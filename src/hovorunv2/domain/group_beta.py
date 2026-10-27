"""Whole-group beta selection and the administrators supporting it."""

from datetime import UTC, datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from hovorunv2.domain.base import Base


class GroupBetaFeatureDB(Base):
    """Retain selected state independently of temporary eligibility suspension."""

    __tablename__ = "group_beta_features"

    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("chats.id"), nullable=False)
    feature: Mapped[str] = mapped_column(String(128), nullable=False)
    selected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("0"))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint("length(feature) > 0", name="ck_group_beta_features_feature"),
        Index(
            "uq_group_beta_features_active",
            "group_id",
            "feature",
            unique=True,
            sqlite_where=text("deleted_at IS NULL"),
        ),
    )


class GroupBetaEnablerDB(Base):
    """Record intentional administrator support without copying membership or eligibility."""

    __tablename__ = "group_beta_enablers"

    id: Mapped[int] = mapped_column(primary_key=True)
    selection_id: Mapped[int] = mapped_column(ForeignKey("group_beta_features.id"), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index(
            "uq_group_beta_enablers_active",
            "selection_id",
            "user_id",
            unique=True,
            sqlite_where=text("revoked_at IS NULL AND deleted_at IS NULL"),
        ),
    )
