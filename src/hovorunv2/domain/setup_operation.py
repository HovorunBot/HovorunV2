"""SQLAlchemy model for idempotent, bounded guided chat configuration apply outcomes."""

from datetime import UTC, datetime

from sqlalchemy import JSON, BigInteger, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from hovorunv2.domain.base import Base


class SetupOperationDB(Base):
    """Persist bounded idempotency outcomes for atomic guided setup confirmation."""

    __tablename__ = "setup_apply_operations"

    operation_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    chat_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("chats.id", ondelete="CASCADE"), nullable=False, index=True
    )
    chat_generation: Mapped[int] = mapped_column(Integer, nullable=False)
    actor_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    applied_values: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )
