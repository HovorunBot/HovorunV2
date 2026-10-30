"""Group settings controls and guidance through real services and Telegram HTTP."""

import json

import pytest
from aiogram import Bot
from dishka import AsyncContainer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from hovorunv2.application.features import FeatureRegistry
from hovorunv2.application.services.trust_service import TrustService
from hovorunv2.domain.audit import ActorContext, AuditEventDB
from hovorunv2.interface.telegram.callbacks import SettingsMenu
from hovorunv2.interface.telegram.handlers.settings import SettingsCommand
from tests.conftest import provision_role
from tests.utils.activation import sent_fields
from tests.utils.recorded_responses import RecordedResponses
from tests.utils.runtimes.beta import BETA, BOT_ADMIN, FUTURE, GROUP, MEMBER, REGISTRY, SECOND
from tests.utils.runtimes.features import CAPABLE, feature_runtime
from tests.utils.settings import button, query_for, settings_http, settings_message
from tests.utils.telegram import BOT_CREDENTIAL


@pytest.mark.parametrize("actor", [SECOND, BOT_ADMIN])
async def test_group_settings_retain_controls_and_explain_permissions(
    test_container: AsyncContainer, actor: int
) -> None:
    """Current local and bot admins see only group controls and safe operational guidance."""
    service = await feature_runtime(test_container, FeatureRegistry((*REGISTRY.all(), CAPABLE)))
    if actor == BOT_ADMIN:
        await provision_role(test_container, actor)
    command = await test_container.get(SettingsCommand)
    message = settings_message(actor_id=actor)
    async with Bot(BOT_CREDENTIAL) as bot:
        with RecordedResponses() as http:
            settings_http(http, message)
            await command.handle(message.as_(bot), bot)
            markup = json.loads(sent_fields(http, "editMessageText")[-1]["reply_markup"])
            labels = [item["text"] for row in markup["inline_keyboard"] for item in row]
            assert "🌐 Languages" in labels
            assert "⚙️ Modules" in labels
            assert not any(word in label.lower() for word in ("trust", "ban", "role") for label in labels)
            await command.handle_callback(query_for(message, bot, actor), button(http, SettingsMenu.ACCESS))
            text = sent_fields(http, "editMessageText")[-1]["text"]
            assert "Group processing is available" in text
            assert "verification is unavailable or expired" in text
            assert "Bot permissions are missing" in text
            assert not (await service.evaluate(CAPABLE.key, GROUP, actor)).allowed


@pytest.mark.slow
async def test_group_beta_requires_explicit_rendered_activation(test_container: AsyncContainer) -> None:
    """Opening Modules changes nothing; one eligible click creates audited group selection."""
    service = await feature_runtime(test_container)
    command = await test_container.get(SettingsCommand)
    message = settings_message(actor_id=SECOND)
    assert not (await service.evaluate(BETA.key, GROUP, MEMBER)).allowed
    async with Bot(BOT_CREDENTIAL) as bot:
        with RecordedResponses() as http:
            settings_http(http, message)
            await command.handle(message.as_(bot), bot)
            query = query_for(message, bot, SECOND)
            await command.handle_callback(query, button(http, SettingsMenu.MODULES))
            markup = json.loads(sent_fields(http, "editMessageText")[-1]["reply_markup"])
            labels = [item["text"] for row in markup["inline_keyboard"] for item in row]
            assert any(BETA.display_name in label for label in labels)
            assert not any(FUTURE.display_name in label for label in labels)
            assert not (await service.evaluate(BETA.key, GROUP, MEMBER)).allowed
            await command.handle_callback(query, button(http, SettingsMenu.MODULES, value=BETA.key))
            assert (await service.evaluate(BETA.key, GROUP, MEMBER)).allowed
    sessions = await test_container.get(async_sessionmaker)
    async with sessions() as session:
        events = (await session.scalars(select(AuditEventDB).where(AuditEventDB.target_type == "group_beta"))).all()
        assert len(events) == 1
        assert events[0].actor_id == SECOND


async def test_trust_does_not_authorize_group_configuration(test_container: AsyncContainer) -> None:
    """An ordinary trusted member cannot open the group management window."""
    await feature_runtime(test_container)
    trust = await test_container.get(TrustService)
    await trust.set_trust(str(MEMBER), is_trusted=True, actor=ActorContext.cli())
    command = await test_container.get(SettingsCommand)
    message = settings_message(actor_id=MEMBER)
    async with Bot(BOT_CREDENTIAL) as bot:
        with RecordedResponses() as http:
            settings_http(http, message, admin=False)
            await command.handle(message.as_(bot), bot)
            assert len(sent_fields(http, "getChatMember")) == 1
            assert not sent_fields(http, "sendMessage")
