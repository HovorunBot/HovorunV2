"""Encrypted, revisioned announcement drafts composed by bot administrators."""

from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, and_, column
from sqlalchemy.orm import Mapped, mapped_column

from hovorunv2.domain.base import Base


class AnnouncementAudience(StrEnum):
    """Draft audience shape resolved into concrete recipients at preview time."""

    ALL = "all"
    USERS = "users"
    GROUPS = "groups"
    SELECTED = "selected"


class AnnouncementDraftDB(Base):
    """Keep only ciphertext and safe metadata; the authored body never enters a column in plaintext."""

    __tablename__ = "announcement_drafts"

    id: Mapped[int] = mapped_column(primary_key=True)
    author_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    audience: Mapped[str] = mapped_column(String(16), nullable=False)
    body_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    body_salt: Mapped[str] = mapped_column(String(64), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    preview_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "category IN ('release', 'beta', 'feature_status', 'announcement')",
            name="ck_announcement_drafts_category",
        ),
        CheckConstraint(
            "audience IN ('all', 'users', 'groups', 'selected')",
            name="ck_announcement_drafts_audience",
        ),
        CheckConstraint("revision >= 1", name="ck_announcement_drafts_revision"),
        CheckConstraint("length(body_ciphertext) > 0", name="ck_announcement_drafts_body"),
    )


class AnnouncementTargetDB(Base):
    """Selected internal identities for a draft; no usernames or profiles."""

    __tablename__ = "announcement_targets"

    id: Mapped[int] = mapped_column(primary_key=True)
    draft_id: Mapped[int] = mapped_column(ForeignKey("announcement_drafts.id"), nullable=False)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    group_id: Mapped[int | None] = mapped_column(ForeignKey("chats.id"), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "(user_id IS NOT NULL AND group_id IS NULL) OR (user_id IS NULL AND group_id IS NOT NULL)",
            name="ck_announcement_targets_one_subject",
        ),
        Index(
            "uq_announcement_targets_user_active",
            "draft_id",
            "user_id",
            unique=True,
            postgresql_where=and_(column("deleted_at").is_(None), column("user_id").is_not(None)),
        ),
        Index(
            "uq_announcement_targets_group_active",
            "draft_id",
            "group_id",
            unique=True,
            postgresql_where=and_(column("deleted_at").is_(None), column("group_id").is_not(None)),
        ),
    )
