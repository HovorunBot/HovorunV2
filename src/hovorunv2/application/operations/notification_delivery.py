"""Bounded native notification delivery with preference, access and body checks."""

import logging
from typing import Protocol

from hovorunv2.application.access.permissions import PermissionTarget
from hovorunv2.application.jobs.feature_jobs import PayloadState, SyncPayloadPort
from hovorunv2.application.operations.notification_ledger import (
    DEFAULT_PENDING_LIMIT,
    NotificationEvent,
    NotificationOutcome,
    NotificationReceipt,
)
from hovorunv2.application.operations.notification_preferences import NotificationCategory
from hovorunv2.application.usage.budgets import Clock
from hovorunv2.domain.audit import ActorContext

logger = logging.getLogger(__name__)

MAX_DELIVERY_ATTEMPTS = 5
BASE_BACKOFF_SECONDS = 30
MAX_BACKOFF_SECONDS = 300


class NotificationLedgerPort(Protocol):
    """Synchronous delivery ledger reads and audited outcome writes."""

    def ensure_event(self, event: NotificationEvent, *, actor: ActorContext) -> bool:
        """Create the immutable event once; an existing key is a no-op."""
        ...

    def pending(self, limit: int = DEFAULT_PENDING_LIMIT) -> tuple[NotificationReceipt, ...]:
        """Return a bounded page of pending/retry receipts."""
        ...

    def preference_enabled(self, target: PermissionTarget, category: NotificationCategory) -> bool:
        """Return the active category switch; absence means enabled."""
        ...

    def reachable(self, target: PermissionTarget) -> bool:
        """Require a current active subject that may receive routine notifications."""
        ...

    def record(
        self,
        event_key: str,
        target: PermissionTarget,
        outcome: NotificationOutcome,
        *,
        attempts: int,
        telegram_message_id: int | None,
        actor: ActorContext,
    ) -> bool:
        """Upsert one recipient outcome without touching other recipients."""
        ...


class NotificationSenderPort(Protocol):
    """Narrow synchronous Telegram text sender."""

    def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        parse_mode: str = "HTML",
        disable_web_page_preview: bool = True,
        reply_markup: str | None = None,
    ) -> int | None:
        """Send one formatted message and return its ID when accepted."""
        ...


class NotificationDeliveryService:
    """Deliver due receipts once; transient failures retry with bounded exponential backoff."""

    def __init__(
        self,
        ledger: NotificationLedgerPort,
        payloads: SyncPayloadPort,
        sender: NotificationSenderPort,
        clock: Clock,
    ) -> None:
        self._ledger = ledger
        self._payloads = payloads
        self._sender = sender
        self._clock = clock

    def deliver_due(self, limit: int = DEFAULT_PENDING_LIMIT) -> int:
        """Process a bounded page and return the number of accepted sends.

        Args:
            limit: Maximum number of receipts to process.

        Returns:
            Number of receipts delivered.
        """
        delivered = 0
        for receipt in self._ledger.pending(limit):
            if self._deliver(receipt):
                delivered += 1
        return delivered

    def _deliver(self, receipt: NotificationReceipt) -> bool:
        if not self._eligible(receipt):
            return False
        text = self._body(receipt)
        if text is None:
            self._record(receipt, NotificationOutcome.SKIPPED)
            return False
        telegram_id = receipt.target.telegram_id
        if telegram_id is None:
            self._record(receipt, NotificationOutcome.UNREACHABLE)
            return False
        attempts = receipt.attempts + 1
        try:
            message_id = self._sender.send_message(telegram_id, text, reply_markup=receipt.body_markup)
        except Exception:
            logger.warning("Notification delivery deferred")
            outcome = (
                NotificationOutcome.UNREACHABLE if attempts >= MAX_DELIVERY_ATTEMPTS else NotificationOutcome.RETRY
            )
            self._record(receipt, outcome, attempts=attempts)
            return False
        self._record(
            receipt,
            NotificationOutcome.DELIVERED,
            attempts=attempts,
            telegram_message_id=message_id,
        )
        return True

    def _eligible(self, receipt: NotificationReceipt) -> bool:
        """Apply the preference, reachability and backoff gates before any send.

        Args:
            receipt: Recipient receipt to gate.

        Returns:
            True when the receipt is due and eligible for delivery, False otherwise.
        """
        category = receipt.category
        if category is None or not self._ledger.preference_enabled(receipt.target, category):
            self._record(receipt, NotificationOutcome.SKIPPED)
            return False
        if not self._ledger.reachable(receipt.target):
            self._record(receipt, NotificationOutcome.UNREACHABLE)
            return False
        return self._due(receipt)

    def _body(self, receipt: NotificationReceipt) -> str | None:
        """Load the encrypted body by reference; no body is rendered from ledger metadata.

        Args:
            receipt: Recipient receipt holding the body reference.

        Returns:
            Decoded body text, or None when unavailable or undecodable.
        """
        if receipt.body_ref is None:
            return None
        loaded = self._payloads.load(receipt.body_ref, receipt.event_key)
        if loaded.state != PayloadState.READY or loaded.data is None:
            return None
        try:
            return loaded.data.decode()
        except UnicodeDecodeError:
            return None

    def _due(self, receipt: NotificationReceipt) -> bool:
        if receipt.attempts == 0 or receipt.last_attempt_at is None:
            return True
        backoff = min(
            BASE_BACKOFF_SECONDS * (2 ** max(0, receipt.attempts - 1)),
            MAX_BACKOFF_SECONDS,
        )
        return self._clock.now() >= receipt.last_attempt_at + backoff

    def _record(
        self,
        receipt: NotificationReceipt,
        outcome: NotificationOutcome,
        *,
        attempts: int | None = None,
        telegram_message_id: int | None = None,
    ) -> None:
        self._ledger.record(
            receipt.event_key,
            receipt.target,
            outcome,
            attempts=receipt.attempts if attempts is None else attempts,
            telegram_message_id=telegram_message_id,
            actor=ActorContext.system("notification delivery"),
        )
