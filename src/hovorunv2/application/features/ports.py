"""Application ports (Protocols) defining boundaries for external adapters.

Optional lookups describe expected absence. Boolean mutations report their documented
no-op or acceptance outcome; they are not generic availability checks. Required
operations propagate failures to the owning workflow. Injected adapters are mandatory.
"""

from typing import Protocol

from hovorunv2.domain.audit import ActorContext


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
