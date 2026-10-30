"""Shared discovery and execution regressions with real SQLite and Telegram HTTP."""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from functools import partial

import pytest
from aiogram import Bot
from aiogram.types import CallbackQuery, Chat, Message, TelegramObject, User
from aioresponses import CallbackResult
from dishka import AsyncContainer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from hovorunv2.application.ban_ports import BanStore
from hovorunv2.application.clients.twitter import TwitterService
from hovorunv2.application.features import (
    FeatureChannel,
    FeatureDefinition,
    FeatureRegistry,
    TriggerKind,
)
from hovorunv2.application.group_beta import GroupBetaStore
from hovorunv2.application.media.extractor import MediaExtractor
from hovorunv2.application.media.pipeline import MediaPipeline
from hovorunv2.application.membership import BotCapabilityEvidence, MembershipEvidence, MembershipStatus
from hovorunv2.application.membership_store import MembershipStore
from hovorunv2.application.policy_facts import FeatureFactStore
from hovorunv2.application.ports import CachePort, ChatStore, CommandStore, UserStore
from hovorunv2.application.services.access_service import AccessService, CommandPolicy
from hovorunv2.application.services.chat_status_service import ChatStatusService
from hovorunv2.application.services.feature_access_service import FeatureAccessService
from hovorunv2.application.services.permission_service import PermissionService
from hovorunv2.application.services.translation_service import TranslationService
from hovorunv2.application.services.trust_service import TrustService
from hovorunv2.domain.audit import ActorContext, AuditEventDB
from hovorunv2.domain.ban import BanKind
from hovorunv2.domain.beta import ALL_BETA
from hovorunv2.domain.enums import CommandName, TargetKind
from hovorunv2.infrastructure.config import Settings
from hovorunv2.interface.telegram.callbacks import SettingsAction, SettingsCallback, SettingsMenu
from hovorunv2.interface.telegram.handlers.help import HelpCommand
from hovorunv2.interface.telegram.handlers.settings import SettingsCommand
from hovorunv2.interface.telegram.middlewares import AccessMiddleware
from tests.test_group_beta import BETA, FIRST, FUTURE, GROUP, MEMBER, REGISTRY, group_beta_runtime
from tests.test_membership_adapter import BOT_CREDENTIAL
from tests.utils.activation import sent_fields, stub_telegram
from tests.utils.recorded_responses import RecordedResponses
from tests.utils.settings import bind_settings_callback

CAPABLE = FeatureDefinition(
    "capability_feature", "Capability feature", required_bot_capabilities=frozenset({"can_pin_messages"})
)


async def feature_runtime(container: AsyncContainer, registry: FeatureRegistry = REGISTRY) -> FeatureAccessService:
    """Compose the production evaluator with real stores and a test-only feature registry."""
    await group_beta_runtime(container)
    service = FeatureAccessService(
        registry,
        await container.get(UserStore),
        await container.get(ChatStore),
        await container.get(BanStore),
        await container.get(MembershipStore),
        await container.get(PermissionService),
        await container.get(ChatStatusService),
        await container.get(GroupBetaStore),
        await container.get(FeatureFactStore),
    )
    access = await container.get(AccessService)
    access.features = service
    return service


def message(actor: int = MEMBER, text: str = "/synthetic_group_beta") -> Message:
    """Provide genuine human transport facts independently of callback values."""
    return Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(id=GROUP, type="supergroup"),
        from_user=User(id=actor, is_bot=False, first_name="Synthetic"),
        text=text,
    )


async def handled(event: TelegramObject, data: dict[str, object]) -> str:
    """Observe that the real middleware admitted the downstream operation."""
    return "handled"


@pytest.mark.parametrize("trigger", [TriggerKind.DIRECT, TriggerKind.AUTOMATIC])
async def test_group_beta_requires_selection_then_serves_ordinary_members(
    test_container: AsyncContainer, trigger: TriggerKind
) -> None:
    """Individual grants never replace group selection or current qualifying support."""
    service = await feature_runtime(test_container)
    decision = await service.evaluate(BETA.key, GROUP, MEMBER, trigger=trigger)
    assert not decision.visible
    assert not decision.allowed
    assert (await service.evaluate(BETA.key, GROUP, FIRST, configure=True)).visible
    await service.set_group_beta(BETA.key, GROUP, FIRST, enabled=True)
    assert (await service.evaluate(BETA.key, GROUP, MEMBER, trigger=trigger)).allowed
    assert not (await service.evaluate(FUTURE.key, GROUP, MEMBER, trigger=trigger)).allowed
    assert not (await service.evaluate("guessed_unknown", GROUP, FIRST, configure=True)).visible
    runtime = await group_beta_runtime(test_container)
    await runtime.eligibility.set_grant(runtime.first, ALL_BETA, granted=False, actor=ActorContext.cli())
    suspended = await service.evaluate(BETA.key, GROUP, MEMBER, trigger=trigger)
    assert not suspended.visible
    assert not suspended.allowed
    facts = await runtime.service.facts(runtime.target, BETA.key)
    assert facts.selected


@pytest.mark.parametrize("callback", [False, True])
async def test_guessed_execution_and_stale_callbacks_recheck_current_group_support(
    test_container: AsyncContainer, callback: bool
) -> None:
    """The same middleware denies guessed actions and admits a supported selected group feature."""
    service = await feature_runtime(test_container)
    access = await test_container.get(AccessService)
    middleware = AccessMiddleware(access, await test_container.get(CachePort), await test_container.get(Settings))
    bot = Bot(BOT_CREDENTIAL)
    event = message().as_(bot)
    if callback:
        event = CallbackQuery(id="synthetic", from_user=event.from_user, chat_instance="synthetic", message=event).as_(
            bot
        )
    data = {"policy": CommandPolicy(is_toggleable=True), "command_name": BETA.key}
    try:
        with RecordedResponses() as http:
            stub_telegram(http)
            assert await middleware(handled, event, data) is None
            await service.set_group_beta(BETA.key, GROUP, FIRST, enabled=True)
            assert await middleware(handled, event, data) == "handled"
            await service.set_group_beta(BETA.key, GROUP, FIRST, enabled=False)
            assert await middleware(handled, event, data) is None
    finally:
        await bot.session.close()


async def test_help_and_modules_hide_future_beta_and_guessed_toggle_writes_nothing(
    test_container: AsyncContainer,
) -> None:
    """Render actual Telegram forms and verify rejected callback leaves selection/audit unchanged."""
    service = await feature_runtime(test_container)
    command = await test_container.get(SettingsCommand)
    help_command = await test_container.get(HelpCommand)
    bot = Bot(BOT_CREDENTIAL)
    menu = message(FIRST).as_(bot)
    query = CallbackQuery(id="settings", from_user=menu.from_user, chat_instance="synthetic", message=menu).as_(bot)
    sessions = await test_container.get(async_sessionmaker)
    async with sessions() as session:
        before = len((await session.scalars(select(AuditEventDB))).all())
    try:
        with RecordedResponses() as http:
            stub_telegram(http)
            callback = await bind_settings_callback(
                test_container, menu, FIRST, SettingsCallback(menu=SettingsMenu.MODULES)
            )
            await command.handle_callback(query, callback)
            markup = json.loads(sent_fields(http, "editMessageText")[-1]["reply_markup"])
            callbacks = [button["callback_data"] for row in markup["inline_keyboard"] for button in row]
            assert any(BETA.key in value for value in callbacks)
            # FIRST has all-beta, so future definitions remain configurable but unselected.
            await help_command.handle(message(MEMBER).as_(bot), bot)
            text = sent_fields(http, "sendMessage")[-1]["text"]
            assert BETA.display_name not in text
            assert FUTURE.display_name not in text
            assert "/allow_chat" in text
            assert "/feedback" in text
            await command.handle_callback(
                query,
                SettingsCallback(menu=SettingsMenu.MODULES, action=SettingsAction.TOGGLE, value="guessed_unknown"),
            )
            assert sent_fields(http, "answerCallbackQuery")[-1]["text"] == "Settings unavailable. Reopen /settings."
        async with sessions() as session:
            assert len((await session.scalars(select(AuditEventDB))).all()) == before
        await service.set_group_beta(BETA.key, GROUP, FIRST, enabled=True)
        with RecordedResponses() as http:
            stub_telegram(http)
            await help_command.handle(message(MEMBER).as_(bot), bot)
            assert BETA.display_name in sent_fields(http, "sendMessage")[-1]["text"]
    finally:
        await bot.session.close()


async def test_capability_guidance_only_for_current_managers(test_container: AsyncContainer) -> None:
    """Unknown, expired and missing bot rights deny execution without exposing guidance to ordinary users."""
    service = await feature_runtime(test_container, FeatureRegistry((*REGISTRY.all(), CAPABLE)))
    manager = await service.evaluate(CAPABLE.key, GROUP, FIRST, configure=True)
    member = await service.evaluate(CAPABLE.key, GROUP, MEMBER)
    assert manager.visible
    assert not manager.allowed
    assert manager.guidance
    assert not member.allowed
    assert member.guidance is None
    memberships = await test_container.get(MembershipStore)
    chats = await test_container.get(ChatStore)
    chat = await chats.get_chat(GROUP)
    assert chat is not None
    now = datetime.now(UTC) - timedelta(seconds=1)
    evidence = MembershipEvidence(GROUP, 123456, MembershipStatus.ADMIN, now, now + timedelta(minutes=5))
    await memberships.save_bot(chat.id, BotCapabilityEvidence(evidence, False, False, False, True, False))
    assert (await service.evaluate(CAPABLE.key, GROUP, FIRST, configure=True)).allowed
    assert not (await service.evaluate(CAPABLE.key, GROUP, MEMBER)).allowed  # Still not selected.


async def test_private_beta_needs_access_grant_and_explicit_selection(test_container: AsyncContainer) -> None:
    """Trust, bot authority and all-beta eligibility each remain separate from selection."""
    key = str(CommandName.TWITTER_POST)
    registry = FeatureRegistry(
        replace(definition, channel=FeatureChannel.BETA, default_enabled=False) if definition.key == key else definition
        for definition in REGISTRY.all()
    )
    service = await feature_runtime(test_container, registry)
    users = await test_container.get(UserStore)
    trust = await test_container.get(TrustService)
    await trust.set_trust(str(FIRST), is_trusted=True, actor=ActorContext.cli())
    chats = await test_container.get(ChatStore)
    await chats.get_or_create_chat(FIRST)
    assert not (await service.evaluate(key, FIRST, FIRST)).allowed
    assert (await service.evaluate(key, FIRST, FIRST, configure=True)).visible
    commands = await test_container.get(CommandStore)
    await commands.enable_command(FIRST, key)
    assert (await service.evaluate(key, FIRST, FIRST)).allowed
    assert not (await service.evaluate(key, FIRST, MEMBER)).allowed
    access = await test_container.get(AccessService)
    middleware = AccessMiddleware(access, await test_container.get(CachePort), await test_container.get(Settings))
    private_message = Message(
        message_id=2,
        date=datetime.now(UTC),
        chat=Chat(id=FIRST, type="private"),
        from_user=User(id=FIRST, is_bot=False, first_name="Synthetic"),
        text="/guessed_unknown",
    )
    assert (
        await middleware(handled, private_message, {"policy": CommandPolicy(), "command_name": "guessed_unknown"})
        is None
    )
    await trust.set_trust(str(FIRST), is_trusted=False, actor=ActorContext.cli())
    assert not (await service.evaluate(key, FIRST, FIRST)).visible
    user = await users.get_by_telegram_id(FIRST)
    assert user is not None


async def revoke_support_response(
    url: object, *, service: FeatureAccessService, key: str, **kwargs: object
) -> CallbackResult:
    """Model an entitlement/selection change while the external extraction request is running."""
    await service.set_group_beta(key, GROUP, FIRST, enabled=False)
    return CallbackResult(payload={"text": "synthetic post", "media_extended": []})


async def test_pipeline_denies_before_network_and_rechecks_after_extraction(test_container: AsyncContainer) -> None:
    """Real extractor admission and post-extraction revalidation use the same scoped feature policy."""
    keys = {str(CommandName.TWITTER_VIDEO), str(CommandName.TWITTER_POST)}
    registry = FeatureRegistry(
        replace(definition, channel=FeatureChannel.BETA, default_enabled=False)
        if definition.key in keys
        else definition
        for definition in REGISTRY.all()
    )
    service = await feature_runtime(test_container, registry)
    pipeline = MediaPipeline(
        await test_container.get(MediaExtractor), await test_container.get(TranslationService), service
    )
    client = await test_container.get(TwitterService)
    with RecordedResponses() as http:
        result = await pipeline.process(
            client, "https://x.com/synthetic/status/123", GROUP, actor_id=MEMBER, trigger=TriggerKind.AUTOMATIC
        )
        assert result is None
        assert not http.requests
        await service.set_group_beta(str(CommandName.TWITTER_POST), GROUP, FIRST, enabled=True)
        http.get(
            "https://api.vxtwitter.com/i/status/123",
            callback=partial(revoke_support_response, service=service, key=str(CommandName.TWITTER_POST)),
        )
        result = await pipeline.process(
            client, "https://x.com/synthetic/status/123", GROUP, actor_id=MEMBER, trigger=TriggerKind.AUTOMATIC
        )
        assert result is None
        assert len(http.requests) == 1


async def test_personal_ban_denies_direct_but_not_group_automatic_work(test_container: AsyncContainer) -> None:
    """Group beta support remains group-owned while explicit actions retain actor restrictions."""
    service = await feature_runtime(test_container)
    await service.set_group_beta(BETA.key, GROUP, FIRST, enabled=True)
    bans = await test_container.get(BanStore)
    await bans.set_ban(
        TargetKind.USER, MEMBER, BanKind.PERSONAL, active=True, actor=ActorContext.cli(reason="synthetic ban")
    )
    assert not (await service.evaluate(BETA.key, GROUP, MEMBER, trigger=TriggerKind.DIRECT)).allowed
    assert (await service.evaluate(BETA.key, GROUP, MEMBER, trigger=TriggerKind.AUTOMATIC)).allowed
    await bans.set_ban(
        TargetKind.GROUP, GROUP, BanKind.FULL, active=True, actor=ActorContext.cli(reason="synthetic ban")
    )
    assert not (await service.evaluate(BETA.key, GROUP, MEMBER, trigger=TriggerKind.AUTOMATIC)).allowed
