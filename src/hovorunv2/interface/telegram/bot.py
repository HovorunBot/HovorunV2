"""Telegram bot message handlers."""

from aiogram import Router
from dishka import AsyncContainer

from hovorunv2.application.services.access_service import AccessService
from hovorunv2.application.services.message_service import MessageService
from hovorunv2.interface.telegram.handlers.base import BaseCommand
from hovorunv2.interface.telegram.middlewares import (
    AccessMiddleware,
    MessageCacheMiddleware,
)

router = Router()


async def setup_middlewares(
    container: AsyncContainer,  # noqa: ARG001
    message_service: MessageService,
    access_service: AccessService,
) -> None:
    """Register middlewares with the router."""
    router.message.outer_middleware(MessageCacheMiddleware(message_service))
    router.message.middleware(AccessMiddleware(access_service))


def setup_handlers(commands: list[BaseCommand]) -> None:
    """Register all command handlers with the router."""
    for command in commands:
        flags = {
            "command_name": command.name,
            "policy": command.policy,
        }

        # Register the command handler with its custom trigger filter
        router.message.register(
            command.handle,
            command.is_triggered,
            flags=flags,
        )
