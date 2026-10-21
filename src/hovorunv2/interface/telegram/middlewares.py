"""ALL middlewares of Hovorun application."""

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.dispatcher.flags import get_flag
from aiogram.dispatcher.middlewares.user_context import EVENT_CONTEXT_KEY, EventContext, UserContextMiddleware
from aiogram.enums import ChatType
from aiogram.types import CallbackQuery, ChatMemberUpdated, Message, TelegramObject, Update

from hovorunv2.application.data.constants import TOGGLEABLE_COMMANDS
from hovorunv2.application.services.access_service import AccessService, CommandPolicy
from hovorunv2.application.services.message_service import MessageService
from hovorunv2.infrastructure.logger import get_logger, set_log_context
from hovorunv2.interface.telegram.handlers.help import HelpCommand

logger = get_logger(__name__)


class LogContextMiddleware(BaseMiddleware):
    """Outer middleware to bind user_id and chat_id into logging ContextVars."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        """Bind event user and chat IDs to logging context for the current update task."""
        user_id, chat_id = self._extract_ids(event, data)
        set_log_context(user_id=user_id, chat_id=chat_id)
        return await handler(event, data)

    def _extract_ids(self, event: TelegramObject, data: dict[str, Any]) -> tuple[int | None, int | None]:
        """Extract user_id and chat_id from Telegram events or aiogram EventContext."""
        event_context = data.get(EVENT_CONTEXT_KEY)
        if isinstance(event_context, EventContext):
            return event_context.user_id, event_context.chat_id
        if isinstance(event, Update):
            resolved = UserContextMiddleware.resolve_event_context(event)
            return resolved.user_id, resolved.chat_id
        if isinstance(event, (Message, ChatMemberUpdated)):
            from_user = getattr(event, "from_user", None)
            user_id = from_user.id if from_user else None
            chat = getattr(event, "chat", None)
            chat_id = chat.id if chat else None
            return user_id, chat_id
        if isinstance(event, CallbackQuery):
            from_user = getattr(event, "from_user", None)
            user_id = from_user.id if from_user else None
            msg = getattr(event, "message", None)
            chat = getattr(msg, "chat", None) if msg else None
            chat_id = chat.id if chat else None
            return user_id, chat_id
        return None, None


class MessageCacheMiddleware(BaseMiddleware):
    """Outer middleware to cache every incoming message."""

    def __init__(self, message_service: MessageService) -> None:
        """Initialize middleware with dependencies."""
        super().__init__()
        self._message_service = message_service

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        """Cache message before any filters run."""
        if isinstance(event, Message):
            await self._message_service.cache_message(event)
        return await handler(event, data)


class AccessMiddleware(BaseMiddleware):
    """Unified middleware to handle all access control via CommandPolicy."""

    def __init__(self, access_service: AccessService) -> None:
        """Initialize middleware with dependencies."""
        super().__init__()
        self._access_service = access_service

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        """Check authorization before executing handler."""
        handler_obj = data.get("handler")
        policy = (
            get_flag(data, "policy")
            or data.get("policy")
            or (getattr(handler_obj, "flags", {}).get("policy") if handler_obj else None)
        )
        command_name = (
            get_flag(data, "command_name")
            or data.get("command_name")
            or (getattr(handler_obj, "flags", {}).get("command_name") if handler_obj else None)
        )

        # If no policy is attached, it might be a default command
        # However, for toggleable commands we MUST have a policy to evaluate access
        if not policy or not isinstance(policy, CommandPolicy):
            # If it's a known toggleable command but has no policy, DENY by default
            if command_name in TOGGLEABLE_COMMANDS:
                logger.error("Toggleable command %s missing policy in middleware!", command_name)
                return None
            return await handler(event, data)

        user_id, chat_id, is_private = self._extract_event_context(event)
        if not chat_id or (is_private and user_id and await self._access_service.is_admin(user_id)):
            return await handler(event, data)

        # 1. Handle Private Chats (Direct Messages)
        if is_private:
            await self._deny_private_chat(event)
            return None

        # 2. Evaluate Policy
        is_allowed = await self._access_service.is_allowed(
            user_id=user_id,
            chat_id=chat_id,
            policy=policy,
            bot=data["bot"],
            command_name=command_name,
        )

        if is_allowed:
            return await handler(event, data)

        logger.info("Access denied for %s in chat %d by user %s", command_name or "callback", chat_id, user_id)

        if isinstance(event, CallbackQuery):
            await event.answer("🚫 You don't have permission to do this.", show_alert=True)

        return None

    def _extract_event_context(self, event: TelegramObject) -> tuple[int | None, int, bool]:
        """Extract user_id, chat_id, and private chat flag from a Telegram event."""
        if isinstance(event, Message):
            user_id = event.from_user.id if event.from_user else None
            return user_id, event.chat.id, event.chat.type == ChatType.PRIVATE
        if isinstance(event, CallbackQuery):
            user_id = event.from_user.id
            if event.message and isinstance(event.message, Message):
                return user_id, event.message.chat.id, event.message.chat.type == ChatType.PRIVATE
            return user_id, 0, False
        return None, 0, False

    async def _deny_private_chat(self, event: TelegramObject) -> None:
        """Send landing page or access denied alert to non-admin private chat users."""
        if isinstance(event, Message):
            help_cmd = HelpCommand()
            await event.answer(help_cmd.get_help_text(), parse_mode="HTML", disable_web_page_preview=True)
        elif isinstance(event, CallbackQuery):
            await event.answer("🚫 Access denied.", show_alert=True)
