"""Audited beta decisions and eligible-subject enumeration."""

from datetime import UTC, datetime
from functools import partial

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from hovorunv2.application.access.permissions import PermissionTarget
from hovorunv2.application.auditing import AuditedUnitOfWork
from hovorunv2.application.features.beta_prompts import BetaDecision, BetaPromptStore
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.beta import ALL_BETA, BetaEntitlementDB
from hovorunv2.domain.beta_decision import BetaDecisionDB
from hovorunv2.domain.chat import ChatDB
from hovorunv2.domain.enums import ChatStatus
from hovorunv2.domain.group_beta import GroupBetaEnablerDB, GroupBetaFeatureDB
from hovorunv2.domain.notification_event import NotificationEventDB
from hovorunv2.domain.user import UserDB
from hovorunv2.infrastructure.persistence.repositories.policy_queries import exact_target

MAX_BETA_RECIPIENTS = 1000


class SQLAlchemyBetaPromptStore(BetaPromptStore):
    """Keep decisions recoverable and select only the announced eligible subset."""

    def __init__(self, session_maker: async_sessionmaker, audited_uow: AuditedUnitOfWork) -> None:
        self._session_maker = session_maker
        self._audited_uow = audited_uow

    async def decision(self, target: PermissionTarget, feature: str) -> BetaDecision | None:
        """Read one active decision for the exact subject generation."""
        async with self._session_maker() as session:
            row = await self._row(session, target, feature)
            return None if row is None else BetaDecision(row.decision)

    async def set_decision(
        self, target: PermissionTarget, feature: str, decision: BetaDecision, *, actor: ActorContext
    ) -> bool:
        """Upsert one decision atomically with audit."""
        result: tuple[bool, dict[str, object]] = await self._audited_uow.execute(
            actor=actor,
            target_type="beta_decision",
            target_id=f"{target.kind}:{target.entity_id}:{feature}",
            action="set",
            operation=partial(self._mutate, target=target, feature=feature, decision=decision),
            changed_fields=lambda outcome: outcome[1],
            audit_if=lambda outcome: outcome[0],
        )
        return result[0]

    async def eligible_targets(self, feature: str) -> tuple[PermissionTarget, ...]:
        """Return active eligible users and qualifying approved groups, bounded."""
        async with self._session_maker() as session:
            users = await self._eligible_users(session, feature)
            groups = await self._eligible_groups(session, feature)
            targets = [PermissionTarget("user", user.id, user.generation, user.telegram_id) for user in users]
            targets.extend(PermissionTarget("group", chat.id, chat.generation, chat.chat_id) for chat in groups)
            deduped: dict[tuple[str, int], PermissionTarget] = {}
            for target in targets:
                deduped[(target.kind, target.entity_id or 0)] = target
            return tuple(deduped.values())[:MAX_BETA_RECIPIENTS]

    async def announced(self, event_key: str) -> bool:
        """Return whether the immutable introduction event already exists."""
        async with self._session_maker() as session:
            return (
                await session.scalar(select(NotificationEventDB.id).where(NotificationEventDB.event_key == event_key))
                is not None
            )

    async def _mutate(
        self,
        session: AsyncSession,
        *,
        target: PermissionTarget,
        feature: str,
        decision: BetaDecision,
    ) -> tuple[bool, dict[str, object]]:
        statement = exact_target(target)
        if statement is None or await session.scalar(statement) is None:
            msg = "Beta decision requires the exact active target generation."
            raise ValueError(msg)
        row = await self._row(session, target, feature)
        before = row.decision if row is not None else None
        if row is None:
            row = BetaDecisionDB(
                user_id=target.entity_id if target.kind == "user" else None,
                group_id=target.entity_id if target.kind == "group" else None,
                feature=feature,
                decision=decision.value,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            session.add(row)
        else:
            if row.decision == decision.value:
                return False, {}
            row.decision = decision.value
            row.updated_at = datetime.now(UTC)
        await session.flush()
        return True, {"decision": [before, decision.value], "feature": feature}

    async def _row(self, session: AsyncSession, target: PermissionTarget, feature: str) -> BetaDecisionDB | None:
        subject = BetaDecisionDB.user_id if target.kind == "user" else BetaDecisionDB.group_id
        return await session.scalar(
            select(BetaDecisionDB).where(
                subject == target.entity_id,
                BetaDecisionDB.feature == feature,
                BetaDecisionDB.deleted_at.is_(None),
            )
        )

    async def _eligible_users(self, session: AsyncSession, feature: str) -> list[UserDB]:
        return list(
            (
                await session.scalars(
                    select(UserDB)
                    .join(BetaEntitlementDB, BetaEntitlementDB.user_id == UserDB.id)
                    .where(
                        UserDB.platform == "telegram",
                        UserDB.deleted_at.is_(None),
                        BetaEntitlementDB.feature.in_((feature, ALL_BETA)),
                        BetaEntitlementDB.revoked_at.is_(None),
                        BetaEntitlementDB.deleted_at.is_(None),
                    )
                    .distinct()
                    .limit(MAX_BETA_RECIPIENTS)
                )
            ).all()
        )

    async def _eligible_groups(self, session: AsyncSession, feature: str) -> list[ChatDB]:
        selected = (
            await session.scalars(
                select(ChatDB)
                .join(GroupBetaFeatureDB, GroupBetaFeatureDB.group_id == ChatDB.id)
                .where(
                    GroupBetaFeatureDB.feature == feature,
                    GroupBetaFeatureDB.selected.is_(True),
                    GroupBetaFeatureDB.deleted_at.is_(None),
                    ChatDB.status == ChatStatus.APPROVED,
                    ChatDB.deleted_at.is_(None),
                    ChatDB.chat_type.in_(("group", "supergroup")),
                    exists().where(
                        GroupBetaEnablerDB.selection_id == GroupBetaFeatureDB.id,
                        GroupBetaEnablerDB.revoked_at.is_(None),
                        GroupBetaEnablerDB.deleted_at.is_(None),
                    ),
                )
                .limit(MAX_BETA_RECIPIENTS)
            )
        ).all()
        granted = (
            await session.scalars(
                select(ChatDB)
                .join(BetaEntitlementDB, BetaEntitlementDB.group_id == ChatDB.id)
                .where(
                    BetaEntitlementDB.feature == feature,
                    BetaEntitlementDB.revoked_at.is_(None),
                    BetaEntitlementDB.deleted_at.is_(None),
                    ChatDB.status == ChatStatus.APPROVED,
                    ChatDB.deleted_at.is_(None),
                    ChatDB.chat_type.in_(("group", "supergroup")),
                )
                .limit(MAX_BETA_RECIPIENTS)
            )
        ).all()
        deduped: dict[int, ChatDB] = {chat.id: chat for chat in (*selected, *granted)}
        return list(deduped.values())
