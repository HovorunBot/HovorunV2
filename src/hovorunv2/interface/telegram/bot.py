"""Telegram bot router factory and configuration."""

from aiogram import Router

from hovorunv2.application.access.access_service import AccessService
from hovorunv2.application.configuration import AppSettings
from hovorunv2.application.messaging.message_service import MessageService
from hovorunv2.application.messaging.ports import CachePort
from hovorunv2.interface.telegram.handlers.base import BaseCommand
from hovorunv2.interface.telegram.handlers.bot_join import BotJoinHandler
from hovorunv2.interface.telegram.middlewares import (
    AccessMiddleware,
    LogContextMiddleware,
    MessageCacheMiddleware,
)
from hovorunv2.interface.telegram.transport.identity import IdentityMiddleware


def _register_middlewares(
    router: Router,
    identity_middleware: IdentityMiddleware,
    access_service: AccessService,
    message_service: MessageService,
    cache: CachePort,
    settings: AppSettings,
) -> None:
    """Attach identity, logging, access and caching middlewares to router.

    Args:
        router: Router receiving the middlewares.
        identity_middleware: Outer middleware ingesting public identity evidence.
        access_service: Access service driving authorization decisions.
        message_service: Service caching authorized messages.
        cache: Cooldown cache for access denial guidance.
        settings: Application settings consumed by the access middleware.
    """
    router.message.outer_middleware(identity_middleware)
    router.callback_query.outer_middleware(identity_middleware)
    router.my_chat_member.outer_middleware(identity_middleware)
    router.chat_member.outer_middleware(identity_middleware)

    log_context_middleware = LogContextMiddleware()
    router.message.outer_middleware(log_context_middleware)
    router.callback_query.outer_middleware(log_context_middleware)
    router.my_chat_member.outer_middleware(log_context_middleware)

    router.message.middleware(AccessMiddleware(access_service, cache, settings))
    router.callback_query.middleware(AccessMiddleware(access_service, cache, settings))
    router.message.middleware(MessageCacheMiddleware(message_service))


def _register_commands(router: Router, commands: list[BaseCommand]) -> None:
    """Register command triggers and attached callback query routes.

    Args:
        router: Router receiving the command and callback registrations.
        commands: Command handlers to register with their access policies.
    """
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


def build_router(
    commands: list[BaseCommand],
    message_service: MessageService,
    access_service: AccessService,
    settings: AppSettings,
    identity_middleware: IdentityMiddleware,
    cache: CachePort,
    bot_join_handler: BotJoinHandler | None = None,
) -> Router:
    """Build and configure the main Telegram router with middlewares and handlers.

    Args:
        commands: Command handlers to register on the router.
        message_service: Service caching authorized messages.
        access_service: Access service driving authorization decisions.
        settings: Application settings consumed by the access middleware.
        identity_middleware: Outer middleware ingesting public identity evidence.
        cache: Cooldown cache for access denial guidance.
        bot_join_handler: Optional handler for bot membership changes.

    Returns:
        Configured Telegram router.
    """
    router = Router()
    _register_middlewares(
        router,
        identity_middleware,
        access_service,
        message_service,
        cache,
        settings,
    )
    if bot_join_handler:
        bot_join_handler.register(router)
    _register_commands(router, commands)
    return router
