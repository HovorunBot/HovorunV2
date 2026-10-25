"""Application ports (Protocols) defining boundaries for external adapters."""

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from aiogram import Bot

from hovorunv2.application.permissions import PermissionFacts
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.chat import ChatDB
from hovorunv2.domain.enums import ChatStatus, Platform, Role, TargetKind
from hovorunv2.domain.user import UserDB


class CachePort(Protocol):
    """Protocol for key-value and sorted-set caching operations."""

    async def get(self, key: str, default: Any = None) -> Any:
        """Retrieve a value from the cache by key."""
        ...

    async def set(self, key: str, value: Any, expire: int | None = None) -> None:
        """Store a value in the cache with optional TTL."""
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
        """Fetch rendered HTML content of given URL."""
        ...

    async def extract_and_download(
        self,
        url: str,
        extractor_fn: Callable[..., Any],
        wait_selector: str | None = None,
        cookies: list[dict[str, Any]] | None = None,
    ) -> tuple[Any, list[bytes]]:
        """Fetch URL, extract metadata, and download media in browser session."""
        ...


class TaskQueuePort(Protocol):
    """Protocol for asynchronous background task queuing and scheduling."""

    def register_function(self, fn: Callable[..., Any]) -> None:
        """Register a worker task callback function."""
        ...

    def register_cron(self, fn: Callable[..., Any], cron: str) -> None:
        """Register a scheduled cron job callback."""
        ...

    async def enqueue(self, function_name: str, **kwargs: Any) -> None:
        """Enqueue a background task by registered name."""
        ...


class AppSettings(Protocol):
    """Protocol for application configuration properties needed by application services."""

    @property
    def owners(self) -> list[int]:
        """List of bot owner user IDs."""
        ...

    @property
    def admin_ids(self) -> list[int]:
        """List of bot admin user IDs."""
        ...

    @property
    def cache_encryption_key(self) -> str | None:
        """Master secret key for per-chat PBKDF2 message encryption."""
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
        """Fetch active user by internal entity ID."""
        ...

    async def get_by_telegram_id(self, telegram_id: int, platform: str = "telegram") -> UserDB | None:
        """Fetch active user by platform and numeric Telegram ID."""
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
        """Soft-delete an active user identity recording actor and reason."""
        ...

    async def set_trust(
        self, telegram_id: int, *, is_trusted: bool, actor: ActorContext | None = None, platform: str = "telegram"
    ) -> bool:
        """Update trust flag for an active user."""
        ...

    async def get_historical_by_telegram_id(self, telegram_id: int, platform: str = "telegram") -> list[UserDB]:
        """Fetch all generations of user identity for explicit recovery workflows."""
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
        """Resolve public @username to stable numeric target (active only)."""
        ...

    async def get_handle_by_target(
        self, target_kind: TargetKind, target_id: int, platform: Platform = Platform.TELEGRAM
    ) -> ResolvedTarget | None:
        """Get current public handle for a numeric target entity."""
        ...

    async def sync_handle(
        self,
        target_kind: TargetKind,
        target_id: int,
        raw_handle: str | None,
        actor: ActorContext,
        platform: Platform = Platform.TELEGRAM,
    ) -> ResolvedTarget | None:
        """Synchronize public handle from verified Telegram evidence with audit."""
        ...

    async def release_handle(
        self,
        target_kind: TargetKind,
        target_id: int,
        actor: ActorContext,
        platform: Platform = Platform.TELEGRAM,
    ) -> bool:
        """Release public handle on target soft-deletion or removal with audit."""
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
        """Set one role atomically with audit; USER explicitly demotes authority."""
        ...


class ChatStore(Protocol):
    """Protocol for chat persistence operations."""

    async def get_chat(self, chat_id: int, platform: str = "telegram") -> ChatDB | None:
        """Fetch chat from database."""
        ...

    async def get_or_create_chat(
        self, chat_id: int, platform: str = "telegram", actor: ActorContext | None = None
    ) -> ChatDB:
        """Fetch chat from database or create it if not exists."""
        ...

    async def update_status(
        self, chat_id: int, status: ChatStatus, platform: str = "telegram", actor: ActorContext | None = None
    ) -> None:
        """Update lifecycle status for a chat."""
        ...

    async def update_translation_settings(
        self,
        chat_id: int,
        target_lang: str,
        ignored_langs: list[str],
        platform: str = "telegram",
        actor: ActorContext | None = None,
    ) -> None:
        """Update translation settings for a chat."""
        ...

    async def set_encryption_salt(
        self, chat_id: int, salt: str, platform: str = "telegram", actor: ActorContext | None = None
    ) -> None:
        """Set encryption salt for a chat."""
        ...

    async def get_all_by_status(self, status: ChatStatus, platform: str = "telegram") -> list[ChatDB]:
        """Fetch all chats with a specific status."""
        ...

    async def soft_delete_chat(
        self, chat_id: int, actor: ActorContext, reason: str, platform: str = "telegram"
    ) -> bool:
        """Soft-delete an active chat identity recording actor and reason."""
        ...

    async def get_historical_by_chat_id(self, chat_id: int, platform: str = "telegram") -> list[ChatDB]:
        """Fetch all generations of chat identity for explicit recovery workflows."""
        ...


class CommandStore(Protocol):
    """Protocol for command allowance and association operations."""

    async def get_allowed_commands(self, chat_id: int, platform: str = "telegram") -> list[str]:
        """Fetch allowed commands for a chat."""
        ...

    async def enable_command(
        self, chat_id: int, command_name: str, platform: str = "telegram", actor: ActorContext | None = None
    ) -> bool:
        """Enable specific command for a chat."""
        ...

    async def disable_command(
        self, chat_id: int, command_name: str, platform: str = "telegram", actor: ActorContext | None = None
    ) -> bool:
        """Disable specific command for a chat."""
        ...


class SystemStore(Protocol):
    """Protocol for system-wide state persistence operations."""

    async def get_last_notified_version(self) -> str | None:
        """Fetch last notified version from database."""
        ...

    async def set_last_notified_version(self, version: str, actor: ActorContext | None = None) -> None:
        """Update last notified version in database."""
        ...

    async def get_value(self, key: str) -> str | None:
        """Fetch arbitrary value from system storage."""
        ...

    async def set_value(self, key: str, value: str, actor: ActorContext | None = None) -> None:
        """Store arbitrary value in system storage."""
        ...


class UnauthorizedPromptSender(Protocol):
    """Protocol for sending unauthorized guidance and approval prompts."""

    async def send(self, bot: Bot, chat_id: int) -> bool:
        """Send prompt to chat. Returns True if sent, False otherwise."""
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
        """Execute a business operation and append an audit event atomically."""
        ...


class AuthorizationPolicy(Protocol):
    """Pure permission evaluation; no identity storage or transport dependencies."""

    def is_allowed(self, facts: PermissionFacts) -> bool:
        """Evaluate current facts against the loaded policy; unmatched actions deny."""
        ...
