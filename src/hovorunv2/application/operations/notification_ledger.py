"""Immutable notification events and per-recipient delivery outcomes."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from hovorunv2.application.access.permissions import PermissionTarget
from hovorunv2.application.operations.notification_preferences import NotificationCategory
from hovorunv2.domain.audit import ActorContext

DEFAULT_PENDING_LIMIT = 100


class NotificationOutcome(StrEnum):
    """Recipient-specific operational outcomes; never a source message body."""

    PENDING = "pending"
    DELIVERED = "delivered"
    RETRY = "retry"
    DISMISSED = "dismissed"
    SKIPPED = "skipped"
    UNREACHABLE = "unreachable"


@dataclass(frozen=True, slots=True)
class NotificationEvent:
    """Immutable logical event identity independent of any recipient."""

    event_key: str
    category: NotificationCategory
    introduction: str
    release_identity: str


@dataclass(frozen=True, slots=True)
class NotificationReceipt:
    """One recipient's current outcome and attempt metadata."""

    event_key: str
    target: PermissionTarget
    outcome: NotificationOutcome
    attempts: int
    telegram_message_id: int | None


class NotificationEventStore(Protocol):
    """Durable event/receipt ledger with audited writes."""

    async def ensure_event(self, event: NotificationEvent, *, actor: ActorContext) -> bool:
        """Create the event once by immutable key; an existing event is a no-op."""
        ...

    async def receipt(self, event_key: str, target: PermissionTarget) -> NotificationReceipt | None:
        """Return one recipient's receipt or None when it was never recorded."""
        ...

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
        """Upsert one recipient receipt without touching other recipients."""
        ...

    async def pending(self, event_key: str, limit: int = DEFAULT_PENDING_LIMIT) -> tuple[NotificationReceipt, ...]:
        """Return a bounded page of pending/retry recipients for delivery."""
        ...


class NotificationLedgerService:
    """Keep event identity and per-recipient outcomes separate from message bodies."""

    def __init__(self, store: NotificationEventStore) -> None:
        self._store = store

    async def ensure_event(self, event: NotificationEvent, *, actor: ActorContext) -> bool:
        """Create the immutable event once; duplicates cannot create logical receipts."""
        return await self._store.ensure_event(event, actor=actor)

    async def receipt(self, event_key: str, target: PermissionTarget) -> NotificationReceipt | None:
        """Read one recipient outcome."""
        return await self._store.receipt(event_key, target)

    async def record(
        self,
        event_key: str,
        target: PermissionTarget,
        outcome: NotificationOutcome,
        *,
        attempts: int,
        telegram_message_id: int | None = None,
        actor: ActorContext,
    ) -> bool:
        """Record one recipient outcome without resetting successful recipients."""
        return await self._store.record(
            event_key,
            target,
            outcome,
            attempts=attempts,
            telegram_message_id=telegram_message_id,
            actor=actor,
        )

    async def pending(self, event_key: str) -> tuple[NotificationReceipt, ...]:
        """Return pending/retry recipients for bounded delivery."""
        return await self._store.pending(event_key)

    async def should_deliver(self, event_key: str, target: PermissionTarget) -> bool:
        """Return whether a new, pending or retry receipt may still be delivered."""
        receipt = await self._store.receipt(event_key, target)
        return receipt is None or receipt.outcome in {NotificationOutcome.PENDING, NotificationOutcome.RETRY}
