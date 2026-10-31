"""Session-bound group beta state and current supporting administrator queries."""

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from hovorunv2.application.access.permissions import PermissionTarget
from hovorunv2.application.features.group_beta import GroupBetaFacts
from hovorunv2.application.identity.membership import MembershipStatus
from hovorunv2.domain.beta import ALL_BETA, BetaEntitlementDB
from hovorunv2.domain.chat import ChatDB
from hovorunv2.domain.group_beta import GroupBetaEnablerDB, GroupBetaFeatureDB
from hovorunv2.domain.membership import MemberEvidenceDB
from hovorunv2.domain.user import UserDB
from hovorunv2.infrastructure.repositories.beta_repository import SQLAlchemyBetaEntitlementRepository
from hovorunv2.infrastructure.repositories.policy_queries import fresh_admins, group_support


@dataclass(frozen=True, slots=True)
class GroupBetaMutation:
    """Carry actual changes to the caller's shared audit transaction."""

    changed: bool
    changes: dict[str, object] = field(default_factory=dict)


class SQLAlchemyGroupBetaRepository:
    """Keep selections durable while reading current grant, identity and membership facts."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def selection(self, group_id: int, feature: str) -> GroupBetaFeatureDB | None:
        """Read only the current nondeleted selection, including an intentional off state."""
        return await self._session.scalar(
            select(GroupBetaFeatureDB).where(
                GroupBetaFeatureDB.group_id == group_id,
                GroupBetaFeatureDB.feature == feature,
                GroupBetaFeatureDB.deleted_at.is_(None),
            )
        )

    async def group_granted(self, group_id: int, feature: str) -> bool:
        """Require an explicit current feature grant; user wildcard grants never substitute."""
        return bool(
            await self._session.scalar(
                select(
                    exists().where(
                        BetaEntitlementDB.group_id == group_id,
                        BetaEntitlementDB.feature == feature,
                        BetaEntitlementDB.revoked_at.is_(None),
                        BetaEntitlementDB.deleted_at.is_(None),
                    )
                )
            )
        )

    async def is_admin(self, group_id: int, user_id: int, now: datetime) -> bool:
        """Require current known administrator evidence for exactly this group and user."""
        return bool(
            await self._session.scalar(
                select(
                    exists().where(
                        MemberEvidenceDB.group_id == group_id,
                        MemberEvidenceDB.user_id == user_id,
                        MemberEvidenceDB.deleted_at.is_(None),
                        MemberEvidenceDB.status == MembershipStatus.ADMIN,
                        MemberEvidenceDB.needs_verification.is_(False),
                        MemberEvidenceDB.verified_at <= now,
                        MemberEvidenceDB.expires_at > now,
                        fresh_admins(group_id, now),
                    )
                )
            )
        )

    async def user_eligible(self, user_id: int, feature: str) -> bool:
        """Read a specific or all-beta user grant without treating trust as eligibility."""
        return bool(
            await self._session.scalar(
                select(
                    exists().where(
                        BetaEntitlementDB.user_id == user_id,
                        BetaEntitlementDB.feature.in_((feature, ALL_BETA)),
                        BetaEntitlementDB.revoked_at.is_(None),
                        BetaEntitlementDB.deleted_at.is_(None),
                    )
                )
            )
        )

    async def _has_support(self, selection: GroupBetaFeatureDB, now: datetime) -> bool:
        return await self._session.scalar(group_support(selection, now)) is not None

    async def facts(self, target: PermissionTarget, feature: str, now: datetime) -> GroupBetaFacts:
        """Read current support without caching a prior eligibility or membership result."""
        entity = await SQLAlchemyBetaEntitlementRepository(self._session).target(target)
        if not isinstance(entity, ChatDB):
            return GroupBetaFacts(feature=feature, subject_active=False)
        selection = await self.selection(entity.id, feature)
        return GroupBetaFacts(
            feature=feature,
            subject_active=True,
            selected=bool(selection and selection.selected),
            group_granted=await self.group_granted(entity.id, feature),
            qualifying_enabler=selection is not None and await self._has_support(selection, now),
        )

    async def set_enabled(
        self, group: ChatDB, feature: str, *, enabled: bool, enabler: UserDB | None, now: datetime
    ) -> GroupBetaMutation:
        """Retain selected state and revoke supporting intent on an explicit global off decision."""
        selection = await self.selection(group.id, feature)
        if selection is None:
            if not enabled:
                return GroupBetaMutation(changed=False)
            selection = GroupBetaFeatureDB(group_id=group.id, feature=feature, selected=False)
            self._session.add(selection)
            await self._session.flush()
        prior = selection.selected
        selection.selected = enabled
        changes: dict[str, object] = {"feature": feature, "generation": group.generation, "selected": [prior, enabled]}
        changed = prior != enabled
        if enabled and enabler is not None and await self._add_enabler(selection, enabler, now):
            changes.update(enabler_user_id=enabler.id, enabler_added=True)
            changed = True
        if not enabled:
            revoked = await self._revoke_enablers(selection, now)
            if revoked:
                changes["enablers_revoked"] = revoked
                changed = True
        return GroupBetaMutation(changed, changes if changed else {})

    async def _add_enabler(self, selection: GroupBetaFeatureDB, enabler: UserDB, now: datetime) -> bool:
        """Add a supporting administrator only when their active intent is absent."""
        support = await self._session.scalar(
            select(GroupBetaEnablerDB).where(
                GroupBetaEnablerDB.selection_id == selection.id,
                GroupBetaEnablerDB.user_id == enabler.id,
                GroupBetaEnablerDB.revoked_at.is_(None),
                GroupBetaEnablerDB.deleted_at.is_(None),
            )
        )
        if support is not None:
            return False
        self._session.add(GroupBetaEnablerDB(selection_id=selection.id, user_id=enabler.id, created_at=now))
        return True

    async def _revoke_enablers(self, selection: GroupBetaFeatureDB, now: datetime) -> int:
        """Revoke all active supporting intent after an explicit off decision."""
        supports = list(
            (
                await self._session.scalars(
                    select(GroupBetaEnablerDB).where(
                        GroupBetaEnablerDB.selection_id == selection.id,
                        GroupBetaEnablerDB.revoked_at.is_(None),
                        GroupBetaEnablerDB.deleted_at.is_(None),
                    )
                )
            ).all()
        )
        for support in supports:
            support.revoked_at = now
        return len(supports)
