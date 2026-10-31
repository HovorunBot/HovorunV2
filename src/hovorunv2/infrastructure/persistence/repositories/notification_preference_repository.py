"""Session-bound lifecycle filtering and recoverable notification preference mutations."""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hovorunv2.application.access.permissions import PermissionTarget
from hovorunv2.application.operations.notification_preferences import (
    NotificationCategory,
    NotificationPreference,
)
from hovorunv2.domain.chat import ChatDB
from hovorunv2.domain.notification import NotificationPreferenceDB
from hovorunv2.domain.user import UserDB
from hovorunv2.infrastructure.persistence.repositories.policy_queries import exact_target


class NotificationPreferenceRepository:
    """Read and persist exact active-generation category overrides."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def preferences(self, target: PermissionTarget) -> tuple[NotificationPreference, ...]:
        """Return active overrides only for the exact current target generation.

        Args:
            target: Permission target identifying the subject.

        Returns:
            Active category overrides for the target.
        """
        return tuple(
            NotificationPreference(NotificationCategory(row.category), row.enabled) for row in await self._rows(target)
        )

    async def set(
        self, target: PermissionTarget, category: NotificationCategory, *, enabled: bool
    ) -> tuple[bool, dict[str, object]]:
        """Write or update one explicit switch; unchanged values are a no-op.

        Args:
            target: Permission target identifying the subject.
            category: Notification category being switched.
            enabled: Whether the category is enabled.

        Returns:
            A pair of whether the switch changed and the audit changes.

        Raises:
            ValueError: If the exact active target generation is unavailable.
        """
        if await self._active_target(target) is None:
            msg = "Notification preferences require the exact active target generation."
            raise ValueError(msg)
        row = next((row for row in await self._rows(target) if row.category == category.value), None)
        before = row.enabled if row is not None else True
        if row is None:
            row = NotificationPreferenceDB(
                user_id=target.entity_id if target.kind == "user" else None,
                group_id=target.entity_id if target.kind == "group" else None,
                category=category.value,
                enabled=enabled,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            self._session.add(row)
        else:
            if row.enabled == enabled:
                return False, {}
            row.enabled = enabled
            row.updated_at = datetime.now(UTC)
        await self._session.flush()
        return True, {"category": category.value, "enabled": [before, enabled]}

    async def _rows(self, target: PermissionTarget) -> list[NotificationPreferenceDB]:
        """Never expose overrides whose subject generation is no longer active."""
        if await self._active_target(target) is None:
            return []
        subject = NotificationPreferenceDB.user_id if target.kind == "user" else NotificationPreferenceDB.group_id
        return list(
            (
                await self._session.scalars(
                    select(NotificationPreferenceDB)
                    .where(subject == target.entity_id, NotificationPreferenceDB.deleted_at.is_(None))
                    .order_by(NotificationPreferenceDB.category)
                )
            ).all()
        )

    async def _active_target(self, target: PermissionTarget) -> UserDB | ChatDB | None:
        """Return the exact active target generation, or None."""
        statement = exact_target(target)
        return await self._session.scalar(statement) if statement is not None else None
