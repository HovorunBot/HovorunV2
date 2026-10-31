"""Real settings-session setup for Telegram integration regressions."""

from aiogram.types import Message
from dishka import AsyncContainer

from hovorunv2.application.settings.settings_session_service import SettingsSessionService
from hovorunv2.interface.telegram.callbacks import SettingsCallback


async def bind_settings_callback(
    container: AsyncContainer,
    message: Message,
    actor_id: int,
    callback: SettingsCallback,
    *,
    target: int | None = None,
) -> SettingsCallback:
    """Issue a real bounded session without pretending callback fields establish authority."""
    sessions = await container.get(SettingsSessionService)
    context = await sessions.prepare(target or message.chat.id, actor_id, message.chat.id, message.message_id)
    token = await sessions.issue(context, [callback.pack()])
    return callback.model_copy(update={"token": token})
