"""SQLAlchemy model for lifecycle-aware user identity."""

from datetime import UTC, datetime

from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, Enum, Index, Integer, String, column
from sqlalchemy.orm import Mapped, mapped_column

from hovorunv2.domain.base import Base
from hovorunv2.domain.enums import Platform, Role
from hovorunv2.domain.role_type import RoleType


class UserDB(Base):
    """Lifecycle-aware user entity recording stable numeric Telegram identity."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    platform: Mapped[Platform] = mapped_column(
        Enum(Platform, native_enum=False, values_callable=lambda x: [e.value for e in x]),
        default=Platform.TELEGRAM,
        nullable=False,
    )
    telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    role: Mapped[Role] = mapped_column(
        RoleType(),
        default=Role.USER,
        server_default=str(Role.USER.value),
        nullable=False,
    )
    is_trusted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    trust_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    generation: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None)
    deleted_by_actor_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, default=None)
    deleted_reason: Mapped[str | None] = mapped_column(String, nullable=True, default=None)

    __table_args__ = (
        Index(
            "uq_users_active_identity",
            "platform",
            "telegram_id",
            unique=True,
            postgresql_where=column("deleted_at").is_(None),
        ),
        CheckConstraint("role IN (1, 10, 100)", name="ck_users_valid_role"),
    )
