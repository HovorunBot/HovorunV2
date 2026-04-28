"""ALL middlewares of Hovorun application."""

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.dispatcher.flags import get_flag
from aiogram.types import Message, TelegramObject

from hovorunv2.application.services.command_service import CommandService
from hovorunv2.application.services.message_service import MessageService
from hovorunv2.application.services.whitelist_service import WhitelistService
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


class WhitelistMiddleware(BaseMiddleware):
    """Inner middleware to block messages from non-whitelisted chats."""

    def __init__(self, whitelist_service: WhitelistService) -> None:
        """Initialize middleware with dependencies."""
        super().__init__()
        self._whitelist_service = whitelist_service

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:  # noqa: ANN401
        """Check whitelist before executing handler."""
        # Only process Messages
        if not isinstance(event, Message):
            return await handler(event, data)

        # Allow handlers with 'bypass_whitelist' flag
        if get_flag(data, "bypass_whitelist"):
            return await handler(event, data)

        is_whitelisted = await self._whitelist_service.is_whitelisted(event.chat.id)
        if is_whitelisted:
            return await handler(event, data)

        logger.debug("Ignoring message in non-whitelisted chat %d", event.chat.id)
        return None


class CommandConfigurationMiddleware(BaseMiddleware):
    """Middleware to check if command is enabled for the chat."""

    def __init__(self, command_service: CommandService) -> None:
        """Initialize middleware with dependencies."""
        super().__init__()
        self._command_service = command_service

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:  # noqa: ANN401
        """Check if command is enabled before executing handler."""
        if not isinstance(event, Message):
            return await handler(event, data)

        command_name = get_flag(data, "command_name")

        if not command_name:
            return await handler(event, data)

        is_allowed = await self._command_service.is_command_allowed(event.chat.id, command_name)
        if is_allowed:
            return await handler(event, data)

        logger.info("Command %s is not allowed in chat %d", command_name, event.chat.id)
        return None
