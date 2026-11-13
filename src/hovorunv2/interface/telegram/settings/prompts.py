"""Telegram unauthorized prompt presentation and delivery."""

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError
from aiogram.types import InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from hovorunv2.application.groups.ports import ChatStore
from hovorunv2.infrastructure.logger import get_logger
from hovorunv2.interface.telegram.i18n import get_message
from hovorunv2.interface.telegram.settings.callbacks import AccessAction, AccessCallback
from hovorunv2.interface.telegram.transport.sync_prompts import (
    ACCESS_REQUEST_BUTTON_TEXT,
    UNAUTHORIZED_PROMPT_TEXT,
    SyncTelegramUnauthorizedPromptSender,
)

__all__ = [
    "ACCESS_REQUEST_BUTTON_TEXT",
    "UNAUTHORIZED_PROMPT_TEXT",
    "SyncTelegramUnauthorizedPromptSender",
    "TelegramUnauthorizedPromptSender",
    "build_unauthorized_prompt",
]

logger = get_logger(__name__)


def build_unauthorized_prompt(
    chat_id: int,
    group_id: int,
    generation: int,
    locale: str | None = None,
) -> tuple[str, InlineKeyboardBuilder]:
    """Generate guidance text and approval button for unauthorized chats.

    Args:
        chat_id: Telegram chat ID receiving the prompt.
        group_id: Group entity ID bound into the approval callback.
        generation: Group identity generation bound into the approval callback.
        locale: Optional explicit locale code ('uk' or 'en').

    Returns:
        Guidance text and keyboard builder carrying the approval request.
    """
    builder = InlineKeyboardBuilder()
    button_text = get_message("unauthorized-btn-text", locale)
    builder.add(
        InlineKeyboardButton(
            text=button_text,
            callback_data=AccessCallback(
                chat_id=chat_id, group_id=group_id, generation=generation, action=AccessAction.REQUEST
            ).pack(),
        )
    )
    prompt_text = get_message("unauthorized-prompt-text", locale)
    return prompt_text, builder


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
        """Dispatch prompt message catching expected transport errors.

        Args:
            bot: Telegram bot delivering the prompt.
            chat_id: Telegram chat ID receiving the prompt.
            text: Guidance text to send.
            builder: Keyboard builder carrying the approval request.

        Returns:
            True when the prompt was sent, False on transport errors.
        """
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
        except TelegramAPIError:
            logger.exception("Failed to send unauthorized prompt to chat %d", chat_id)
            return False
        return True

    async def send(self, bot: Bot, chat_id: int) -> bool:
        """Send prompt to chat, returning False when forbidden or on error.

        Args:
            bot: Telegram bot delivering the prompt.
            chat_id: Telegram chat ID receiving the prompt.

        Returns:
            True when the prompt was sent, False otherwise.
        """
        chat = await self._chats.get_chat(chat_id)
        if chat is None or not self._is_eligible_group(chat.chat_type):
            return False
        text, builder = build_unauthorized_prompt(chat_id, chat.id, chat.generation, locale=chat.ui_locale)
        return await self._dispatch_prompt(bot, chat_id, text, builder)
