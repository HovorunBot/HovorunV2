"""ALL middlewares of Hovorun application."""

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.dispatcher.flags import get_flag
from aiogram.enums import ChatType
from aiogram.types import Message, TelegramObject

from hovorunv2.application.services.access_service import AccessService, CommandPolicy
from hovorunv2.application.services.message_service import MessageService
from hovorunv2.infrastructure.config import settings
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
        # Only process Messages
        if not isinstance(event, Message):
            return await handler(event, data)

        policy = get_flag(data, "policy")
        command_name = get_flag(data, "command_name")

        # If no policy is attached, we assume it's a generic message that doesn't need auth
        # (or auth is handled elsewhere/by a default policy)
        if not policy or not isinstance(policy, CommandPolicy):
            return await handler(event, data)

        user_id = event.from_user.id if event.from_user else None
        chat_id = event.chat.id

        # 1. Handle Private Chats (Direct Messages)
        if event.chat.type == ChatType.PRIVATE:
            # Admins get full access to everything in DM
            if user_id in settings.admin_ids:
                return await handler(event, data)

            # Non-admins get the landing page for ANY interaction in DM
            from hovorunv2.interface.telegram.handlers.help import HelpCommand  # noqa: PLC0415

            help_cmd = HelpCommand()
            await event.answer(help_cmd.get_help_text(), parse_mode="HTML", disable_web_page_preview=True)
            return None

        # 2. Evaluate Policy
        is_allowed = await self._access_service.is_allowed(
            user_id=user_id,
            chat_id=chat_id,
            policy=policy,
            command_name=command_name,
        )

        if is_allowed:
            return await handler(event, data)

        logger.info("Access denied for command %s in chat %d", command_name, chat_id)
        return None
