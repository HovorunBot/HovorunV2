"""Session-bound beta entitlement persistence without transaction ownership."""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hovorunv2.application.access.permissions import PermissionTarget
from hovorunv2.application.features.beta import BetaEligibilityFacts
from hovorunv2.domain.beta import ALL_BETA, BetaEntitlementDB
from hovorunv2.domain.chat import ChatDB
from hovorunv2.domain.user import UserDB
from hovorunv2.infrastructure.persistence.repositories.policy_queries import exact_target


class SQLAlchemyBetaEntitlementRepository:
    """Query active identities and retain revoked grant history in the caller's session."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def target(self, target: PermissionTarget) -> UserDB | ChatDB | None:
        """Require the exact current active lifecycle and canonical numeric route.

        Args:
            target: Permission target identifying kind, entity, generation and numeric route.

        Returns:
            Active user or chat matching the target, or None if absent or inactive.
        """
        statement = exact_target(target)
        return await self._session.scalar(statement) if statement is not None else None

    async def facts(self, target: PermissionTarget, feature: str) -> BetaEligibilityFacts:
        """Read current effective entitlements and filter deleted lifecycle subjects.

        Args:
            target: Permission target identifying kind, entity, generation and numeric route.
            feature: Feature key whose specific or all-beta entitlement is read.

        Returns:
            Eligibility facts reflecting subject activity and current grants.
        """
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
        """Apply a grant or preserve its revocation history without altering feature selections.

        Args:
            entity: Active user or chat receiving the entitlement change.
            feature: Feature key of the entitlement.
            granted: Whether to grant or revoke the entitlement.

        Returns:
            True if the entitlement changed, False if it was already in the requested state.
        """
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
