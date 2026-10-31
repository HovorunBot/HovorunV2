"""SQLAlchemy model for a Command."""

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from hovorunv2.domain.base import Base


class CommandDB(Base):
    """Command DB model."""

    __tablename__ = "commands"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(String, nullable=True)
