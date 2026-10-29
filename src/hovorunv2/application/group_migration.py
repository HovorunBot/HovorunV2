"""Verified Telegram routing transitions and active canonical delivery resolution."""

from typing import Protocol


class GroupMigrationStore(Protocol):
    """Preserve group identity and configuration through Telegram service events."""

    async def migrate(self, old_id: int, new_id: int) -> bool:
        """Change routing atomically; replaying the same migration returns False.

        Invalid IDs, unavailable or stale sources, and conflicting target
        identities or handle mappings raise ValueError.
        """
        ...

    async def canonical_id(self, telegram_id: int) -> int | None:
        """Return the active canonical group route; missing/deleted/non-group targets return None."""
        ...
