"""Application ports (Protocols) defining boundaries for external adapters."""

from collections.abc import Callable, Mapping
from typing import Any, Protocol

from aiogram import Bot

from hovorunv2.domain.chat import ChatDB
from hovorunv2.domain.enums import ChatStatus


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


class ChatStore(Protocol):
    """Protocol for chat persistence operations."""

    async def get_chat(self, chat_id: int, platform: str = "telegram") -> ChatDB | None:
        """Fetch chat from database."""
        ...

    async def get_or_create_chat(self, chat_id: int, platform: str = "telegram") -> ChatDB:
        """Fetch chat from database or create it if not exists."""
        ...

    async def update_status(self, chat_id: int, platform: str, status: ChatStatus) -> None:
        """Update lifecycle status for a chat."""
        ...

    async def update_translation_settings(
        self, chat_id: int, platform: str, target_lang: str, ignored_langs: list[str]
    ) -> None:
        """Update translation settings for a chat."""
        ...

    async def set_encryption_salt(self, chat_id: int, salt: str, platform: str = "telegram") -> None:
        """Set encryption salt for a chat."""
        ...

    async def get_all_by_status(self, status: ChatStatus, platform: str) -> list[ChatDB]:
        """Fetch all chats with a specific status."""
        ...


class CommandStore(Protocol):
    """Protocol for command allowance and association operations."""

    async def get_allowed_commands(self, chat_id: int, platform: str) -> list[str]:
        """Fetch allowed commands for a chat."""
        ...

    async def enable_command(self, chat_id: int, platform: str, command_name: str) -> bool:
        """Enable specific command for a chat."""
        ...

    async def disable_command(self, chat_id: int, platform: str, command_name: str) -> bool:
        """Disable specific command for a chat."""
        ...


class SystemStore(Protocol):
    """Protocol for system-wide state persistence operations."""

    async def get_last_notified_version(self) -> str | None:
        """Fetch last notified version from database."""
        ...

    async def set_last_notified_version(self, version: str) -> None:
        """Update last notified version in database."""
        ...

    async def get_value(self, key: str) -> str | None:
        """Fetch arbitrary value from system storage."""
        ...

    async def set_value(self, key: str, value: str) -> None:
        """Store arbitrary value in system storage."""
        ...


class UnauthorizedPromptSender(Protocol):
    """Protocol for sending unauthorized guidance and approval prompts."""

    async def send(self, bot: Bot, chat_id: int) -> bool:
        """Send prompt to chat. Returns True if sent, False otherwise."""
        ...
