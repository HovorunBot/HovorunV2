"""Durable per-subject decisions for announced beta features."""

from datetime import UTC, datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, and_, column
from sqlalchemy.orm import Mapped, mapped_column

from hovorunv2.domain.base import Base


class BetaDecisionDB(Base):
    """One active decision per subject/feature; absence means no decision yet."""

    __tablename__ = "beta_decisions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    group_id: Mapped[int | None] = mapped_column(ForeignKey("chats.id"), nullable=True)
    feature: Mapped[str] = mapped_column(String(128), nullable=False)
    decision: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "(user_id IS NOT NULL AND group_id IS NULL) OR (user_id IS NULL AND group_id IS NOT NULL)",
            name="ck_beta_decisions_one_subject",
        ),
        CheckConstraint("decision IN ('active', 'declined')", name="ck_beta_decisions_decision"),
        CheckConstraint("length(feature) > 0", name="ck_beta_decisions_feature"),
        Index(
            "uq_beta_decisions_user_active",
            "user_id",
            "feature",
            unique=True,
            postgresql_where=and_(column("deleted_at").is_(None), column("user_id").is_not(None)),
        ),
        Index(
            "uq_beta_decisions_group_active",
            "group_id",
            "feature",
            unique=True,
            postgresql_where=and_(column("deleted_at").is_(None), column("group_id").is_not(None)),
        ),
    )
