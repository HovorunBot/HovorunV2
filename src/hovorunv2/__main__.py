"""Main application entry point."""

import asyncio

from aiogram import Bot, Dispatcher
from dishka import make_async_container
from dishka.integrations.aiogram import setup_dishka
from sqlalchemy.ext.asyncio import async_sessionmaker

from hovorunv2.application.services.access_service import AccessService
from hovorunv2.application.services.message_service import MessageService
from hovorunv2.application.services.notification_service import NotificationService
from hovorunv2.infrastructure.config import settings
from hovorunv2.infrastructure.di import AppProvider, InfrastructureProvider
from hovorunv2.infrastructure.fixtures import setup_fixtures
from hovorunv2.infrastructure.logger import get_logger, setup_logging
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
        access_service = await container.get(AccessService)

        # Setup router middlewares and handlers
        await setup_middlewares(container, message_service, access_service)

        # Resolve all registered commands and register them with the router
        commands = await container.get(list[BaseCommand])
        setup_handlers(commands)

        bot = Bot(token=settings.bot_token)

        # Send update notifications if any
        notification_service = await container.get(NotificationService)
        await notification_service.notify_updates(bot)

        dp = Dispatcher()
        dp.include_router(router)

        # Wire dishka with aiogram
        setup_dishka(container=container, router=dp)

        # Initialize improved logging with Telegram DM handler
        if settings.error_dm_enabled and settings.owners:
            logger.info("Enabling Error DM logging to owners: %s", settings.owners)
            setup_logging(bot=bot, owner_ids=settings.owners)

        logger.info("Bot started and polling...")
        await dp.start_polling(bot)
    finally:
        await container.close()


def main() -> None:
    """Entry point for the application."""
    asyncio.run(run_bot())


if __name__ == "__main__":
    main()
