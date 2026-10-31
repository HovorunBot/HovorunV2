"""Read-only dependant counts used by the recovery preview."""

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql import FromClause

from hovorunv2.application.identity.recovery import RecoveryStore
from hovorunv2.domain.beta import BetaEntitlementDB
from hovorunv2.domain.chat import chat_commands
from hovorunv2.domain.command import CommandDB
from hovorunv2.domain.group_beta import GroupBetaEnablerDB, GroupBetaFeatureDB
from hovorunv2.domain.group_grant import GroupApprovalDB
from hovorunv2.domain.membership import GroupMembershipStateDB, MemberEvidenceDB
from hovorunv2.domain.notification import NotificationPreferenceDB


class SQLAlchemyRecoveryStore(RecoveryStore):
    """Count retained rows without exposing their content."""

    def __init__(self, session_maker: async_sessionmaker) -> None:
        self._session_maker = session_maker

    async def user_dependants(self, user_id: int, telegram_id: int) -> dict[str, int]:
        """Count grants, memberships, group support and preferences for a tombstoned user."""
        async with self._session_maker() as session:
            return {
                "beta_grants": await self._count(
                    session,
                    BetaEntitlementDB,
                    BetaEntitlementDB.user_id == user_id,
                    BetaEntitlementDB.revoked_at.is_(None),
                    BetaEntitlementDB.deleted_at.is_(None),
                ),
                "memberships": await self._count(
                    session,
                    MemberEvidenceDB,
                    MemberEvidenceDB.user_id == user_id,
                    MemberEvidenceDB.deleted_at.is_(None),
                ),
                "group_adminships": await self._count(
                    session,
                    GroupBetaEnablerDB,
                    GroupBetaEnablerDB.user_id == user_id,
                    GroupBetaEnablerDB.revoked_at.is_(None),
                    GroupBetaEnablerDB.deleted_at.is_(None),
                ),
                "notification_preferences": await self._count(
                    session,
                    NotificationPreferenceDB,
                    NotificationPreferenceDB.user_id == user_id,
                    NotificationPreferenceDB.deleted_at.is_(None),
                ),
            }

    async def group_dependants(self, chat_id: int) -> dict[str, int]:
        """Count retained selections, membership state, preferences and approvals."""
        async with self._session_maker() as session:
            return {
                "commands": await self._count(session, chat_commands, chat_commands.c.chat_id == chat_id),
                "beta_selections": await self._count(
                    session,
                    GroupBetaFeatureDB,
                    GroupBetaFeatureDB.group_id == chat_id,
                    GroupBetaFeatureDB.deleted_at.is_(None),
                ),
                "membership_state": await self._count(
                    session,
                    GroupMembershipStateDB,
                    GroupMembershipStateDB.group_id == chat_id,
                    GroupMembershipStateDB.deleted_at.is_(None),
                ),
                "notification_preferences": await self._count(
                    session,
                    NotificationPreferenceDB,
                    NotificationPreferenceDB.group_id == chat_id,
                    NotificationPreferenceDB.deleted_at.is_(None),
                ),
                "approvals": await self._count(
                    session,
                    GroupApprovalDB,
                    GroupApprovalDB.group_id == chat_id,
                    GroupApprovalDB.deleted_at.is_(None),
                ),
            }

    async def group_commands(self, chat_id: int) -> tuple[str, ...]:
        """List command names including soft-deleted association history."""
        async with self._session_maker() as session:
            rows = await session.scalars(
                select(CommandDB.name)
                .join(chat_commands, CommandDB.id == chat_commands.c.command_id)
                .where(chat_commands.c.chat_id == chat_id)
                .order_by(CommandDB.name)
            )
            return tuple(rows.all())

    async def _count(self, session: AsyncSession, model: FromClause | type, *conditions: ColumnElement[bool]) -> int:
        statement = select(func.count()).select_from(model)
        for condition in conditions:
            statement = statement.where(condition)
        return int(await session.scalar(statement) or 0)
