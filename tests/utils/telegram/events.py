"""Telegram event payload and update builders."""

from datetime import datetime

from aiogram.types import Chat, ChatMemberLeft, ChatMemberMember, ChatMemberUpdated, User

from tests.utils.telegram.transport import USER_ID

BOT_ID = 123456
ACTOR = 230001


def member_payload(status: str = "member", *, is_bot: bool = False) -> dict[str, object]:
    """Sanitized external Telegram response with only required synthetic fields."""
    result: dict[str, object] = {
        "status": status,
        "user": {"id": BOT_ID if is_bot else USER_ID, "is_bot": is_bot, "first_name": "Synthetic"},
    }
    if status == "creator":
        result["is_anonymous"] = False
    if status == "administrator":
        for field in (
            "can_be_edited",
            "is_anonymous",
            "can_manage_chat",
            "can_delete_messages",
            "can_manage_video_chats",
            "can_restrict_members",
            "can_promote_members",
            "can_change_info",
            "can_invite_users",
            "can_post_stories",
            "can_edit_stories",
            "can_delete_stories",
            "can_send_welcome_messages",
            "can_pin_messages",
        ):
            result[field] = field == "can_delete_messages"
    return result


def bot_event(
    chat_id: int,
    *,
    present: bool,
    date: datetime,
    bot_id: int = BOT_ID,
    actor_id: int = ACTOR,
) -> ChatMemberUpdated:
    """Create authentic event shapes for the synthetic bot without group-directory data."""
    user = User(id=bot_id, is_bot=True, first_name="Bot")
    return ChatMemberUpdated(
        chat=Chat(id=chat_id, type="supergroup", title="Synthetic group"),
        from_user=User(id=actor_id, is_bot=False, first_name="Synthetic"),
        date=date,
        old_chat_member=ChatMemberLeft(user=user),
        new_chat_member=ChatMemberMember(user=user) if present else ChatMemberLeft(user=user),
    )
