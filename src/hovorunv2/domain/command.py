"""SQLAlchemy model for a Command."""

from typing import TYPE_CHECKING

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from hovorunv2.domain import Base

if TYPE_CHECKING:
    from hovorunv2.domain import ChatDB


class CommandDB(Base):
    """Command DB model."""

    __tablename__ = "commands"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(String, nullable=True)

    # Relationship
    chats: Mapped[list[ChatDB]] = relationship(secondary="chat_commands", back_populates="commands")
