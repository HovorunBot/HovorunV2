"""Early ingestion of public Telegram identity evidence."""

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.enums import ChatType
from aiogram.types import CallbackQuery, Chat, ChatMemberUpdated, Message, TelegramObject, User

from hovorunv2.application.group_migration import GroupMigrationStore
from hovorunv2.application.identity import GroupIdentityStore
from hovorunv2.application.ports import PublicHandleStore, UserStore
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.enums import TargetKind


class IdentityMiddleware(BaseMiddleware):
    """Observe numeric identity before caching, filters and authorization."""

    def __init__(
        self, users: UserStore, groups: GroupIdentityStore, handles: PublicHandleStore, migrations: GroupMigrationStore
    ) -> None:
        self._users = users
        self._groups = groups
        self._handles = handles
        self._migrations = migrations

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        """Persist minimal observed fields and never ingest message contents."""
        active = True
        if isinstance(event, Message):
            if event.migrate_to_chat_id is not None or event.migrate_from_chat_id is not None:
                await self._migrate(event)
                return None
            if event.chat.type in {ChatType.GROUP, ChatType.SUPERGROUP}:
                canonical = await self._migrations.canonical_id(event.chat.id)
                if canonical is not None and canonical != event.chat.id:
                    return None
            active = await self.observe(event.chat, event.from_user if event.sender_chat is None else None)
        elif isinstance(event, CallbackQuery):
            chat = event.message.chat if event.message is not None else None
            active = await self.observe(chat, event.from_user)
        elif isinstance(event, ChatMemberUpdated):
            active = await self.observe(event.chat, event.from_user)
        if not active:
            return None
        return await handler(event, data)

    async def observe(self, chat: Chat | None, user: User | None) -> bool:
        """Sync only human users and group public metadata from Telegram evidence."""
        actor = ActorContext.system("telegram_identity_evidence")
        if user is not None and not user.is_bot:
            active_user = await self._users.ingest_user(user.id, actor=actor)
            if active_user is None:
                return False
            await self._handles.sync_handle(TargetKind.USER, user.id, user.username, actor)
        if chat is not None and chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
            active_group = await self._groups.ingest_group(chat.id, chat.title, chat.type)
            if active_group is None or active_group.chat_id != chat.id:
                return False
            await self._handles.sync_handle(TargetKind.GROUP, chat.id, chat.username, actor)
        return True

    async def _migrate(self, message: Message) -> None:
        """Consume authentic service fields before new-ID ingestion creates a competing group."""
        if message.migrate_to_chat_id is not None and message.chat.type == ChatType.GROUP:
            old_id, new_id = message.chat.id, message.migrate_to_chat_id
        elif message.migrate_from_chat_id is not None and message.chat.type == ChatType.SUPERGROUP:
            old_id, new_id = message.migrate_from_chat_id, message.chat.id
        else:
            return
        try:
            await self._migrations.migrate(old_id, new_id)
        except ValueError:
            # Conflicting or deleted identities require operator review; never merge grants.
            return
