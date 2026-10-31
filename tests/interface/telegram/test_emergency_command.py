"""Private bot-administrator feature emergency controls through real Telegram transport."""

from aiogram import Bot
from aiogram.types import Message
from aioresponses import aioresponses
from dishka import AsyncContainer

from hovorunv2.application.features.emergency_service import FeatureEmergencyService
from hovorunv2.application.identity.ports import UserStore
from hovorunv2.interface.telegram.handlers.emergency import EmergencyCommand
from tests.conftest import provision_role
from tests.utils.activation import sent_fields
from tests.utils.settings import settings_http, settings_message
from tests.utils.telegram import BOT_CREDENTIAL
from tests.utils.usage import USER

ADMIN = 510001
GROUP = -510002
PROVIDER = "twitter"


def _message(text: str, *, chat_id: int, actor_id: int) -> Message:
    return settings_message(chat_id=chat_id, actor_id=actor_id).model_copy(update={"text": text})


async def test_private_administrator_can_disable_and_re_enable(test_container: AsyncContainer) -> None:
    """A current private bot administrator applies audited transitions with a public reason."""
    await provision_role(test_container, ADMIN)
    command = await test_container.get(EmergencyCommand)
    service = await test_container.get(FeatureEmergencyService)
    disable = _message(
        f'/emergency {PROVIDER} disable --reason "synthetic outage" --public "Temporary provider outage"',
        chat_id=ADMIN,
        actor_id=ADMIN,
    )
    async with Bot(BOT_CREDENTIAL) as bot:
        with aioresponses() as http:
            settings_http(http, disable)
            await command.handle(disable.as_(bot), bot)
            text = sent_fields(http, "sendMessage")[-1]["text"]
    assert "disabled" in text
    assert "epoch 1" in text
    assert "Temporary provider outage" in text
    assert (await service.state(PROVIDER)).disabled
    enable = _message(f'/emergency {PROVIDER} enable --reason "synthetic recovery"', chat_id=ADMIN, actor_id=ADMIN)
    async with Bot(BOT_CREDENTIAL) as bot:
        with aioresponses() as http:
            settings_http(http, enable)
            await command.handle(enable.as_(bot), bot)
    state = await service.state(PROVIDER)
    assert not state.disabled
    assert state.epoch == 2


async def test_group_and_ordinary_callers_cannot_toggle_global_state(test_container: AsyncContainer) -> None:
    """Non-private transport and non-administrators never change durable emergency state."""
    await provision_role(test_container, ADMIN)
    await (await test_container.get(UserStore)).ingest_user(USER)
    command = await test_container.get(EmergencyCommand)
    service = await test_container.get(FeatureEmergencyService)
    for chat_id, actor_id in ((GROUP, ADMIN), (USER, USER)):
        message = _message(
            f'/emergency {PROVIDER} disable --reason "synthetic outage"', chat_id=chat_id, actor_id=actor_id
        )
        async with Bot(BOT_CREDENTIAL) as bot:
            with aioresponses() as http:
                settings_http(http, message)
                await command.handle(message.as_(bot), bot)
        assert not (await service.state(PROVIDER)).disabled


async def test_missing_internal_reason_fails_before_state_change(test_container: AsyncContainer) -> None:
    """Parsing rejects an absent reason without touching durable state."""
    await provision_role(test_container, ADMIN)
    command = await test_container.get(EmergencyCommand)
    service = await test_container.get(FeatureEmergencyService)
    message = _message(f"/emergency {PROVIDER} disable", chat_id=ADMIN, actor_id=ADMIN)
    async with Bot(BOT_CREDENTIAL) as bot:
        with aioresponses() as http:
            settings_http(http, message)
            await command.handle(message.as_(bot), bot)
            text = sent_fields(http, "sendMessage")[-1]["text"]
    assert "unavailable" in text.lower()
    assert not (await service.state(PROVIDER)).disabled
