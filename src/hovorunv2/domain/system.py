"""SQLAlchemy model for global system state."""

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from hovorunv2.domain.base import Base


class SystemStateDB(Base):
    """System state model for storing global bot configuration/state."""

    __tablename__ = "system_state"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    value: Mapped[str] = mapped_column(String, nullable=False)
