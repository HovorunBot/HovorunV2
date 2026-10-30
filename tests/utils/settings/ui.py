"""Settings UI test helpers and callback inspection."""

import json
from datetime import UTC, datetime

from aiogram import Bot
from aiogram.types import CallbackQuery, Chat, Message, User

from hovorunv2.interface.telegram.callbacks import SettingsCallback, SettingsMenu
from tests.utils.activation import sent_fields
from tests.utils.runtimes.beta import FIRST, GROUP
from tests.utils.telegram.transport import API, RecordedResponse, RecordedResponses

DENIED = "Settings unavailable. Reopen /settings."


def settings_message(chat_id: int = GROUP, actor_id: int = FIRST, message_id: int = 31) -> Message:
    """Supply real transport facts independently of callback arguments."""
    return Message(
        message_id=message_id,
        date=datetime.now(UTC),
        chat=Chat(id=chat_id, type="private" if chat_id > 0 else "supergroup"),
        from_user=User(id=actor_id, is_bot=False, first_name="Synthetic"),
        text="/settings",
    )


def query_for(message: Message, bot: Bot, actor_id: int = FIRST) -> CallbackQuery:
    """Bind callback and nested message to the external Telegram client."""
    return CallbackQuery(
        id="synthetic-settings",
        chat_instance="synthetic",
        from_user=User(id=actor_id, is_bot=False, first_name="Synthetic"),
        message=message.as_(bot),
    ).as_(bot)


def settings_http(http: RecordedResponses, message: Message, *, admin: bool = True) -> None:
    """Intercept external Telegram calls only, including fresh membership queries."""
    for method in ("sendMessage", "editMessageText"):
        http.post(
            f"{API}/{method}",
            payload={"ok": True, "result": message.model_dump(mode="json")},
            repeat=True,
            response_class=RecordedResponse,
        )
    for method in ("answerCallbackQuery", "deleteMessage"):
        http.post(f"{API}/{method}", payload={"ok": True, "result": True}, repeat=True, response_class=RecordedResponse)
    user_payload = message.from_user.model_dump(mode="json") if message.from_user else {}
    member = {"status": "creator" if admin else "member", "user": user_payload}
    if admin:
        member["is_anonymous"] = False
    http.post(
        f"{API}/getChatMember", payload={"ok": True, "result": member}, repeat=True, response_class=RecordedResponse
    )


def button(http: RecordedResponses, menu: SettingsMenu, *, value: str | None = None) -> SettingsCallback:
    """Read the real server-rendered callback rather than fabricate trusted state."""
    markup = json.loads(sent_fields(http, "editMessageText")[-1]["reply_markup"])
    for row in markup["inline_keyboard"]:
        for item in row:
            if not item["callback_data"].startswith("settings:"):
                continue
            callback = SettingsCallback.unpack(item["callback_data"])
            assert len(callback.pack().encode()) <= 64  # Telegram callback byte limit.
            if callback.menu == menu and (value is None or callback.value == value):
                return callback
    msg = f"Button for menu {menu} (value={value}) not found in rendered markup"
    raise AssertionError(msg)
