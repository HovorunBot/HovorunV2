"""Application service for role evaluation and CLI-managed role assignment."""

from hovorunv2.application.access.ports import RoleStore
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.enums import ActorOrigin, Platform, Role
from hovorunv2.domain.user import UserDB


class RoleService:
    """Service governing role assignments and administrative authority."""

    def __init__(self, role_store: RoleStore) -> None:
        """Initialize service with role store."""
        self._role_store = role_store

    async def get_role(self, telegram_id: int, platform: Platform = Platform.TELEGRAM) -> Role | None:
        """Fetch active user's single authority role."""
        return await self._role_store.get_role(telegram_id, platform)

    async def has_role(self, telegram_id: int, role: Role, platform: Platform = Platform.TELEGRAM) -> bool:
        """Check if user has a specific application role."""
        return await self.get_role(telegram_id, platform) == role

    async def has_at_least_role(self, telegram_id: int, minimum: Role, platform: Platform = Platform.TELEGRAM) -> bool:
        """Return whether the user holds at least the given authority tier.

        Args:
            telegram_id: Numeric Telegram identity of the user.
            minimum: Lowest role that satisfies the check.
            platform: Platform to look up.

        Returns:
            Whether the active role meets or exceeds the minimum tier.
        """
        role = await self.get_role(telegram_id, platform)
        return role is not None and role >= minimum

    async def is_owner(self, telegram_id: int, platform: Platform = Platform.TELEGRAM) -> bool:
        """Check if user is a designated Owner."""
        return await self.has_role(telegram_id, Role.OWNER, platform)

    async def is_effective_admin(self, telegram_id: int, platform: Platform = Platform.TELEGRAM) -> bool:
        """Check if user is an effective admin (Owner OR Admin). Owner always implies admin."""
        return await self.has_at_least_role(telegram_id, Role.ADMIN, platform)

    async def list_roles(self, role: Role | None = None) -> list[UserDB]:
        """List active assignments for offline administration."""
        return await self._role_store.list_roles(role)

    async def set_role(
        self,
        telegram_id: int,
        role: Role,
        actor: ActorContext,
        platform: Platform = Platform.TELEGRAM,
    ) -> bool:
        """Assign a role from the CLI origin only.

        Args:
            telegram_id: Numeric Telegram identity of the user.
            role: Role to assign; USER demotes authority.
            actor: Audited actor performing the assignment.
            platform: Platform to assign the role on.

        Returns:
            Whether the role assignment was applied.

        Raises:
            PermissionError: If the actor is not the CLI origin.
        """
        if actor.origin != ActorOrigin.CLI:
            msg = "Role assignment is strictly restricted to CLI origin."
            raise PermissionError(msg)
        return await self._role_store.set_role(telegram_id, role, actor, platform)
