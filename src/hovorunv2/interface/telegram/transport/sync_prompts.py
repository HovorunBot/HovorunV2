"""Pure synchronous unauthorized prompt formatting and transport adapter."""

import json

from hovorunv2.application.operations.ports import SyncUnauthorizedPromptPort, WorkerTelegramPort

ACCESS_REQUEST_BUTTON_TEXT: str = "🛡️ Activate / Request Access"

UNAUTHORIZED_PROMPT_TEXT: str = (
    "👋 <b>Hello! I am Hovorun — Your Media Companion</b>\n\n"
    "I enhance group conversations with fast media processing and automatic translation.\n\n"
    "⚠️ <b>Chat Authorization Required:</b>\n"
    "To keep performance reliable, I only operate in approved chats.\n"
    "A trusted user can activate the bot with the button below or <code>/allow_chat</code>. "
    "Other group administrators can request approval.\n\n"
    "💬 <i>Need help or have questions? Type <code>/feedback &lt;message&gt;</code> to contact developers.</i>"
)


def format_access_request_callback(chat_id: int, group_id: int, generation: int) -> str:
    """Format the pure callback string matching AccessCallback wire representation.

    Args:
        chat_id: Telegram chat ID receiving the prompt.
        group_id: Group entity ID bound into the approval callback.
        generation: Group identity generation bound into the approval callback.

    Returns:
        Colon-delimited callback string unpackable by aiogram AccessCallback.
    """
    return f"access:{chat_id}:request:{group_id}:{generation}:0"


def format_unauthorized_prompt_keyboard(chat_id: int, group_id: int, generation: int) -> dict[str, object]:
    """Build the pure inline keyboard dictionary for an unauthorized prompt.

    Args:
        chat_id: Telegram chat ID receiving the prompt.
        group_id: Group entity ID bound into the approval callback.
        generation: Group identity generation bound into the approval callback.

    Returns:
        Dictionary formatted according to Telegram Bot API InlineKeyboardMarkup schema.
    """
    return {
        "inline_keyboard": [
            [
                {
                    "text": ACCESS_REQUEST_BUTTON_TEXT,
                    "callback_data": format_access_request_callback(chat_id, group_id, generation),
                }
            ]
        ]
    }


class SyncTelegramUnauthorizedPromptSender(SyncUnauthorizedPromptPort):
    """Delivers unauthorized guidance prompt to Telegram chats synchronously without aiogram."""

    def __init__(self, api: WorkerTelegramPort) -> None:
        self._api = api

    def send(self, chat_id: int, group_id: int, generation: int) -> bool:
        """Send prompt to unauthorized chat.

        Args:
            chat_id: Telegram chat ID receiving the prompt.
            group_id: Group entity ID bound into the approval callback.
            generation: Group identity generation bound into the approval callback.

        Returns:
            True when the prompt was delivered, False otherwise.
        """
        reply_markup = json.dumps(format_unauthorized_prompt_keyboard(chat_id, group_id, generation))
        return self._api.send_message(chat_id, UNAUTHORIZED_PROMPT_TEXT, reply_markup=reply_markup) is not None
