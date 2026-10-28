"""SQLAlchemy model and contracts for append-only audit events."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

from sqlalchemy import JSON, BigInteger, DateTime, Enum, String
from sqlalchemy.orm import Mapped, mapped_column

from hovorunv2.domain.base import Base
from hovorunv2.domain.enums import ActorOrigin

DISALLOWED_AUDIT_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "encryption_salt",
        "cache_encryption_key",
        "password",
        "secret",
        "token",
        "ciphertext",
        "raw_payload",
        "message_text",
        "text",
        "body",
        "url",
    }
)

SENSITIVE_FIELD_MARKER: Final = "[REDACTED_CHANGED]"


def sanitize_audit_changes(changes: dict[str, Any] | None) -> dict[str, Any] | None:
    """Sanitize changed fields by redacting secret and private payload material."""
    if not changes:
        return changes

    sanitized: dict[str, Any] = {}
    for key, val in changes.items():
        lower_key = key.lower()
        if (
            lower_key in DISALLOWED_AUDIT_FIELDS
            or "salt" in lower_key
            or "token" in lower_key
            or "key" in lower_key
            or "secret" in lower_key
        ):
            sanitized[key] = SENSITIVE_FIELD_MARKER
            continue
        sanitized[key] = _sanitize_audit_value(val)
    return sanitized


def _sanitize_audit_value(value: object) -> object:
    """Traverse nested audit containers without changing the original payload."""
    match value:
        case dict():
            return sanitize_audit_changes(value)
        case list():
            return [_sanitize_audit_value(item) for item in value]
        case tuple():
            return tuple(_sanitize_audit_value(item) for item in value)
        case _:
            return value


@dataclass(frozen=True, slots=True)
class ActorContext:
    """Actor context representing who or what initiated an audited mutation."""

    origin: ActorOrigin
    actor_id: int | None = None
    reason: str | None = None
    correlation_id: str | None = None

    @classmethod
    def system(cls, reason: str | None = None, correlation_id: str | None = None) -> ActorContext:
        """Create a system actor context."""
        return cls(origin=ActorOrigin.SYSTEM, reason=reason, correlation_id=correlation_id)

    @classmethod
    def cli(
        cls,
        actor_id: int | None = None,
        reason: str | None = None,
        correlation_id: str | None = None,
    ) -> ActorContext:
        """Create a CLI operator actor context."""
        return cls(origin=ActorOrigin.CLI, actor_id=actor_id, reason=reason, correlation_id=correlation_id)

    @classmethod
    def migration(cls, reason: str | None = None, correlation_id: str | None = None) -> ActorContext:
        """Create a database migration actor context."""
        return cls(origin=ActorOrigin.MIGRATION, reason=reason, correlation_id=correlation_id)

    @classmethod
    def telegram(
        cls,
        user_id: int,
        reason: str | None = None,
        correlation_id: str | None = None,
    ) -> ActorContext:
        """Create a Telegram user actor context."""
        return cls(origin=ActorOrigin.TELEGRAM, actor_id=user_id, reason=reason, correlation_id=correlation_id)


class AuditEventDB(Base):
    """Append-only audit event recording durable entity mutations."""

    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
        index=True,
    )
    actor_origin: Mapped[ActorOrigin] = mapped_column(
        Enum(ActorOrigin, native_enum=False, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
    )
    actor_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    target_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    target_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    changed_fields: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    reason: Mapped[str | None] = mapped_column(String, nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
