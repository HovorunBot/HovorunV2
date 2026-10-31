"""Session-bound notification event and recipient receipt persistence."""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hovorunv2.application.access.permissions import PermissionTarget
from hovorunv2.application.operations.notification_ledger import (
    NotificationEvent,
    NotificationOutcome,
    NotificationReceipt,
)
from hovorunv2.domain.chat import ChatDB
from hovorunv2.domain.notification_event import NotificationEventDB, NotificationReceiptDB
from hovorunv2.domain.user import UserDB
from hovorunv2.infrastructure.persistence.repositories.policy_queries import exact_target


class NotificationLedgerRepository:
    """Persist immutable events and one receipt per event/recipient pair."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def event(self, event_key: str) -> NotificationEventDB | None:
        """Read the immutable event by key without provisioning a duplicate.

        Args:
            event_key: Immutable event key.

        Returns:
            Event matching the key, or None if absent.
        """
        return await self._session.scalar(select(NotificationEventDB).where(NotificationEventDB.event_key == event_key))

    async def ensure_event(self, event: NotificationEvent) -> tuple[bool, dict[str, object]]:
        """Create the event once; an existing key is an idempotent no-op.

        Args:
            event: Notification event to persist.

        Returns:
            A pair of whether the event was created and the audit changes.
        """
        existing = await self.event(event.event_key)
        if existing is not None:
            return False, {}
        self._session.add(
            NotificationEventDB(
                event_key=event.event_key,
                category=event.category.value,
                introduction=event.introduction,
                release_identity=event.release_identity,
                body_ref=event.body_ref,
                created_at=datetime.now(UTC),
            )
        )
        await self._session.flush()
        return True, {"event_key": event.event_key, "category": event.category.value}

    async def receipt(self, event_key: str, target: PermissionTarget) -> NotificationReceipt | None:
        """Read one recipient receipt only for the exact active subject generation.

        Args:
            event_key: Immutable event key.
            target: Permission target identifying the recipient.

        Returns:
            Recipient receipt, or None if the event or active target is absent.
        """
        event = await self.event(event_key)
        if event is None or await self._active_target(target) is None:
            return None
        row = await self._receipt_row(event.id, target)
        if row is None:
            return None
        return NotificationReceipt(
            event_key,
            target,
            NotificationOutcome(row.status),
            row.attempts,
            row.telegram_message_id,
        )

    async def record(
        self,
        event_key: str,
        target: PermissionTarget,
        outcome: NotificationOutcome,
        *,
        attempts: int,
        telegram_message_id: int | None,
    ) -> tuple[bool, dict[str, object]]:
        """Upsert one recipient receipt without touching any other recipient.

        Args:
            event_key: Immutable event key.
            target: Permission target identifying the recipient.
            outcome: Delivery outcome recorded on the receipt.
            attempts: Number of delivery attempts recorded on the receipt.
            telegram_message_id: Optional delivered Telegram message ID.

        Returns:
            A pair of whether the receipt changed and the audit changes.

        Raises:
            ValueError: If the event or the exact active target generation is unavailable.
        """
        event = await self.event(event_key)
        if event is None:
            msg = "Notification receipt requires an existing immutable event."
            raise ValueError(msg)
        if await self._active_target(target) is None:
            msg = "Notification receipt requires the exact active target generation."
            raise ValueError(msg)
        row = await self._receipt_row(event.id, target)
        before = row.status if row is not None else None
        if row is None:
            row = NotificationReceiptDB(
                event_id=event.id,
                user_id=target.entity_id if target.kind == "user" else None,
                group_id=target.entity_id if target.kind == "group" else None,
                status=outcome.value,
                attempts=attempts,
                telegram_message_id=telegram_message_id,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            self._session.add(row)
        else:
            row.status = outcome.value
            row.attempts = attempts
            row.telegram_message_id = telegram_message_id
            row.updated_at = datetime.now(UTC)
        await self._session.flush()
        return True, {
            "status": [before, outcome.value],
            "attempts": attempts,
            "telegram_message_id": telegram_message_id,
        }

    async def pending(self, event_key: str, limit: int) -> tuple[NotificationReceipt, ...]:
        """Return a bounded pending/retry page with current subject routing.

        Args:
            event_key: Immutable event key.
            limit: Maximum number of receipts to return.

        Returns:
            Pending or retry receipts with current subject routing.
        """
        event = await self.event(event_key)
        if event is None or limit < 1:
            return ()
        rows = (
            await self._session.execute(
                select(NotificationReceiptDB, UserDB, ChatDB)
                .outerjoin(UserDB, UserDB.id == NotificationReceiptDB.user_id)
                .outerjoin(ChatDB, ChatDB.id == NotificationReceiptDB.group_id)
                .where(
                    NotificationReceiptDB.event_id == event.id,
                    NotificationReceiptDB.status.in_(
                        (NotificationOutcome.PENDING.value, NotificationOutcome.RETRY.value)
                    ),
                )
                .order_by(NotificationReceiptDB.id)
                .limit(limit)
            )
        ).all()
        return tuple(self._to_receipt(event_key, receipt, user, chat) for receipt, user, chat in rows)

    async def _receipt_row(self, event_id: int, target: PermissionTarget) -> NotificationReceiptDB | None:
        """Return the receipt row for the exact event and subject, or None."""
        subject = NotificationReceiptDB.user_id if target.kind == "user" else NotificationReceiptDB.group_id
        return await self._session.scalar(
            select(NotificationReceiptDB).where(
                NotificationReceiptDB.event_id == event_id,
                subject == target.entity_id,
            )
        )

    async def _active_target(self, target: PermissionTarget) -> UserDB | ChatDB | None:
        """Return the exact active target generation, or None."""
        statement = exact_target(target)
        return await self._session.scalar(statement) if statement is not None else None

    def _to_receipt(
        self, event_key: str, row: NotificationReceiptDB, user: UserDB | None, chat: ChatDB | None
    ) -> NotificationReceipt:
        """Build a receipt from its row and current subject routing."""
        if user is not None:
            target = PermissionTarget("user", user.id, user.generation, user.telegram_id)
        elif chat is not None:
            target = PermissionTarget("group", chat.id, chat.generation, chat.chat_id)
        else:
            msg = "Notification receipt subject is unavailable."
            raise ValueError(msg)
        return NotificationReceipt(
            event_key,
            target,
            NotificationOutcome(row.status),
            row.attempts,
            row.telegram_message_id,
        )
