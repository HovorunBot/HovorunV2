from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject

from hovorunv2.infrastructure.container import container
from hovorunv2.infrastructure.logger import get_logger

logger = get_logger(__name__)


class MessageCacheMiddleware(BaseMiddleware):
    """Outer middleware to cache every incoming message."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
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
    ) -> Any:
        """Check whitelist before executing handler."""
        # Only process Messages
        if not isinstance(event, Message):
            return await handler(event, data)

        # Allow handlers with 'bypass_whitelist' flag
        handler_flags = data.get("handler_flags", {})
        if handler_flags.get("bypass_whitelist"):
            return await handler(event, data)

        if not container.whitelist_service:
            logger.error("WhitelistService not initialized")
            return None

        is_whitelisted = await container.whitelist_service.is_whitelisted(event.chat.id)
        if is_whitelisted:
            return await handler(event, data)

        logger.debug("Ignoring message in non-whitelisted chat %d", event.chat.id)
        return None
