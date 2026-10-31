"""Audited notification event and recipient receipt writes."""

from functools import partial

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from hovorunv2.application.access.permissions import PermissionTarget
from hovorunv2.application.auditing import AuditedUnitOfWork
from hovorunv2.application.operations.notification_ledger import (
    DEFAULT_PENDING_LIMIT,
    NotificationEvent,
    NotificationEventStore,
    NotificationOutcome,
    NotificationReceipt,
)
from hovorunv2.domain.audit import ActorContext
from hovorunv2.infrastructure.persistence.repositories.notification_ledger_repository import (
    NotificationLedgerRepository,
)


class SQLAlchemyNotificationLedgerStore(NotificationEventStore):
    """Keep immutable event identity and recipient outcomes auditable and replay-safe."""

    def __init__(self, session_maker: async_sessionmaker, audited_uow: AuditedUnitOfWork) -> None:
        self._session_maker = session_maker
        self._audited_uow = audited_uow

    async def _ensure(self, session: AsyncSession, event: NotificationEvent) -> tuple[bool, dict[str, object]]:
        """Create the event once through the session-bound repository."""
        return await NotificationLedgerRepository(session).ensure_event(event)

    async def ensure_event(self, event: NotificationEvent, *, actor: ActorContext) -> bool:
        """Create the event once and audit the creation atomically.

        Args:
            event: Notification event to persist.
            actor: Actor context recorded on the audit event.

        Returns:
            True if the event was created.
        """
        result: tuple[bool, dict[str, object]] = await self._audited_uow.execute(
            actor=actor,
            target_type="notification_event",
            target_id=event.event_key,
            action="create",
            operation=partial(self._ensure, event=event),
            changed_fields=lambda outcome: outcome[1],
            audit_if=lambda outcome: outcome[0],
        )
        return result[0]

    async def receipt(self, event_key: str, target: PermissionTarget) -> NotificationReceipt | None:
        """Read one recipient receipt through an owning session.

        Args:
            event_key: Immutable event key.
            target: Permission target identifying the recipient.

        Returns:
            Recipient receipt, or None if the event or active target is absent.
        """
        async with self._session_maker() as session:
            return await NotificationLedgerRepository(session).receipt(event_key, target)

    async def _record(
        self,
        session: AsyncSession,
        *,
        event_key: str,
        target: PermissionTarget,
        outcome: NotificationOutcome,
        attempts: int,
        telegram_message_id: int | None,
    ) -> tuple[bool, dict[str, object]]:
        """Upsert one receipt through the session-bound repository."""
        return await NotificationLedgerRepository(session).record(
            event_key,
            target,
            outcome,
            attempts=attempts,
            telegram_message_id=telegram_message_id,
        )

    async def record(
        self,
        event_key: str,
        target: PermissionTarget,
        outcome: NotificationOutcome,
        *,
        attempts: int,
        telegram_message_id: int | None,
        actor: ActorContext,
    ) -> bool:
        """Upsert one recipient receipt and its audit event atomically.

        Args:
            event_key: Immutable event key.
            target: Permission target identifying the recipient.
            outcome: Delivery outcome recorded on the receipt.
            attempts: Number of delivery attempts recorded on the receipt.
            telegram_message_id: Optional delivered Telegram message ID.
            actor: Actor context recorded on the audit event.

        Returns:
            True if the receipt changed.
        """
        result: tuple[bool, dict[str, object]] = await self._audited_uow.execute(
            actor=actor,
            target_type="notification_receipt",
            target_id=f"{event_key}:{target.kind}:{target.entity_id}",
            action="set",
            operation=partial(
                self._record,
                event_key=event_key,
                target=target,
                outcome=outcome,
                attempts=attempts,
                telegram_message_id=telegram_message_id,
            ),
            changed_fields=lambda outcome: outcome[1],
            audit_if=lambda outcome: outcome[0],
        )
        return result[0]

    async def pending(self, event_key: str, limit: int = DEFAULT_PENDING_LIMIT) -> tuple[NotificationReceipt, ...]:
        """Return pending/retry recipients for later bounded delivery.

        Args:
            event_key: Immutable event key.
            limit: Maximum number of receipts to return.

        Returns:
            Pending or retry receipts with current subject routing.
        """
        async with self._session_maker() as session:
            return await NotificationLedgerRepository(session).pending(event_key, limit)
