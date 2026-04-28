"""SQLAlchemy model for a Chat."""

from sqlalchemy import Column, ForeignKey, Integer, String, Table
from sqlalchemy.orm import Mapped, mapped_column, relationship

from hovorunv2.domain import Base
from hovorunv2.domain.command import CommandDB

# Association table for M2M relationship between Chats and Commands
chat_commands = Table(
    "chat_commands",
    Base.metadata,
    Column("chat_id", Integer, ForeignKey("chats.id"), primary_key=True),
    Column("command_id", Integer, ForeignKey("commands.id"), primary_key=True),
)


class ChatDB(Base):
    """Chat DB model."""

    __tablename__ = "chats"

    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[int] = mapped_column(nullable=False)
    is_whitelisted: Mapped[bool] = mapped_column(default=False)
    platform: Mapped[str] = mapped_column(String, default="telegram")
    target_lang: Mapped[str | None] = mapped_column(String, nullable=True)
    ignored_langs: Mapped[str | None] = mapped_column(String, nullable=True)  # Comma-separated or JSON

    # M2M Relationship
    commands: Mapped[list[CommandDB]] = relationship(secondary=chat_commands, back_populates="chats")
