"""SQLAlchemy model for a Chat."""

from sqlalchemy import String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base for SQLAlchemy models."""


class ChatDB(Base):
    """Chat DB model."""

    __tablename__ = "chats"

    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[int] = mapped_column(nullable=False)
    is_whitelisted: Mapped[bool] = mapped_column(default=False)
    platform: Mapped[str] = mapped_column(String, default="telegram")
    target_lang: Mapped[str | None] = mapped_column(String, nullable=True)
    ignored_langs: Mapped[str | None] = mapped_column(String, nullable=True)  # Comma-separated or JSON
