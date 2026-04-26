"""Telegram bot message handlers."""

from aiogram import Router

from hovorunv2.interface.telegram.commands import get_commands
from hovorunv2.interface.telegram.commands.debug import DebugCommand
from hovorunv2.interface.telegram.commands.whitelist import AllowBotCommand
from hovorunv2.interface.telegram.middlewares import MessageCacheMiddleware, WhitelistMiddleware

router = Router()

# Register middlewares
router.message.outer_middleware(MessageCacheMiddleware())
router.message.middleware(WhitelistMiddleware())


def register_handlers() -> None:
    """Register all command handlers with the router."""
    commands = get_commands()

    for command in commands.values():
        flags = {}
        # Whitelist and Debug commands skip the whitelist check
        if isinstance(command, AllowBotCommand | DebugCommand):
            flags["bypass_whitelist"] = True

        # Register the command handler with its custom trigger filter
        router.message.register(
            command.handle,
            command.is_triggered,
            flags=flags,
        )


# Initialize handlers
register_handlers()
