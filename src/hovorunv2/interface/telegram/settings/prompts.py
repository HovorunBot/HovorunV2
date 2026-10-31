"""Telegram unauthorized prompt presentation and delivery."""

import json

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError
from aiogram.types import InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from hovorunv2.application.ports import ChatStore, SyncUnauthorizedPromptPort, WorkerTelegramPort
from hovorunv2.infrastructure.logger import get_logger
from hovorunv2.interface.telegram.settings.callbacks import AccessAction, AccessCallback

logger = get_logger(__name__)


def build_unauthorized_prompt(chat_id: int, group_id: int, generation: int) -> tuple[str, InlineKeyboardBuilder]:
    """Generate guidance text and approval button for unauthorized chats."""
    builder = InlineKeyboardBuilder()
    builder.add(
        InlineKeyboardButton(
            text="🛡️ Activate / Request Access",
            callback_data=AccessCallback(
                chat_id=chat_id, group_id=group_id, generation=generation, action=AccessAction.REQUEST
            ).pack(),
        )
    )

    text = (
        "👋 <b>Hello! I am Hovorun — Your Media Companion</b>\n\n"
        "I enhance group conversations with fast media processing and automatic translation.\n\n"
        "⚠️ <b>Chat Authorization Required:</b>\n"
        "To keep performance reliable, I only operate in approved chats.\n"
        "A trusted user can activate the bot with the button below or <code>/allow_chat</code>. "
        "Other group administrators can request approval.\n\n"
        "💬 <i>Need help or have questions? Type <code>/feedback &lt;message&gt;</code> to contact developers.</i>"
    )
    return text, builder


class TelegramUnauthorizedPromptSender:
    """Delivers unauthorized guidance prompt to Telegram chats."""

    def __init__(self, chats: ChatStore) -> None:
        self._chats = chats

    def _is_eligible_group(self, chat_type: str | None) -> bool:
        """Check if chat type supports group unauthorized prompts."""
        return chat_type in {"group", "supergroup"}

    async def _dispatch_prompt(
        self,
        bot: Bot,
        chat_id: int,
        text: str,
        builder: InlineKeyboardBuilder,
    ) -> bool:
        """Dispatch prompt message catching expected transport errors."""
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode="HTML",
                reply_markup=builder.as_markup(),
            )
        except TelegramForbiddenError:
            logger.warning("Bot removed or blocked in chat %d", chat_id)
            return False
        except Exception:
            logger.exception("Failed to send unauthorized prompt to chat %d", chat_id)
            return False
        return True

    async def send(self, bot: Bot, chat_id: int) -> bool:
        """Send prompt to chat. Returns True if sent, False if forbidden or on error."""
        chat = await self._chats.get_chat(chat_id)
        if chat is None or not self._is_eligible_group(chat.chat_type):
            return False
        text, builder = build_unauthorized_prompt(chat_id, chat.id, chat.generation)
        return await self._dispatch_prompt(bot, chat_id, text, builder)


class SyncTelegramUnauthorizedPromptSender(SyncUnauthorizedPromptPort):
    """Delivers unauthorized guidance prompt to Telegram chats synchronously."""

    def __init__(self, api: WorkerTelegramPort) -> None:
        self._api = api

    def send(self, chat_id: int, group_id: int, generation: int) -> bool:
        """Send prompt to unauthorized chat."""
        text, builder = build_unauthorized_prompt(chat_id, group_id, generation)
        reply_markup = json.dumps(builder.as_markup().model_dump(mode="json"))
        return self._api.send_message(chat_id, text, reply_markup=reply_markup) is not None
