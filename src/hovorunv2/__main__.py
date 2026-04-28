"""Main application entry point."""

import asyncio

from aiogram import Bot, Dispatcher
from dishka import make_async_container
from dishka.integrations.aiogram import setup_dishka
from sqlalchemy.ext.asyncio import async_sessionmaker

from hovorunv2.application.services.command_service import CommandService
from hovorunv2.application.services.message_service import MessageService
from hovorunv2.application.services.whitelist_service import WhitelistService
from hovorunv2.infrastructure.config import settings
from hovorunv2.infrastructure.di import AppProvider, InfrastructureProvider
from hovorunv2.infrastructure.fixtures import setup_fixtures
from hovorunv2.infrastructure.logger import get_logger
from hovorunv2.interface.telegram.bot import router, setup_handlers, setup_middlewares
from hovorunv2.interface.telegram.handlers.base import BaseCommand

logger = get_logger(__name__)


async def run_bot() -> None:
    """Initialize app and start polling."""
    logger.info("Starting application...")

    # Initialize DI container
    container = make_async_container(InfrastructureProvider(), AppProvider())

    try:
        # Populate fixtures
        session_maker = await container.get(async_sessionmaker)
        await setup_fixtures(session_maker)

        # Resolve services for middlewares
        message_service = await container.get(MessageService)
        whitelist_service = await container.get(WhitelistService)
        command_service = await container.get(CommandService)

        # Setup router middlewares and handlers
        await setup_middlewares(container, message_service, whitelist_service, command_service)

        # Resolve all registered commands and register them with the router
        commands = await container.get(list[BaseCommand])
        setup_handlers(commands)

        bot = Bot(token=settings.bot_token)
        dp = Dispatcher()
        dp.include_router(router)

        # Wire dishka with aiogram
        setup_dishka(container=container, router=dp)

        logger.info("Bot started and polling...")
        await dp.start_polling(bot)
    finally:
        await container.close()


def main() -> None:
    """Entry point for the application."""
    asyncio.run(run_bot())


if __name__ == "__main__":
    main()
