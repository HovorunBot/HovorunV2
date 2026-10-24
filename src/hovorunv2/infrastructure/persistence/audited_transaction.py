"""Transaction adapter providing atomic execution with append-only audit logging."""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from hovorunv2.application.ports import AuditedUnitOfWork
from hovorunv2.domain.audit import ActorContext, AuditEventDB, sanitize_audit_changes
from hovorunv2.infrastructure.repositories.audit_repository import SQLAlchemyAuditRepository


class SQLAlchemyAuditedTransaction(AuditedUnitOfWork):
    """Executes business operations and records append-only audit events in a single transaction."""

    def __init__(self, session_maker: async_sessionmaker) -> None:
        """Initialize with session maker."""
        self._session_maker = session_maker

    async def execute(
        self,
        actor: ActorContext,
        target_type: str,
        target_id: str | int,
        action: str,
        operation: Callable[[AsyncSession], Awaitable[Any]],
        changed_fields: dict[str, Any] | Callable[[Any], dict[str, Any] | None] | None = None,
        audit_if: Callable[[Any], bool] | None = None,
    ) -> Any:
        """Execute operation and record audit entry atomically.

        Args:
            actor: Actor context identifying origin, optional actor ID, and correlation metadata.
            target_type: Business entity category (e.g. 'chat', 'user', 'command_grant').
            target_id: Stable identifier of the target subject.
            action: Action verb (e.g. 'create', 'update', 'delete', 'grant', 'revoke').
            operation: Async callback performing entity mutations on the transaction session.
            changed_fields: Explicit dictionary of changed attributes or callable deriving them from operation result.
            audit_if: Optional predicate on operation result deciding whether to append an audit event.

        Returns:
            Result returned by the operation callback.

        Raises:
            ValueError: If target_type is 'audit' (prevents recursive audit).
            Exception: Any exception raised during operation, triggering full rollback.

        """
        if target_type == "audit":
            msg = "Recursive audit logging of audit entities is forbidden."
            raise ValueError(msg)

        async with self._session_maker() as session:
            async with session.begin():
                result = await operation(session)

                should_audit = audit_if(result) if audit_if is not None else True
                if should_audit:
                    if isinstance(changed_fields, dict):
                        raw_changes = changed_fields
                    elif callable(changed_fields):
                        raw_changes = changed_fields(result)
                    else:
                        raw_changes = None
                    sanitized_changes = sanitize_audit_changes(raw_changes)
                    audit_repo = SQLAlchemyAuditRepository(session)
                    event = AuditEventDB(
                        created_at=datetime.now(UTC),
                        actor_origin=actor.origin,
                        actor_id=actor.actor_id,
                        target_type=target_type,
                        target_id=str(target_id),
                        action=action,
                        changed_fields=sanitized_changes,
                        reason=actor.reason,
                        correlation_id=actor.correlation_id,
                    )
                    await audit_repo.add(event)

            return result
