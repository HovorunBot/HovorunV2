"""Beta introduction announcements and durable decisions against real PostgreSQL."""

import pytest
from dishka import AsyncContainer

from hovorunv2.application.access.ban_ports import BanStore
from hovorunv2.application.access.permission_service import PermissionService
from hovorunv2.application.access.permissions import PermissionTarget
from hovorunv2.application.features.beta import BetaEntitlementStore
from hovorunv2.application.features.beta_prompts import BetaDecision, BetaPromptService, BetaPromptStore
from hovorunv2.application.features.beta_service import BetaEligibilityService
from hovorunv2.application.features.group_beta import GroupBetaStore
from hovorunv2.application.features.group_beta_service import GroupBetaService
from hovorunv2.application.features.ports import CommandStore
from hovorunv2.application.features.registry import FeatureChannel, FeatureDefinition, FeatureRegistry
from hovorunv2.application.groups.chat_status_service import ChatStatusService
from hovorunv2.application.groups.group_management_service import GroupManagementService
from hovorunv2.application.groups.ports import ChatStore
from hovorunv2.application.identity.ports import UserStore
from hovorunv2.application.jobs.feature_jobs import PayloadPort
from hovorunv2.application.operations.notification_ledger import NotificationLedgerService
from hovorunv2.application.usage.budgets import Clock
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.beta import ALL_BETA
from hovorunv2.domain.enums import CommandName

USER = 580001
OTHER = 580002
FEATURE = str(CommandName.TWITTER_VIDEO)
OTHER_FEATURE = str(CommandName.TWITTER_POST)


async def _service(container: AsyncContainer) -> tuple[BetaPromptService, BetaEligibilityService]:
    registry = FeatureRegistry(
        [
            FeatureDefinition(key=FEATURE, display_name="Beta video", channel=FeatureChannel.BETA),
            FeatureDefinition(key=OTHER_FEATURE, display_name="Beta post", channel=FeatureChannel.BETA),
        ]
    )
    beta = BetaEligibilityService(await container.get(BetaEntitlementStore), registry)
    group_beta = GroupBetaService(await container.get(GroupBetaStore), registry, await container.get(ChatStatusService))
    service = BetaPromptService(
        await container.get(BetaPromptStore),
        registry,
        beta,
        group_beta,
        await container.get(CommandStore),
        await container.get(GroupManagementService),
        await container.get(PermissionService),
        await container.get(BanStore),
        await container.get(NotificationLedgerService),
        await container.get(PayloadPort),
        await container.get(Clock),
    )
    return service, beta


async def _user_target(container: AsyncContainer, telegram_id: int) -> PermissionTarget:
    users = await container.get(UserStore)
    await users.ingest_user(telegram_id)
    await users.set_trust(telegram_id, is_trusted=True, actor=ActorContext.cli())
    await (await container.get(ChatStore)).get_or_create_chat(telegram_id)
    user = await users.get_by_telegram_id(telegram_id)
    assert user is not None
    return PermissionTarget("user", user.id, user.generation, telegram_id)


async def test_announcement_is_one_time_and_activation_is_durable(
    test_container: AsyncContainer,
) -> None:
    """One introduction event is created; Activate persists and enables exactly that feature."""
    service, beta = await _service(test_container)
    target = await _user_target(test_container, USER)
    assert await beta.set_grant(target, FEATURE, granted=True, actor=ActorContext.cli())
    assert await service.announce(FEATURE) == 1
    assert await service.announce(FEATURE) == 0
    assert await service.decide(
        target,
        FEATURE,
        BetaDecision.ACTIVE,
        actor=ActorContext.telegram(USER),
        transport_kind="private",
    )
    store = await test_container.get(BetaPromptStore)
    assert await store.decision(target, FEATURE) == BetaDecision.ACTIVE
    commands = await test_container.get(CommandStore)
    assert FEATURE in await commands.get_allowed_commands(USER)


async def test_decline_is_durable_and_unannounced_feature_is_rejected(
    test_container: AsyncContainer,
) -> None:
    """Declining persists without enabling; an unannounced feature cannot be decided."""
    service, beta = await _service(test_container)
    target = await _user_target(test_container, USER)
    await beta.set_grant(target, FEATURE, granted=True, actor=ActorContext.cli())
    await beta.set_grant(target, OTHER_FEATURE, granted=True, actor=ActorContext.cli())
    await service.announce(FEATURE)
    assert await service.decide(
        target,
        FEATURE,
        BetaDecision.DECLINED,
        actor=ActorContext.telegram(USER),
        transport_kind="private",
    )
    store = await test_container.get(BetaPromptStore)
    assert await store.decision(target, FEATURE) == BetaDecision.DECLINED
    commands = await test_container.get(CommandStore)
    assert FEATURE not in await commands.get_allowed_commands(USER)
    with pytest.raises(ValueError, match="not announced"):
        await service.decide(
            target,
            OTHER_FEATURE,
            BetaDecision.ACTIVE,
            actor=ActorContext.telegram(USER),
            transport_kind="private",
        )


async def test_cross_recipient_replay_cannot_activate(test_container: AsyncContainer) -> None:
    """Another user cannot act on a prompt announced to a different recipient."""
    service, beta = await _service(test_container)
    owner = await _user_target(test_container, USER)
    await _user_target(test_container, OTHER)
    await beta.set_grant(owner, FEATURE, granted=True, actor=ActorContext.cli())
    await service.announce(FEATURE)
    with pytest.raises(PermissionError):
        await service.decide(
            owner,
            FEATURE,
            BetaDecision.ACTIVE,
            actor=ActorContext.telegram(OTHER),
            transport_kind="private",
        )


async def test_all_beta_eligibility_announces_without_auto_activation(
    test_container: AsyncContainer,
) -> None:
    """A future beta feature is included in all-beta eligibility but only an explicit choice enables it."""
    service, beta = await _service(test_container)
    target = await _user_target(test_container, USER)
    assert await beta.set_grant(target, ALL_BETA, granted=True, actor=ActorContext.cli())
    assert await service.announce(FEATURE) == 1
    commands = await test_container.get(CommandStore)
    assert FEATURE not in await commands.get_allowed_commands(USER)
    assert await service.decide(
        target,
        FEATURE,
        BetaDecision.ACTIVE,
        actor=ActorContext.telegram(USER),
        transport_kind="private",
    )
    assert FEATURE in await commands.get_allowed_commands(USER)
