"""Application ports (Protocols) defining boundaries for external adapters.

Optional lookups describe expected absence. Boolean mutations report their documented
no-op or acceptance outcome; they are not generic availability checks. Required
operations propagate failures to the owning workflow. Injected adapters are mandatory.
"""

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from aiogram import Bot

from hovorunv2.application.access.permissions import PermissionFacts
from hovorunv2.application.browser import BrowserTab
from hovorunv2.application.identity.membership import AdministratorEvidence, BotCapabilityEvidence, MembershipEvidence
from hovorunv2.application.media.errors import MediaFailure
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.chat import ChatDB
from hovorunv2.domain.enums import ChatStatus, Platform, Role, TargetKind
from hovorunv2.domain.user import UserDB


class CachePort(Protocol):
    """Protocol for key-value and sorted-set caching operations."""

    async def get(self, key: str, default: Any = None) -> Any:
        """Return decoded data or default for a missing or malformed JSON value."""
        ...

    async def get_text(self, key: str) -> str | None:
        """Return a cached JSON string, or None only when absent; reject malformed values."""
        ...

    async def set(self, key: str, value: Any, expire: int | None = None) -> None:
        """Store a value in the cache with optional TTL."""
        ...

    async def pop(self, key: str) -> Any:
        """Consume a value atomically; absent keys return None, malformed JSON raises."""
        ...

    async def delete(self, key: str) -> None:
        """Delete a key from the cache."""
        ...

    async def zadd(self, name: str, mapping: Mapping[str, float]) -> None:
        """Add members with scores to a sorted set."""
        ...

    async def zrangebyscore(self, name: str, min_score: float, max_score: float) -> list[str]:
        """Fetch members from sorted set within a score range."""
        ...

    async def zrem(self, name: str, *values: str) -> None:
        """Remove members from a sorted set."""
        ...

    async def zrange(self, name: str, start: int, end: int, *, withscores: bool = False) -> list[Any]:
        """Fetch members from sorted set within an index range."""
        ...


class BrowserPort(Protocol):
    """Protocol for headless browser automation."""

    async def get_content(self, url: str, wait_selector: str | None = None) -> str:
        """Return rendered HTML; navigation or browser failures raise."""
        ...

    async def extract_and_download[T](
        self,
        url: str,
        extractor_fn: Callable[[BrowserTab, str], T | None],
        wait_selector: str | None = None,
        cookies: list[dict[str, object]] | None = None,
    ) -> tuple[T | None, list[bytes | MediaFailure]]:
        """Run extraction and download in one browser session.

        The current adapter returns (None, []) when extraction finds no metadata.
        Navigation and browser failures raise BrowserError.
        Each media position contains bytes or an explicit MediaFailure.
        """
        ...


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


class AppSettings(Protocol):
    """Protocol for application configuration properties needed by application services."""

    @property
    def bot_token(self) -> str:
        """Telegram bot token."""
        ...

    @property
    def cache_encryption_key(self) -> str | None:
        """Optional encryption configuration; absence disables encrypted message caching."""
        ...

    @property
    def translation_target_lang(self) -> str:
        """Default target language code."""
        ...

    @property
    def translation_ignored_langs(self) -> list[str]:
        """Default list of language codes to ignore for translation."""
        ...

    @property
    def instagram_sessionid(self) -> str | None:
        """Optional sessionid cookie for Instagram extraction."""
        ...

    @property
    def valkey_url(self) -> str:
        """Connection URL for Valkey."""
        ...

    @property
    def project_description(self) -> str:
        """Short project description."""
        ...

    @property
    def github_url(self) -> str:
        """URL to project GitHub repository."""
        ...

    @property
    def issue_tracker_url(self) -> str:
        """URL to project issue tracker."""
        ...

    @property
    def contact_email(self) -> str:
        """Contact email address."""
        ...


class UserStore(Protocol):
    """Protocol for user identity persistence operations."""

    async def get_by_id(self, user_id: int) -> UserDB | None:
        """Return an active user, or None for a missing or deleted identity."""
        ...

    async def get_by_telegram_id(self, telegram_id: int, platform: str = "telegram") -> UserDB | None:
        """Return an active user by numeric identity, or None when missing/deleted."""
        ...

    async def ingest_user(
        self, telegram_id: int, platform: str = "telegram", actor: ActorContext | None = None
    ) -> UserDB | None:
        """Ingest user from incoming evidence.

        Returns active UserDB if exists or newly created.
        Returns None if user was previously soft-deleted (tombstone-aware; does not resurrect).
        """
        ...

    async def soft_delete_user(
        self, telegram_id: int, actor: ActorContext, reason: str, platform: str = "telegram"
    ) -> bool:
        """Delete and audit an active identity; return False when already absent/deleted."""
        ...

    async def set_trust(
        self, telegram_id: int, *, is_trusted: bool, actor: ActorContext | None = None, platform: str = "telegram"
    ) -> bool:
        """Return whether an active user exists, including an unchanged trust flag."""
        ...

    async def get_historical_by_telegram_id(self, telegram_id: int, platform: str = "telegram") -> list[UserDB]:
        """List identity generations for recovery; an empty list means no history."""
        ...


@dataclass(frozen=True)
class ResolvedTarget:
    """Resolved target from public handle registry."""

    target_kind: TargetKind
    target_id: int
    handle_normalized: str
    handle_display: str
    platform: Platform = Platform.TELEGRAM


class PublicHandleStore(Protocol):
    """Protocol for active public-handle registry operations."""

    async def resolve_handle(self, handle: str, platform: Platform = Platform.TELEGRAM) -> ResolvedTarget | None:
        """Return an active public target, or None for invalid, absent or stale handles."""
        ...

    async def get_handle_by_target(
        self, target_kind: TargetKind, target_id: int, platform: Platform = Platform.TELEGRAM
    ) -> ResolvedTarget | None:
        """Return the stored handle mapping, or None when no mapping exists."""
        ...

    async def sync_handle(
        self,
        target_kind: TargetKind,
        target_id: int,
        raw_handle: str | None,
        actor: ActorContext,
        platform: Platform = Platform.TELEGRAM,
    ) -> ResolvedTarget | None:
        """Synchronize a handle with audit; return None when evidence removes it.

        Invalid nonempty handles raise InvalidHandleError. An unchanged valid
        mapping remains a successful result.
        """
        ...

    async def release_handle(
        self,
        target_kind: TargetKind,
        target_id: int,
        actor: ActorContext,
        platform: Platform = Platform.TELEGRAM,
    ) -> bool:
        """Release a handle with audit; return False when no mapping exists."""
        ...


class RoleStore(Protocol):
    """Durable single-role lookup and audited role assignment."""

    async def get_role(self, telegram_id: int, platform: Platform = Platform.TELEGRAM) -> Role | None:
        """Fetch the active user's role, or None for missing/deleted identities."""
        ...

    async def list_roles(self, role: Role | None = None) -> list[UserDB]:
        """List active authority assignments; by default include ADMIN and OWNER only."""
        ...

    async def set_role(
        self,
        telegram_id: int,
        role: Role,
        actor: ActorContext,
        platform: Platform = Platform.TELEGRAM,
    ) -> bool:
        """Set one role with audit; USER demotes authority.

        Return False for an unchanged role or tombstoned identity. Non-CLI
        actors raise PermissionError.
        """
        ...


class ChatStore(Protocol):
    """Protocol for chat persistence operations."""

    async def get_chat(self, chat_id: int, platform: str = "telegram") -> ChatDB | None:
        """Return the active chat for a current or historical route, or None if absent."""
        ...

    async def list_groups(self, after_id: int, limit: int, *, present_only: bool) -> list[ChatDB]:
        """Page canonical active Telegram groups without exposing rows to the caller directly."""
        ...

    async def get_or_create_chat(
        self, chat_id: int, platform: str = "telegram", actor: ActorContext | None = None
    ) -> ChatDB:
        """Return an active chat or create it; unavailable historical routes raise ValueError."""
        ...

    async def update_status(
        self, chat_id: int, status: ChatStatus, platform: str = "telegram", actor: ActorContext | None = None
    ) -> None:
        """Persist lifecycle status, provisioning a missing current route; invalid old routes raise."""
        ...

    async def update_translation_settings(
        self,
        chat_id: int,
        target_lang: str,
        ignored_langs: list[str],
        platform: str = "telegram",
        actor: ActorContext | None = None,
    ) -> None:
        """Persist translation settings, provisioning a missing current route; invalid old routes raise."""
        ...

    async def set_encryption_salt(
        self, chat_id: int, salt: str, platform: str = "telegram", actor: ActorContext | None = None
    ) -> None:
        """Persist the encryption salt, provisioning a missing current route; invalid old routes raise."""
        ...

    async def get_all_by_status(self, status: ChatStatus, platform: str = "telegram") -> list[ChatDB]:
        """Fetch all chats with a specific status."""
        ...

    async def soft_delete_chat(
        self, chat_id: int, actor: ActorContext, reason: str, platform: str = "telegram"
    ) -> bool:
        """Delete and audit an active identity; return False when already absent/deleted."""
        ...

    async def get_historical_by_chat_id(self, chat_id: int, platform: str = "telegram") -> list[ChatDB]:
        """List chat identity generations; an empty list means no history."""
        ...


class CommandStore(Protocol):
    """Protocol for command allowance and association operations."""

    async def legacy_capture_allowed(self, chat_id: int, platform: str = "telegram") -> bool:
        """Allow empty capture only for an active migrated compatibility configuration."""
        ...

    async def initialize_defaults(self, chat_id: int, commands: tuple[str, ...], platform: str = "telegram") -> None:
        """Initialize defaults once, preserving explicit selections and legacy state.

        Already configured or absent chats are no-ops; unknown default commands
        raise ValueError when initialization is needed.
        """
        ...

    async def get_allowed_commands(self, chat_id: int, platform: str = "telegram") -> list[str]:
        """List configured commands; no selections or missing chat yields an empty list."""
        ...

    async def enable_command(
        self, chat_id: int, command_name: str, platform: str = "telegram", actor: ActorContext | None = None
    ) -> bool:
        """Return command acceptance, including an already enabled command.

        Unknown commands or deleted chats return False; failed authorization
        raises PermissionError. Acceptance does not imply a new audit change.
        """
        ...

    async def disable_command(
        self, chat_id: int, command_name: str, platform: str = "telegram", actor: ActorContext | None = None
    ) -> bool:
        """Return removal or legacy-state change, not generic persistence success.

        Missing chat/command or an already disabled command may return False;
        failed authorization raises PermissionError.
        """
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


class UnauthorizedPromptSender(Protocol):
    """Protocol for sending unauthorized guidance and approval prompts."""

    async def send(self, bot: Bot, chat_id: int) -> bool:
        """Return True after sending, False for an ineligible target or caught send failure.

        This is best-effort guidance, not a delivery receipt or retry guarantee.
        """
        ...


@runtime_checkable
class AuditedUnitOfWork(Protocol):
    """Protocol for transactional execution with atomic audit logging."""

    async def execute(
        self,
        actor: ActorContext,
        target_type: str,
        target_id: str | int,
        action: str,
        operation: Callable[[Any], Awaitable[Any]],
        changed_fields: dict[str, Any] | Callable[[Any], dict[str, Any] | None] | None = None,
        audit_if: Callable[[Any], bool] | None = None,
    ) -> Any:
        """Return the callback result after atomic mutation and conditional audit.

        Callbacks perform database work only because a whole transaction may
        retry. Validation and exhausted persistence failures raise and roll back
        mutation and audit together; audit_if only controls event creation.
        """
        ...


class AuthorizationPolicy(Protocol):
    """Pure permission evaluation; no identity storage or transport dependencies."""

    def is_allowed(self, facts: PermissionFacts) -> bool:
        """Evaluate current facts against the loaded policy; unmatched actions deny."""
        ...
