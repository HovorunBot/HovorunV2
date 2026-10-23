"""Telegram unauthorized prompt presentation and delivery."""

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError
from aiogram.types import InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from hovorunv2.infrastructure.logger import get_logger
from hovorunv2.interface.telegram.callbacks import AccessAction, AccessCallback

logger = get_logger(__name__)


def build_unauthorized_prompt(chat_id: int) -> tuple[str, InlineKeyboardBuilder]:
    """Generate guidance text and approval button for unauthorized chats."""
    builder = InlineKeyboardBuilder()
    builder.add(
        InlineKeyboardButton(
            text="🛡️ Request Approval",
            callback_data=AccessCallback(chat_id=chat_id, action=AccessAction.REQUEST).pack(),
        )
    )

    text = (
        "👋 <b>Hello! I am Hovorun — Your Media Companion</b>\n\n"
        "I enhance group conversations with fast media processing and automatic translation.\n\n"
        "⚠️ <b>Chat Authorization Required:</b>\n"
        "To keep performance reliable, I only operate in approved chats.\n"
        "A chat administrator can tap the button below or type <code>/allow_chat</code> "
        "to request access from the bot owners.\n\n"
        "💬 <i>Need help or have questions? Type <code>/feedback &lt;message&gt;</code> to contact developers.</i>"
    )
    return text, builder


class TelegramUnauthorizedPromptSender:
    """Delivers unauthorized guidance prompt to Telegram chats."""

    async def send(self, bot: Bot, chat_id: int) -> bool:
        """Send prompt to chat. Returns True if sent, False if forbidden or on error."""
        text, builder = build_unauthorized_prompt(chat_id)
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
        else:
            return True
