"""Session-bound beta entitlement persistence without transaction ownership."""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hovorunv2.application.beta import BetaEligibilityFacts
from hovorunv2.application.permissions import PermissionTarget
from hovorunv2.domain.beta import ALL_BETA, BetaEntitlementDB
from hovorunv2.domain.chat import ChatDB
from hovorunv2.domain.user import UserDB


class SQLAlchemyBetaEntitlementRepository:
    """Query active identities and retain revoked grant history in the caller's session."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def target(self, target: PermissionTarget) -> UserDB | ChatDB | None:
        """Require the exact selected lifecycle identity, including its current numeric routing ID."""
        if target.kind == "user":
            return await self._session.scalar(
                select(UserDB).where(
                    UserDB.id == target.entity_id,
                    UserDB.telegram_id == target.telegram_id,
                    UserDB.generation == target.generation,
                    UserDB.platform == "telegram",
                    UserDB.deleted_at.is_(None),
                )
            )
        if target.kind == "group":
            return await self._session.scalar(
                select(ChatDB).where(
                    ChatDB.id == target.entity_id,
                    ChatDB.chat_id == target.telegram_id,
                    ChatDB.generation == target.generation,
                    ChatDB.platform == "telegram",
                    ChatDB.chat_type.in_(("group", "supergroup")),
                    ChatDB.deleted_at.is_(None),
                )
            )
        return None

    async def facts(self, target: PermissionTarget, feature: str) -> BetaEligibilityFacts:
        """Read current effective entitlements and filter deleted lifecycle subjects."""
        entity = await self.target(target)
        if entity is None:
            return BetaEligibilityFacts(feature=feature, subject_active=False)
        subject = BetaEntitlementDB.user_id if isinstance(entity, UserDB) else BetaEntitlementDB.group_id
        grants = set(
            (
                await self._session.scalars(
                    select(BetaEntitlementDB.feature).where(
                        subject == entity.id,
                        BetaEntitlementDB.feature.in_((feature, ALL_BETA)),
                        BetaEntitlementDB.revoked_at.is_(None),
                        BetaEntitlementDB.deleted_at.is_(None),
                    )
                )
            ).all()
        )
        return BetaEligibilityFacts(
            feature=feature, subject_active=True, all_beta=ALL_BETA in grants, specific_feature=feature in grants
        )

    async def set_grant(self, entity: UserDB | ChatDB, feature: str, *, granted: bool) -> bool:
        """Apply a grant or preserve its revocation history; never alter feature selections."""
        subject = BetaEntitlementDB.user_id if isinstance(entity, UserDB) else BetaEntitlementDB.group_id
        current = await self._session.scalar(
            select(BetaEntitlementDB).where(
                subject == entity.id,
                BetaEntitlementDB.feature == feature,
                BetaEntitlementDB.revoked_at.is_(None),
                BetaEntitlementDB.deleted_at.is_(None),
            )
        )
        if granted:
            if current is not None:
                return False
            self._session.add(
                BetaEntitlementDB(
                    user_id=entity.id if isinstance(entity, UserDB) else None,
                    group_id=entity.id if isinstance(entity, ChatDB) else None,
                    feature=feature,
                )
            )
        else:
            if current is None:
                return False
            current.revoked_at = datetime.now(UTC)
            current.revision += 1
        await self._session.flush()
        return True
