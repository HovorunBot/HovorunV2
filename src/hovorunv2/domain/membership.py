"""Minimal policy-relevant membership evidence, scoped to identity generations."""

from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from hovorunv2.domain.base import Base


class GroupMembershipStateDB(Base):
    """Freshness, bot rights and revision of a group policy's membership inputs."""

    __tablename__ = "group_membership_states"

    group_id: Mapped[int] = mapped_column(ForeignKey("chats.id"), primary_key=True)
    policy_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    admins_known: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    admins_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    admins_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    bot_status: Mapped[str] = mapped_column(String(7), nullable=False, default="unknown")
    bot_telegram_id: Mapped[int | None] = mapped_column(Integer, default=None)
    bot_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    bot_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    can_delete_messages: Mapped[bool | None] = mapped_column(Boolean, default=None)
    can_restrict_members: Mapped[bool | None] = mapped_column(Boolean, default=None)
    can_invite_users: Mapped[bool | None] = mapped_column(Boolean, default=None)
    can_pin_messages: Mapped[bool | None] = mapped_column(Boolean, default=None)
    can_manage_chat: Mapped[bool | None] = mapped_column(Boolean, default=None)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    __table_args__ = (
        CheckConstraint("bot_status IN ('unknown', 'member', 'admin', 'left')", name="ck_bot_membership_status"),
    )


class MemberEvidenceDB(Base):
    """Evidence only for administrators and unresolved former administrators."""

    __tablename__ = "member_evidence"

    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("chats.id"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(7), nullable=False)
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    admin_candidate: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    needs_verification: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    __table_args__ = (
        CheckConstraint("status IN ('unknown', 'member', 'admin', 'left')", name="ck_member_evidence_status"),
        Index("uq_member_evidence_active", "group_id", "user_id", unique=True, sqlite_where=text("deleted_at IS NULL")),
    )
