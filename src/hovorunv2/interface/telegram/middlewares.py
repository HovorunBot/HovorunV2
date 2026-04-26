"""ALL middlewares of Hovorun application."""

from typing import TYPE_CHECKING, Any

from aiogram import BaseMiddleware
from aiogram.dispatcher.flags import get_flag
from aiogram.types import Message, TelegramObject

from hovorunv2.infrastructure.container import container
from hovorunv2.infrastructure.logger import get_logger

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

logger = get_logger(__name__)


class MessageCacheMiddleware(BaseMiddleware):
    """Outer middleware to cache every incoming message."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:  # noqa: ANN401
        """Cache message before any filters run."""
        if isinstance(event, Message) and container.message_service:
            await container.message_service.cache_message(event)
        return await handler(event, data)


class WhitelistMiddleware(BaseMiddleware):
    """Inner middleware to block messages from non-whitelisted chats."""

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

        if not container.whitelist_service:
            logger.error("WhitelistService not initialized")
            return None

        is_whitelisted = await container.whitelist_service.is_whitelisted(event.chat.id)
        if is_whitelisted:
            return await handler(event, data)

        logger.debug("Ignoring message in non-whitelisted chat %d", event.chat.id)
        return None


class CommandConfigurationMiddleware(BaseMiddleware):
    """Middleware to check if command is enabled for the chat."""

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

        if not container.command_service:
            logger.error("CommandService not initialized")
            return await handler(event, data)

        is_allowed = await container.command_service.is_command_allowed(event.chat.id, command_name)
        if is_allowed:
            return await handler(event, data)

        logger.info("Command %s is not allowed in chat %d", command_name, event.chat.id)
        return None
