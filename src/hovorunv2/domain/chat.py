"""SQLAlchemy model for a Chat."""

from sqlalchemy import JSON, Column, Enum, ForeignKey, Integer, String, Table
from sqlalchemy.orm import Mapped, mapped_column, relationship

from hovorunv2.domain.base import Base
from hovorunv2.domain.command import CommandDB
from hovorunv2.domain.enums import ChatStatus, Platform

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
    status: Mapped[ChatStatus] = mapped_column(
        Enum(ChatStatus, native_enum=False, values_callable=lambda x: [e.value for e in x]),
        default=ChatStatus.UNAUTHORIZED,
        nullable=False,
    )
    platform: Mapped[Platform] = mapped_column(
        Enum(Platform, native_enum=False, values_callable=lambda x: [e.value for e in x]),
        default=Platform.TELEGRAM,
        nullable=False,
    )
    target_lang: Mapped[str | None] = mapped_column(String, nullable=True)
    ignored_langs: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    encryption_salt: Mapped[str | None] = mapped_column(String, nullable=True)

    # M2M Relationship
    commands: Mapped[list[CommandDB]] = relationship(secondary=chat_commands, back_populates="chats")
