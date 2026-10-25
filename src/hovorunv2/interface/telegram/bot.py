"""Telegram bot router factory and configuration."""

from aiogram import Router

from hovorunv2.application.ports import AppSettings
from hovorunv2.application.services.access_service import AccessService
from hovorunv2.application.services.message_service import MessageService
from hovorunv2.interface.telegram.handlers.base import BaseCommand
from hovorunv2.interface.telegram.handlers.bot_join import BotJoinHandler
from hovorunv2.interface.telegram.identity import IdentityMiddleware
from hovorunv2.interface.telegram.middlewares import (
    AccessMiddleware,
    LogContextMiddleware,
    MessageCacheMiddleware,
)


def build_router(
    commands: list[BaseCommand],
    message_service: MessageService,
    access_service: AccessService,
    settings: AppSettings,
    identity_middleware: IdentityMiddleware,
    bot_join_handler: BotJoinHandler | None = None,
) -> Router:
    """Build and configure the main Telegram router with middlewares and handlers."""
    router = Router()

    router.message.outer_middleware(identity_middleware)
    router.callback_query.outer_middleware(identity_middleware)
    router.my_chat_member.outer_middleware(identity_middleware)
    router.chat_member.outer_middleware(identity_middleware)

    # Outer middlewares (run before filter evaluation)
    log_context_middleware = LogContextMiddleware()
    router.message.outer_middleware(log_context_middleware)
    router.callback_query.outer_middleware(log_context_middleware)
    router.my_chat_member.outer_middleware(log_context_middleware)

    # Inner middlewares (run after filters, before handlers)
    router.message.middleware(AccessMiddleware(access_service, settings))
    router.callback_query.middleware(AccessMiddleware(access_service, settings))
    router.message.middleware(MessageCacheMiddleware(message_service))

    # Register bot join/leave events
    if bot_join_handler:
        bot_join_handler.register(router)

    # Register commands and callbacks
    for command in commands:
        flags = {
            "command_name": command.name,
            "policy": command.policy,
        }

        router.message.register(
            command.handle,
            command.trigger,
            flags=flags,
        )

        command.register_callbacks(router, flags=flags)

    return router
