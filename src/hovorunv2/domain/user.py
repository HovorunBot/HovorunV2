"""SQLAlchemy model for a User."""

from sqlalchemy import BigInteger, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from hovorunv2.application.data.constants import UserTier
from hovorunv2.domain.base import Base


class UserDB(Base):
    """User DB model."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    platform: Mapped[str] = mapped_column(String, default="telegram", nullable=False)
    tier: Mapped[int] = mapped_column(Integer, default=int(UserTier.USER), nullable=False)

    __table_args__ = (UniqueConstraint("user_id", "platform", name="uq_user_platform"),)
