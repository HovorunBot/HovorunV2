"""Minimal observed group identity boundary."""

from typing import Protocol

from hovorunv2.domain.chat import ChatDB


class GroupIdentityStore(Protocol):
    """Persist public group metadata without restoring deleted identities."""

    async def ingest_group(self, telegram_id: int, title: str | None, chat_type: str) -> ChatDB | None:
        """Return the active observed group, or None for a tombstone."""
