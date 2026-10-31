"""Application ports (Protocols) defining boundaries for external adapters.

Optional lookups describe expected absence. Boolean mutations report their documented
no-op or acceptance outcome; they are not generic availability checks. Required
operations propagate failures to the owning workflow. Injected adapters are mandatory.
"""

from typing import Protocol

from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.chat import ChatDB
from hovorunv2.domain.enums import ChatStatus


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
