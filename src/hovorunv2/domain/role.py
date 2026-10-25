"""SQLAlchemy model for user roles."""

from datetime import UTC, datetime

from sqlalchemy import BigInteger, DateTime, Enum, ForeignKey, Index, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship

from hovorunv2.domain.base import Base
from hovorunv2.domain.enums import Platform, Role
from hovorunv2.domain.user import UserDB


class UserRoleDB(Base):
    """Assigned application role for a user."""

    __tablename__ = "user_roles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    role: Mapped[Role] = mapped_column(
        Enum(Role, native_enum=False, values_callable=lambda x: [e.name.lower() for e in x]),
        nullable=False,
    )
    platform: Mapped[Platform] = mapped_column(
        Enum(Platform, native_enum=False, values_callable=lambda x: [e.value for e in x]),
        default=Platform.TELEGRAM,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )

    user: Mapped[UserDB] = relationship(UserDB, lazy="selectin")

    __table_args__ = (Index("uq_user_roles_assignment", "platform", "user_id", "role", unique=True),)
