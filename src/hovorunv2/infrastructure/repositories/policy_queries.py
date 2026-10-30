"""Shared lifecycle-filtered statements for bot and native worker facts."""

from datetime import datetime

from sqlalchemy import Select, exists, or_, select
from sqlalchemy.orm import selectinload
from sqlalchemy.sql.selectable import Exists

from hovorunv2.application.membership import MembershipStatus
from hovorunv2.application.permissions import PermissionTarget
from hovorunv2.domain.ban import BanDB, BanKind
from hovorunv2.domain.beta import ALL_BETA, BetaEntitlementDB
from hovorunv2.domain.chat import ChatDB
from hovorunv2.domain.enums import TargetKind
from hovorunv2.domain.group_beta import GroupBetaEnablerDB, GroupBetaFeatureDB
from hovorunv2.domain.group_route import GroupRouteDB
from hovorunv2.domain.membership import GroupMembershipStateDB, MemberEvidenceDB
from hovorunv2.domain.user import UserDB


def active_user(telegram_id: int, platform: str = "telegram") -> Select[UserDB]:
    """Read role or trust only from an active numeric identity."""
    return select(UserDB).where(
        UserDB.platform == platform, UserDB.telegram_id == telegram_id, UserDB.deleted_at.is_(None)
    )


def active_chat(telegram_id: int, platform: str = "telegram") -> Select[ChatDB]:
    """Resolve retained aliases through their current active parent and explicitly load selections."""
    return (
        select(ChatDB)
        .where(
            or_(
                ChatDB.chat_id == telegram_id,
                ChatDB.id.in_(select(GroupRouteDB.group_id).where(GroupRouteDB.telegram_id == telegram_id)),
            ),
            ChatDB.platform == platform,
            ChatDB.deleted_at.is_(None),
        )
        .options(selectinload(ChatDB.commands))
    )


def fresh_admins(group_id: int, now: datetime) -> Exists:
    """Shared snapshot validity, including explicit invalidation after routing migration."""
    return exists().where(
        GroupMembershipStateDB.group_id == group_id,
        GroupMembershipStateDB.deleted_at.is_(None),
        GroupMembershipStateDB.admins_known.is_(True),
        GroupMembershipStateDB.admins_verified_at <= now,
        GroupMembershipStateDB.admins_expires_at > now,
    )


def current_member(group_id: int, user_id: int) -> Select[MemberEvidenceDB]:
    """Read undeleted evidence for the exact group and user generations."""
    return select(MemberEvidenceDB).where(
        MemberEvidenceDB.group_id == group_id,
        MemberEvidenceDB.user_id == user_id,
        MemberEvidenceDB.deleted_at.is_(None),
    )


def group_support(selection: GroupBetaFeatureDB, now: datetime) -> Select[int]:
    """Find actual current support without truncating candidates or copying eligibility."""
    eligible = exists().where(
        BetaEntitlementDB.user_id == UserDB.id,
        BetaEntitlementDB.feature.in_((selection.feature, ALL_BETA)),
        BetaEntitlementDB.revoked_at.is_(None),
        BetaEntitlementDB.deleted_at.is_(None),
    )
    return (
        select(GroupBetaEnablerDB.id)
        .join(UserDB, UserDB.id == GroupBetaEnablerDB.user_id)
        .join(
            MemberEvidenceDB,
            (MemberEvidenceDB.user_id == UserDB.id) & (MemberEvidenceDB.group_id == selection.group_id),
        )
        .where(
            GroupBetaEnablerDB.selection_id == selection.id,
            GroupBetaEnablerDB.revoked_at.is_(None),
            GroupBetaEnablerDB.deleted_at.is_(None),
            UserDB.deleted_at.is_(None),
            UserDB.platform == "telegram",
            MemberEvidenceDB.deleted_at.is_(None),
            MemberEvidenceDB.status == MembershipStatus.ADMIN,
            MemberEvidenceDB.needs_verification.is_(False),
            MemberEvidenceDB.verified_at <= now,
            MemberEvidenceDB.expires_at > now,
            fresh_admins(selection.group_id, now),
            eligible,
        )
        .limit(1)
    )


def banned_admin_queries(group_id: int, now: datetime) -> tuple[Select[int], Select[int]]:
    """Use complete current administrator candidates, not a bounded maintenance page."""
    candidates = (
        select(MemberEvidenceDB.id)
        .join(UserDB, UserDB.id == MemberEvidenceDB.user_id)
        .join(
            ChatDB,
            ChatDB.id == MemberEvidenceDB.group_id,
        )
        .join(BanDB, (BanDB.telegram_id == UserDB.telegram_id) & (BanDB.platform == UserDB.platform))
        .where(
            MemberEvidenceDB.group_id == group_id,
            MemberEvidenceDB.deleted_at.is_(None),
            UserDB.deleted_at.is_(None),
            ChatDB.deleted_at.is_(None),
            BanDB.target_kind == TargetKind.USER,
            BanDB.kind == BanKind.FULL,
            BanDB.deleted_at.is_(None),
            BanDB.revoked_at.is_(None),
            or_(MemberEvidenceDB.admin_candidate.is_(True), MemberEvidenceDB.needs_verification.is_(True)),
        )
    )
    return (
        candidates.where(
            MemberEvidenceDB.status == MembershipStatus.ADMIN,
            MemberEvidenceDB.verified_at <= now,
            MemberEvidenceDB.expires_at > now,
        ).limit(1),
        candidates.where(
            or_(
                MemberEvidenceDB.status == MembershipStatus.UNKNOWN,
                MemberEvidenceDB.verified_at > now,
                MemberEvidenceDB.expires_at <= now,
            )
        ).limit(1),
    )


def exact_target(target: PermissionTarget) -> Select[UserDB] | Select[ChatDB] | None:
    """Require the current canonical route, active lifecycle and exact generation."""
    if target.kind == "user":
        return active_user(target.telegram_id or 0).where(
            UserDB.id == target.entity_id,
            UserDB.generation == target.generation,
        )
    if target.kind == "group":
        return active_chat(target.telegram_id or 0).where(
            ChatDB.id == target.entity_id,
            ChatDB.generation == target.generation,
            ChatDB.chat_id == target.telegram_id,
            ChatDB.chat_type.in_(("group", "supergroup")),
        )
    return None
