"""Private chat IDs are stored with the private chat type."""

from dishka import AsyncContainer

from hovorunv2.application.groups.ports import ChatStore

USER = 680001
GROUP = -680002


async def test_chat_store_infers_private_and_group_types(test_container: AsyncContainer) -> None:
    """Positive IDs are private; negative IDs are groups."""
    chats = await test_container.get(ChatStore)
    private = await chats.get_or_create_chat(USER)
    group = await chats.get_or_create_chat(GROUP)
    assert private.chat_type == "private"
    assert group.chat_type == "group"
