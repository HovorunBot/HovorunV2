"""Middlewares for Aiogram bot."""

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import Message

from hovorunv2.infrastructure.container import container
from hovorunv2.infrastructure.logger import get_logger

logger = get_logger(__name__)


class MessageCacheMiddleware(BaseMiddleware):
    """Outer middleware to cache every incoming message."""

    async def __call__(
        self,
        handler: Callable[[Message, dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: dict[str, Any],
    ) -> Any:
        """Cache message before any filters run."""
        if container.message_service:
            await container.message_service.cache_message(event)
        return await handler(event, data)


class WhitelistMiddleware(BaseMiddleware):
    """Inner middleware to block messages from non-whitelisted chats."""

    async def __call__(
        self,
        handler: Callable[[Message, dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: dict[str, Any],
    ) -> Any:
        """Check whitelist before executing handler."""
        # Allow handlers with 'bypass_whitelist' flag
        handler_flags = data.get("handler_flags", {})
        if handler_flags.get("bypass_whitelist"):
            return await handler(event, data)

        is_whitelisted = await container.whitelist_service.is_whitelisted(event.chat.id)
        if is_whitelisted:
            return await handler(event, data)

        logger.debug("Ignoring message in non-whitelisted chat %d", event.chat.id)
        return None
