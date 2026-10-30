"""Synthetic group beta test runtime and definitions."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from dishka import AsyncContainer

from hovorunv2.application.beta import BetaEntitlementStore
from hovorunv2.application.features import (
    FEATURE_REGISTRY,
    FeatureChannel,
    FeatureDefinition,
    FeatureRegistry,
    FeatureScope,
)
from hovorunv2.application.group_beta import GroupBetaStore
from hovorunv2.application.group_grants import GroupGrantStore
from hovorunv2.application.membership import AdministratorEvidence, MembershipEvidence, MembershipStatus
from hovorunv2.application.membership_store import MembershipStore
from hovorunv2.application.permissions import PermissionTarget
from hovorunv2.application.ports import ChatStore, UserStore
from hovorunv2.application.services.beta_service import BetaEligibilityService
from hovorunv2.application.services.chat_status_service import ChatStatusService
from hovorunv2.application.services.group_beta_service import GroupBetaService
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.beta import ALL_BETA
from hovorunv2.domain.enums import ChatStatus

GROUP = -100290001
FIRST = 290001
SECOND = 290002
MEMBER = 290003
BOT_ADMIN = 290010
BETA = FeatureDefinition("synthetic_group_beta", "Synthetic group beta", channel=FeatureChannel.BETA)
FUTURE = FeatureDefinition("later_group_beta", "Later group beta", channel=FeatureChannel.BETA, introduction="later")
PRIVATE = FeatureDefinition(
    "private_beta", "Private beta", channel=FeatureChannel.BETA, scopes=frozenset({FeatureScope.PRIVATE})
)
GROUP_ONLY = FeatureDefinition(
    "group_only_beta", "Group-only beta", channel=FeatureChannel.BETA, scopes=frozenset({FeatureScope.GROUP})
)
REGISTRY = FeatureRegistry((*FEATURE_REGISTRY.all(), BETA, FUTURE, PRIVATE, GROUP_ONLY))


@dataclass(frozen=True)
class GroupBetaRuntime:
    """Real services and exact lifecycle identities for one synthetic approved group."""

    service: GroupBetaService
    eligibility: BetaEligibilityService
    target: PermissionTarget
    first: PermissionTarget
    second: PermissionTarget


async def user_target(container: AsyncContainer, telegram_id: int) -> PermissionTarget:
    """Create a synthetic identity through the actual ingestion store."""
    users = await container.get(UserStore)
    await users.ingest_user(telegram_id)
    user = await users.get_by_telegram_id(telegram_id)
    assert user is not None
    return PermissionTarget("user", user.id, user.generation, user.telegram_id)


async def group_beta_runtime(container: AsyncContainer) -> GroupBetaRuntime:
    """Seed approval and fresh administrator evidence without internal service doubles."""
    chats = await container.get(ChatStore)
    chat = await chats.get_or_create_chat(GROUP)
    await chats.update_status(GROUP, ChatStatus.APPROVED)
    grants = await container.get(GroupGrantStore)
    await grants.approve(chat.id, ActorContext.cli())
    first = await user_target(container, FIRST)
    second = await user_target(container, SECOND)
    await user_target(container, MEMBER)
    target = PermissionTarget("group", chat.id, chat.generation, GROUP)
    now = datetime.now(UTC) - timedelta(minutes=1)
    memberships = await container.get(MembershipStore)
    members = tuple(
        MembershipEvidence(GROUP, user_id, MembershipStatus.ADMIN, now, now + timedelta(minutes=5))
        for user_id in (FIRST, SECOND)
    )
    await memberships.save_admins(
        chat.id, AdministratorEvidence(GROUP, True, members, now, now + timedelta(minutes=5)), ActorContext.cli()
    )
    service = GroupBetaService(await container.get(GroupBetaStore), REGISTRY, await container.get(ChatStatusService))
    eligibility = BetaEligibilityService(await container.get(BetaEntitlementStore), REGISTRY)
    await eligibility.set_grant(first, ALL_BETA, granted=True, actor=ActorContext.cli())
    await eligibility.set_grant(second, BETA.key, granted=True, actor=ActorContext.cli())
    return GroupBetaRuntime(service, eligibility, target, first, second)
