"""Synchronous notification ledger, preference and reachability reads for native workers."""

from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from hovorunv2.application.access.permissions import PermissionTarget
from hovorunv2.application.operations.notification_ledger import (
    DEFAULT_PENDING_LIMIT,
    NotificationEvent,
    NotificationOutcome,
    NotificationReceipt,
)
from hovorunv2.application.operations.notification_preferences import NotificationCategory
from hovorunv2.domain.audit import ActorContext, AuditEventDB
from hovorunv2.domain.audit_schema import validate_audit_changes
from hovorunv2.domain.ban import BanDB
from hovorunv2.domain.chat import ChatDB
from hovorunv2.domain.enums import ChatStatus, TargetKind
from hovorunv2.domain.notification import NotificationPreferenceDB
from hovorunv2.domain.notification_event import NotificationEventDB, NotificationReceiptDB
from hovorunv2.domain.user import UserDB


class WorkerNotificationStore:
    """Bounded child-local delivery reads/writes; no message contents are stored here."""

    def __init__(self, sessions: sessionmaker, require_owner: Callable[[], None]) -> None:
        self._sessions = sessions
        self._require_owner = require_owner

    def pending(self, limit: int = DEFAULT_PENDING_LIMIT) -> tuple[NotificationReceipt, ...]:
        """Return a bounded page of pending/retry receipts with current routing.

        Args:
            limit: Maximum number of receipts to return.

        Returns:
            Pending or retry receipts with current subject routing.
        """
        self._require_owner()
        if limit < 1:
            return ()
        with self._sessions() as session:
            rows = session.execute(
                select(NotificationReceiptDB, NotificationEventDB, UserDB, ChatDB)
                .join(NotificationEventDB, NotificationEventDB.id == NotificationReceiptDB.event_id)
                .outerjoin(UserDB, UserDB.id == NotificationReceiptDB.user_id)
                .outerjoin(ChatDB, ChatDB.id == NotificationReceiptDB.group_id)
                .where(
                    NotificationReceiptDB.status.in_(
                        (NotificationOutcome.PENDING.value, NotificationOutcome.RETRY.value)
                    )
                )
                .order_by(NotificationReceiptDB.updated_at, NotificationReceiptDB.id)
                .limit(limit)
            ).all()
            return tuple(self._receipt(event, receipt, user, chat) for receipt, event, user, chat in rows)

    def ensure_event(self, event: NotificationEvent, *, actor: ActorContext) -> bool:
        """Create the immutable event once and append its audit event in one transaction."""
        self._require_owner()
        with self._sessions.begin() as session:
            existing = session.scalar(
                select(NotificationEventDB).where(NotificationEventDB.event_key == event.event_key)
            )
            if existing is not None:
                return False
            session.add(
                NotificationEventDB(
                    event_key=event.event_key,
                    category=event.category.value,
                    introduction=event.introduction,
                    release_identity=event.release_identity,
                    body_ref=event.body_ref,
                    created_at=datetime.now(UTC),
                )
            )
            changes: dict[str, object] = {
                "event_key": event.event_key,
                "category": event.category.value,
            }
            validate_audit_changes("notification_event", "create", changes)
            session.add(
                AuditEventDB(
                    actor_origin=actor.origin,
                    actor_id=actor.actor_id,
                    target_type="notification_event",
                    target_id=event.event_key,
                    action="create",
                    changed_fields=changes,
                    reason=actor.reason,
                    correlation_id=actor.correlation_id,
                )
            )
        return True

    def preference_enabled(self, target: PermissionTarget, category: NotificationCategory) -> bool:
        """Return the active category switch; absence means enabled.

        Args:
            target: Permission target identifying the subject.
            category: Notification category being queried.

        Returns:
            True if the category is enabled or has no override.
        """
        self._require_owner()
        with self._sessions() as session:
            subject = NotificationPreferenceDB.user_id if target.kind == "user" else NotificationPreferenceDB.group_id
            row = session.scalar(
                select(NotificationPreferenceDB).where(
                    subject == target.entity_id,
                    NotificationPreferenceDB.category == category.value,
                    NotificationPreferenceDB.deleted_at.is_(None),
                )
            )
            return True if row is None else row.enabled

    def reachable(self, target: PermissionTarget) -> bool:
        """Require a current active subject and no active personal ban for users.

        Args:
            target: Permission target identifying the subject.

        Returns:
            True if the subject is currently reachable.
        """
        self._require_owner()
        with self._sessions() as session:
            if target.kind == "user":
                return self._user_reachable(session, target)
            return self._group_reachable(session, target)

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
        """Upsert one receipt and append its audit event in one transaction.

        Args:
            event_key: Immutable event key.
            target: Permission target identifying the recipient.
            outcome: Delivery outcome recorded on the receipt.
            attempts: Number of delivery attempts recorded on the receipt.
            telegram_message_id: Optional delivered Telegram message ID.
            actor: Actor context recorded on the audit event.

        Returns:
            True once the receipt is recorded.

        Raises:
            ValueError: If the immutable event is absent.
        """
        self._require_owner()
        with self._sessions.begin() as session:
            event = session.scalar(select(NotificationEventDB).where(NotificationEventDB.event_key == event_key))
            if event is None:
                msg = "Notification receipt requires an existing immutable event."
                raise ValueError(msg)
            subject = NotificationReceiptDB.user_id if target.kind == "user" else NotificationReceiptDB.group_id
            row = session.scalar(
                select(NotificationReceiptDB).where(
                    NotificationReceiptDB.event_id == event.id,
                    subject == target.entity_id,
                )
            )
            before = row.status if row is not None else None
            now = datetime.now(UTC)
            if row is None:
                row = NotificationReceiptDB(
                    event_id=event.id,
                    user_id=target.entity_id if target.kind == "user" else None,
                    group_id=target.entity_id if target.kind == "group" else None,
                    status=outcome.value,
                    attempts=attempts,
                    telegram_message_id=telegram_message_id,
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)
            else:
                row.status = outcome.value
                row.attempts = attempts
                row.telegram_message_id = telegram_message_id
                row.updated_at = now
            changes: dict[str, object] = {
                "status": [before, outcome.value],
                "attempts": attempts,
                "telegram_message_id": telegram_message_id,
            }
            validate_audit_changes("notification_receipt", "set", changes)
            session.add(
                AuditEventDB(
                    actor_origin=actor.origin,
                    actor_id=actor.actor_id,
                    target_type="notification_receipt",
                    target_id=f"{event_key}:{target.kind}:{target.entity_id}",
                    action="set",
                    changed_fields=changes,
                    reason=actor.reason,
                    correlation_id=actor.correlation_id,
                )
            )
        return True

    def _user_reachable(self, session: Session, target: PermissionTarget) -> bool:
        """Return whether the user target is active and unbanned."""
        user = session.scalar(
            select(UserDB).where(
                UserDB.telegram_id == target.telegram_id,
                UserDB.platform == "telegram",
                UserDB.deleted_at.is_(None),
            )
        )
        if user is None or user.id != target.entity_id or user.generation != target.generation:
            return False
        banned = session.scalar(
            select(BanDB.id)
            .where(
                BanDB.target_kind == TargetKind.USER,
                BanDB.telegram_id == target.telegram_id,
                BanDB.platform == "telegram",
                BanDB.deleted_at.is_(None),
                BanDB.revoked_at.is_(None),
            )
            .limit(1)
        )
        return banned is None

    def _group_reachable(self, session: Session, target: PermissionTarget) -> bool:
        """Return whether the group target is active, approved and canonical."""
        chat = session.scalar(
            select(ChatDB).where(
                ChatDB.chat_id == target.telegram_id,
                ChatDB.platform == "telegram",
                ChatDB.deleted_at.is_(None),
            )
        )
        return (
            chat is not None
            and chat.id == target.entity_id
            and chat.generation == target.generation
            and chat.status == ChatStatus.APPROVED
            and chat.chat_type in {"group", "supergroup"}
        )

    def _receipt(
        self,
        event: NotificationEventDB,
        receipt: NotificationReceiptDB,
        user: UserDB | None,
        chat: ChatDB | None,
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
            event.event_key,
            target,
            NotificationOutcome(receipt.status),
            receipt.attempts,
            receipt.telegram_message_id,
            category=NotificationCategory(event.category),
            body_ref=event.body_ref,
            last_attempt_at=receipt.updated_at.timestamp(),
        )
