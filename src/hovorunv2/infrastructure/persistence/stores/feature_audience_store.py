"""Select the scopes that actually use an emergency-affected feature."""

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from hovorunv2.application.access.permissions import PermissionTarget
from hovorunv2.application.operations.feature_status import FeatureAudienceStore
from hovorunv2.domain.chat import ChatDB
from hovorunv2.domain.command import CommandDB
from hovorunv2.domain.enums import PROVIDER_SPLIT_COMMANDS, ChatStatus
from hovorunv2.domain.group_beta import GroupBetaEnablerDB, GroupBetaFeatureDB
from hovorunv2.domain.notification_event import NotificationEventDB
from hovorunv2.domain.user import UserDB

MAX_FEATURE_STATUS_RECIPIENTS = 1000


class SQLAlchemyFeatureAudienceStore(FeatureAudienceStore):
    """Read only active selections for the provider's split commands."""

    def __init__(self, session_maker: async_sessionmaker) -> None:
        self._session_maker = session_maker

    async def selected_targets(self, feature_key: str) -> tuple[PermissionTarget, ...]:
        """Return active users and approved groups with any enabled split of the provider."""
        splits = PROVIDER_SPLIT_COMMANDS.get(feature_key, (feature_key,))
        async with self._session_maker() as session:
            groups = await self._groups(session, splits)
            users = await self._users(session, splits)
        targets = [PermissionTarget("group", chat.id, chat.generation, chat.chat_id) for chat in groups]
        targets.extend(PermissionTarget("user", user.id, user.generation, user.telegram_id) for user in users)
        deduped: dict[tuple[str, int], PermissionTarget] = {}
        for target in targets:
            deduped[(target.kind, target.entity_id or 0)] = target
        return tuple(deduped.values())[:MAX_FEATURE_STATUS_RECIPIENTS]

    async def announced(self, event_key: str) -> bool:
        """Return whether the transition event already exists."""
        async with self._session_maker() as session:
            return (
                await session.scalar(select(NotificationEventDB.id).where(NotificationEventDB.event_key == event_key))
                is not None
            )

    async def _groups(self, session: AsyncSession, splits: tuple[str, ...]) -> list[ChatDB]:
        selected = (
            await session.scalars(
                select(ChatDB)
                .join(GroupBetaFeatureDB, GroupBetaFeatureDB.group_id == ChatDB.id)
                .where(
                    GroupBetaFeatureDB.feature.in_(splits),
                    GroupBetaFeatureDB.selected.is_(True),
                    GroupBetaFeatureDB.deleted_at.is_(None),
                    ChatDB.status == ChatStatus.APPROVED,
                    ChatDB.deleted_at.is_(None),
                    ChatDB.chat_id < 0,
                    exists().where(
                        GroupBetaEnablerDB.selection_id == GroupBetaFeatureDB.id,
                        GroupBetaEnablerDB.revoked_at.is_(None),
                        GroupBetaEnablerDB.deleted_at.is_(None),
                    ),
                )
                .limit(MAX_FEATURE_STATUS_RECIPIENTS)
            )
        ).all()
        enabled = (
            await session.scalars(
                select(ChatDB)
                .join(ChatDB.commands)
                .where(
                    CommandDB.name.in_(splits),
                    ChatDB.status == ChatStatus.APPROVED,
                    ChatDB.deleted_at.is_(None),
                    ChatDB.chat_id < 0,
                )
                .limit(MAX_FEATURE_STATUS_RECIPIENTS)
            )
        ).all()
        deduped: dict[int, ChatDB] = {chat.id: chat for chat in (*selected, *enabled)}
        return list(deduped.values())

    async def _users(self, session: AsyncSession, splits: tuple[str, ...]) -> list[UserDB]:
        return list(
            (
                await session.scalars(
                    select(UserDB)
                    .join(ChatDB, ChatDB.chat_id == UserDB.telegram_id)
                    .join(ChatDB.commands)
                    .where(
                        CommandDB.name.in_(splits),
                        ChatDB.chat_id > 0,
                        ChatDB.deleted_at.is_(None),
                        UserDB.deleted_at.is_(None),
                        UserDB.platform == "telegram",
                    )
                    .distinct()
                    .limit(MAX_FEATURE_STATUS_RECIPIENTS)
                )
            ).all()
        )
