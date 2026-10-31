"""Application ports (Protocols) defining boundaries for external adapters.

Optional lookups describe expected absence. Boolean mutations report their documented
no-op or acceptance outcome; they are not generic availability checks. Required
operations propagate failures to the owning workflow. Injected adapters are mandatory.
"""

from typing import Protocol

from aiogram import Bot

from hovorunv2.application.access.permissions import PermissionFacts
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.enums import Platform, Role
from hovorunv2.domain.user import UserDB


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

        Args:
            telegram_id: Numeric Telegram identity of the user.
            role: Role to assign.
            actor: Audited actor performing the assignment.
            platform: Platform to assign the role on.

        Returns:
            Whether the role changed for an active identity.
        """
        ...


class UnauthorizedPromptSender(Protocol):
    """Protocol for sending unauthorized guidance and approval prompts."""

    async def send(self, bot: Bot, chat_id: int) -> bool:
        """Send unauthorized guidance and report whether it was attempted.

        This is best-effort guidance, not a delivery receipt or retry guarantee.

        Args:
            bot: Telegram bot used to send the prompt.
            chat_id: Numeric Telegram identity of the target chat.

        Returns:
            True after sending; False for an ineligible target or a caught send failure.
        """
        ...


class AuthorizationPolicy(Protocol):
    """Pure permission evaluation; no identity storage or transport dependencies."""

    def is_allowed(self, facts: PermissionFacts) -> bool:
        """Evaluate current facts against the loaded policy; unmatched actions deny."""
        ...
