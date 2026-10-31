"""Application ports (Protocols) defining boundaries for external adapters.

Optional lookups describe expected absence. Boolean mutations report their documented
no-op or acceptance outcome; they are not generic availability checks. Required
operations propagate failures to the owning workflow. Injected adapters are mandatory.
"""

from datetime import datetime
from typing import Protocol

from hovorunv2.application.identity.membership import AdministratorEvidence, BotCapabilityEvidence, MembershipEvidence
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.chat import ChatDB
from hovorunv2.domain.user import UserDB


class OperationsRunnerPort(Protocol):
    """Protocol for synchronous execution of periodic and maintenance operations."""

    def reconcile_memberships(self, now: datetime | None = None) -> int:
        """Run membership reconciliation sweep."""
        ...

    def run_chat_reminders(self) -> int:
        """Run periodic reminder sweep for unauthorized and pending chats."""
        ...

    def sweep_expired_cleanups(self, now: float | None = None) -> int:
        """Sweep and delete expired cleanup window messages."""
        ...

    def broadcast_release_notifications(self) -> int:
        """Broadcast release notification on version upgrade."""
        ...

    def deliver_notifications(self) -> int:
        """Deliver bounded due notification receipts."""
        ...

    def send_unauthorized_reminder(self, chat_id: int, *, force: bool = False) -> bool:
        """Dispatch guidance prompt to unauthorized group chat."""
        ...


class WorkerOperationsPort(Protocol):
    """Synchronous operations persistence interface for background maintenance."""

    def due_groups(self, now: datetime, limit: int = 10) -> list[ChatDB]:
        """Return group records requiring membership verification."""
        ...

    def relevant_users(self, group_id: int, limit: int = 50) -> list[UserDB]:
        """Return distinct user records active in group."""
        ...

    def save_bot(self, group_id: int, evidence: BotCapabilityEvidence, actor: ActorContext | None = None) -> bool:
        """Upsert bot presence and capabilities in group."""
        ...

    def save_admins(self, group_id: int, evidence: AdministratorEvidence, actor: ActorContext | None = None) -> bool:
        """Upsert detected administrators for group."""
        ...

    def save_member(
        self,
        group_id: int,
        evidence: MembershipEvidence,
        actor: ActorContext | None = None,
        *,
        expected_user_id: int | None = None,
    ) -> bool:
        """Upsert single user membership evidence in group."""
        ...

    def unauthorized_group_chats(self) -> list[ChatDB]:
        """Return active unauthorized group chats."""
        ...

    def pending_chats(self) -> list[ChatDB]:
        """Return chats pending approval."""
        ...

    def list_owners(self) -> list[UserDB]:
        """Return user accounts with owner role."""
        ...

    def get_last_notified_version(self) -> str | None:
        """Return last notified software release version."""
        ...

    def set_last_notified_version(self, version: str, actor: ActorContext | None = None) -> None:
        """Record last notified software release version."""
        ...

    def approved_group_chats(self) -> list[ChatDB]:
        """Return active approved group chats."""
        ...

    def get_chat(self, chat_id: int) -> ChatDB | None:
        """Fetch chat record by Telegram chat ID."""
        ...


class WorkerTelegramPort(Protocol):
    """Synchronous Telegram API port for native maintenance operations."""

    def get_chat_member(self, chat_id: int, user_id: int) -> dict[str, object] | None:
        """Fetch chat member info."""
        ...

    def get_chat_administrators(self, chat_id: int) -> list[object] | None:
        """Fetch list of chat administrators."""
        ...

    def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        parse_mode: str = "HTML",
        reply_markup: str | None = None,
        disable_web_page_preview: bool = True,
    ) -> int | None:
        """Send formatted text message and return message ID if delivered."""
        ...

    def delete_message(self, chat_id: int, message_id: int) -> bool:
        """Delete message, returning True if accepted."""
        ...

    def edit_message_text(self, chat_id: int, message_id: int, text: str, *, parse_mode: str = "HTML") -> bool:
        """Edit text message, returning True if accepted."""
        ...


class SyncUnauthorizedPromptPort(Protocol):
    """Synchronous interface for dispatching unauthorized guidance prompts."""

    def send(self, chat_id: int, group_id: int, generation: int) -> bool:
        """Send prompt to unauthorized chat."""
        ...


class SystemStore(Protocol):
    """Protocol for system-wide state persistence operations."""

    async def get_last_notified_version(self) -> str | None:
        """Return the notification marker, or None before any marker is stored."""
        ...

    async def set_last_notified_version(self, version: str, actor: ActorContext | None = None) -> None:
        """Update last notified version in database."""
        ...

    async def get_value(self, key: str) -> str | None:
        """Return a stored value, or None when the setting is absent."""
        ...

    async def set_value(self, key: str, value: str, actor: ActorContext | None = None) -> None:
        """Store arbitrary value in system storage."""
        ...
