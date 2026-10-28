"""Independent group approval and access-request history."""

from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import BigInteger, DateTime, Enum, ForeignKey, Index, String, and_, column
from sqlalchemy.orm import Mapped, mapped_column

from hovorunv2.domain.base import Base


class ApprovalBasis(StrEnum):
    """Provenance of membership-independent approval."""

    EXPLICIT = "explicit"
    LEGACY = "legacy"


class RequestStatus(StrEnum):
    """Request lifecycle independent of effective access and bans."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"


class GrantLifecycle:
    """Common lifecycle metadata; parent identity generations remain immutable."""

    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("chats.id"), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class GroupApprovalDB(GrantLifecycle, Base):
    """Retained explicit or migration-origin access basis."""

    __tablename__ = "group_approvals"

    basis: Mapped[ApprovalBasis] = mapped_column(
        Enum(
            ApprovalBasis,
            native_enum=False,
            create_constraint=True,
            values_callable=lambda values: [v.value for v in values],
        ),
        nullable=False,
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    __table_args__ = (
        Index(
            "uq_group_approvals_active",
            "group_id",
            "basis",
            unique=True,
            postgresql_where=and_(column("deleted_at").is_(None), column("revoked_at").is_(None)),
        ),
    )


class GroupAccessRequestDB(GrantLifecycle, Base):
    """An independent request and its decision; rejection never implies a ban."""

    __tablename__ = "group_access_requests"

    requested_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    status: Mapped[RequestStatus] = mapped_column(
        Enum(
            RequestStatus,
            native_enum=False,
            create_constraint=True,
            values_callable=lambda values: [v.value for v in values],
        ),
        nullable=False,
        default=RequestStatus.PENDING,
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    decision_actor_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, default=None)
    decision_reason: Mapped[str | None] = mapped_column(String, nullable=True, default=None)

    __table_args__ = (
        Index(
            "uq_group_requests_pending",
            "group_id",
            unique=True,
            postgresql_where=and_(
                column("deleted_at").is_(None), column("status", String()) == RequestStatus.PENDING.value
            ),
        ),
    )
