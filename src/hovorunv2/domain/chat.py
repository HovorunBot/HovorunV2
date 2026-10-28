"""SQLAlchemy model for a Chat."""

from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    column,
    false,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from hovorunv2.domain.base import Base
from hovorunv2.domain.command import CommandDB
from hovorunv2.domain.enums import ChatStatus, Platform

# Association table for M2M relationship between Chats and Commands
chat_commands = Table(
    "chat_commands",
    Base.metadata,
    Column("chat_id", Integer, ForeignKey("chats.id", ondelete="CASCADE"), primary_key=True),
    Column("command_id", Integer, ForeignKey("commands.id", ondelete="CASCADE"), primary_key=True),
)


class ChatDB(Base):
    """Chat DB model with lifecycle metadata and active identity uniqueness."""

    __tablename__ = "chats"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
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
    title: Mapped[str | None] = mapped_column(String, nullable=True)
    chat_type: Mapped[str | None] = mapped_column(String(32), nullable=True, default="group")
    is_bot_present: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    bot_capabilities_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
    )
    generation: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    legacy_empty_capture: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    features_configured: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())
    target_lang: Mapped[str | None] = mapped_column(String, nullable=True)
    ignored_langs: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    encryption_salt: Mapped[str | None] = mapped_column(String, nullable=True)
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

    # M2M Relationship
    commands: Mapped[list[CommandDB]] = relationship(secondary=chat_commands, back_populates="chats")

    __table_args__ = (
        Index(
            "uq_chats_active_identity",
            "platform",
            "chat_id",
            unique=True,
            postgresql_where=column("deleted_at").is_(None),
        ),
    )
