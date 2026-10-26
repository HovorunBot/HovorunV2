"""Verified Telegram routing transitions and active canonical delivery resolution."""

from typing import Protocol


class GroupMigrationStore(Protocol):
    """Preserve group identity and configuration through Telegram service events."""

    async def migrate(self, old_id: int, new_id: int) -> bool:
        """Atomically change routing; return False for an identical replay."""
        ...

    async def canonical_id(self, telegram_id: int) -> int | None:
        """Resolve current routing only for an active group generation."""
        ...
