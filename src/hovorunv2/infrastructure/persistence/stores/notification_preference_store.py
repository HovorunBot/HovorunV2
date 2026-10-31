"""Audited notification preference mutations through the shared unit of work."""

from functools import partial

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from hovorunv2.application.access.permissions import PermissionTarget
from hovorunv2.application.auditing import AuditedUnitOfWork
from hovorunv2.application.operations.notification_preferences import (
    NotificationCategory,
    NotificationPreference,
    NotificationPreferenceStore,
)
from hovorunv2.domain.audit import ActorContext
from hovorunv2.infrastructure.persistence.repositories.notification_preference_repository import (
    NotificationPreferenceRepository,
)


class SQLAlchemyNotificationPreferenceStore(NotificationPreferenceStore):
    """Keep explicit switches separate from routine broadcast and request feedback."""

    def __init__(self, session_maker: async_sessionmaker, audited_uow: AuditedUnitOfWork) -> None:
        self._session_maker = session_maker
        self._audited_uow = audited_uow

    async def list(self, target: PermissionTarget) -> tuple[NotificationPreference, ...]:
        """Read active overrides through an owning session.

        Args:
            target: Permission target identifying the subject.

        Returns:
            Active category overrides for the target.
        """
        async with self._session_maker() as session:
            return await NotificationPreferenceRepository(session).preferences(target)

    async def _mutate(
        self,
        session: AsyncSession,
        target: PermissionTarget,
        category: NotificationCategory,
        *,
        enabled: bool,
    ) -> tuple[bool, dict[str, object]]:
        """Apply one switch through the session-bound repository."""
        return await NotificationPreferenceRepository(session).set(target, category, enabled=enabled)

    async def set(
        self,
        target: PermissionTarget,
        category: NotificationCategory,
        *,
        enabled: bool,
        actor: ActorContext,
    ) -> bool:
        """Commit one explicit switch and its audit event atomically.

        Args:
            target: Permission target identifying the subject.
            category: Notification category being switched.
            enabled: Whether the category is enabled.
            actor: Actor context recorded on the audit event.

        Returns:
            True if the switch changed.
        """
        result: tuple[bool, dict[str, object]] = await self._audited_uow.execute(
            actor=actor,
            target_type="notification_preference",
            target_id=f"{target.kind}:{target.entity_id}:{category.value}",
            action="set",
            operation=partial(self._mutate, target=target, category=category, enabled=enabled),
            changed_fields=lambda outcome: outcome[1],
            audit_if=lambda outcome: outcome[0],
        )
        return result[0]
