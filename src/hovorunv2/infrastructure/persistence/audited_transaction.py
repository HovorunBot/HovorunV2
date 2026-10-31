"""Transaction adapter providing atomic execution with append-only audit logging."""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from hovorunv2.application.auditing import AuditedUnitOfWork
from hovorunv2.domain.audit import ActorContext, AuditEventDB, sanitize_audit_changes
from hovorunv2.domain.audit_schema import validate_audit_changes, validate_audit_reason
from hovorunv2.infrastructure.persistence.repositories.audit_repository import SQLAlchemyAuditRepository

MAX_TRANSACTION_ATTEMPTS = 3
RETRYABLE_TRANSACTION_STATES = frozenset({"40001", "40P01"})


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
            DBAPIError: If a non-retryable database error exhausts transaction attempts.

        """
        if target_type == "audit":
            msg = "Recursive audit logging of audit entities is forbidden."
            raise ValueError(msg)
        validate_audit_changes(target_type, action, None)
        validate_audit_reason(actor.reason)

        # Callbacks are database-only; whole-transaction retry cannot repeat external effects.
        attempt = 0
        while True:
            try:
                return await self._execute_attempt(
                    actor, target_type, target_id, action, operation, changed_fields, audit_if
                )
            except DBAPIError as error:
                attempt += 1
                if (
                    getattr(error.orig, "sqlstate", None) not in RETRYABLE_TRANSACTION_STATES
                    or attempt >= MAX_TRANSACTION_ATTEMPTS
                ):
                    raise

    async def _execute_attempt(
        self,
        actor: ActorContext,
        target_type: str,
        target_id: str | int,
        action: str,
        operation: Callable[[AsyncSession], Awaitable[Any]],
        changed_fields: dict[str, Any] | Callable[[Any], dict[str, Any] | None] | None,
        audit_if: Callable[[Any], bool] | None,
    ) -> Any:
        """Run one atomic attempt with audit logging on the transaction session.

        Args:
            actor: Actor context recorded on the appended audit event.
            target_type: Business entity category recorded on the audit event.
            target_id: Stable identifier of the target subject.
            action: Action verb recorded on the audit event.
            operation: Async callback performing entity mutations on the transaction session.
            changed_fields: Explicit dictionary of changed attributes or callable deriving them from the result.
            audit_if: Optional predicate deciding whether to append an audit event.

        Returns:
            Result returned by the operation callback.
        """
        async with self._session_maker() as session:
            async with session.begin():
                result = await operation(session)
                if audit_if is None or audit_if(result):
                    await self._append_audit_event(
                        session, actor, target_type, target_id, action, changed_fields, result
                    )
            return result

    async def _append_audit_event(
        self,
        session: AsyncSession,
        actor: ActorContext,
        target_type: str,
        target_id: str | int,
        action: str,
        changed_fields: dict[str, Any] | Callable[[Any], dict[str, Any] | None] | None,
        result: Any,
    ) -> None:
        """Validate, sanitize and append the audit event for a committed change.

        Args:
            session: Transaction session receiving the audit event.
            actor: Actor context recorded on the audit event.
            target_type: Business entity category recorded on the audit event.
            target_id: Stable identifier of the target subject.
            action: Action verb recorded on the audit event.
            changed_fields: Explicit dictionary of changed attributes or callable deriving them from the result.
            result: Operation result supplied to the changed_fields callable.
        """
        if isinstance(changed_fields, dict):
            raw_changes = changed_fields
        elif callable(changed_fields):
            raw_changes = changed_fields(result)
        else:
            raw_changes = None
        validate_audit_changes(target_type, action, raw_changes)
        sanitized_changes = sanitize_audit_changes(raw_changes)
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
        await SQLAlchemyAuditRepository(session).add(event)
