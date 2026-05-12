"""ALL middlewares of Hovorun application."""

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.dispatcher.flags import get_flag
from aiogram.enums import ChatType
from aiogram.types import Message, TelegramObject

from hovorunv2.application.services.access_service import AccessService, CommandPolicy
from hovorunv2.application.services.message_service import MessageService
from hovorunv2.infrastructure.logger import get_logger

logger = get_logger(__name__)


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
    ) -> Any:  # noqa: ANN401
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
    ) -> Any:  # noqa: ANN401
        """Check authorization before executing handler."""
        from aiogram.types import CallbackQuery  # noqa: PLC0415

        policy = get_flag(data, "policy")
        command_name = get_flag(data, "command_name")

        # If no policy is attached, continue
        if not policy or not isinstance(policy, CommandPolicy):
            return await handler(event, data)

        user_id: int | None = None
        chat_id: int = 0
        is_private = False

        if isinstance(event, Message):
            user_id = event.from_user.id if event.from_user else None
            chat_id = event.chat.id
            is_private = event.chat.type == ChatType.PRIVATE
        elif isinstance(event, CallbackQuery):
            user_id = event.from_user.id
            if event.message and isinstance(event.message, Message):
                chat_id = event.message.chat.id
                is_private = event.message.chat.type == ChatType.PRIVATE

        if not chat_id:
            return await handler(event, data)

        # 1. Handle Private Chats (Direct Messages)
        if is_private:
            # Owners/Admins get full access to everything in DM
            if user_id and await self._access_service.is_admin(user_id):
                return await handler(event, data)

            # Non-admins get the landing page for ANY interaction in DM
            if isinstance(event, Message):
                from hovorunv2.interface.telegram.handlers.help import HelpCommand  # noqa: PLC0415

                help_cmd = HelpCommand()
                await event.answer(help_cmd.get_help_text(), parse_mode="HTML", disable_web_page_preview=True)
            elif isinstance(event, CallbackQuery):
                await event.answer("🚫 Access denied.", show_alert=True)
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
