"""Behavioral tests for localized HTML escaping and unauthorized chat prompts."""

from aiogram.methods import SendMessage
from dishka import AsyncContainer
from sqlalchemy.ext.asyncio import async_sessionmaker

from hovorunv2.application.access.access_service import AccessService
from hovorunv2.application.configuration import AppSettings
from hovorunv2.application.groups.ports import ChatStore
from hovorunv2.domain.chat import ChatDB
from hovorunv2.domain.enums import ChatStatus, Platform
from hovorunv2.infrastructure.config import Settings
from hovorunv2.interface.telegram.handlers.help import HelpCommand
from hovorunv2.interface.telegram.settings.prompts import (
    TelegramUnauthorizedPromptSender,
    build_unauthorized_prompt,
)
from tests.utils.telegram.session import RecordingBot


async def test_help_text_escapes_dynamic_html_parameters(test_container: AsyncContainer) -> None:
    """Verify dynamic parameters interpolated into HTML help text are escaped."""
    settings = await test_container.get(AppSettings)
    access_service = await test_container.get(AccessService)
    assert isinstance(settings, Settings)
    unsafe_settings = settings.model_copy(update={"project_description": "<script>alert('xss')</script> & friends"})
    help_cmd = HelpCommand(unsafe_settings, access_service)

    uk_text = help_cmd.get_help_text(locale="uk")
    en_text = help_cmd.get_help_text(locale="en")

    for text in (uk_text, en_text):
        assert "<script>" not in text
        assert "&lt;script&gt;alert(&#x27;xss&#x27;)&lt;/script&gt; &amp; friends" in text


async def test_unauthorized_prompt_uses_chat_locale(
    test_container: AsyncContainer,
    recording_bot: RecordingBot,
) -> None:
    """Verify unauthorized prompt builder and sender localize using chat's ui_locale."""
    en_text, en_builder = build_unauthorized_prompt(-1001, 1, 0, locale="en")
    uk_text, uk_builder = build_unauthorized_prompt(-1001, 1, 0, locale="uk")

    assert "Chat Authorization Required" in en_text
    assert "Activate / Request Access" in en_builder.export()[0][0].text
    assert "Потрібна авторизація чату" in uk_text
    assert "Активувати / Запросити доступ" in uk_builder.export()[0][0].text

    session_factory = await test_container.get(async_sessionmaker)
    group_id = -100710055
    async with session_factory() as session:
        session.add(
            ChatDB(
                chat_id=group_id,
                platform=Platform.TELEGRAM,
                status=ChatStatus.UNAUTHORIZED,
                chat_type="supergroup",
                ui_locale="uk",
            )
        )
        await session.commit()

    chat_store = await test_container.get(ChatStore)
    sender = TelegramUnauthorizedPromptSender(chat_store)
    sent_ok = await sender.send(recording_bot, group_id)
    assert sent_ok is True

    sent = [c for c in recording_bot.session.calls if isinstance(c, SendMessage) and c.chat_id == group_id]
    assert len(sent) == 1
    assert "Потрібна авторизація чату" in sent[0].text
    assert "/allow_chat" in sent[0].text
    assert "/feedback" in sent[0].text
