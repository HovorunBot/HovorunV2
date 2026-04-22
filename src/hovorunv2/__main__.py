"""Main application entry point."""

import asyncio

from aiogram import Bot, Dispatcher

from hovorunv2.cache import CacheService
from hovorunv2.config import settings
from hovorunv2.controllers.bot import router
from hovorunv2.database import DatabaseService
from hovorunv2.logger_conf import get_logger
from hovorunv2.message_service import MessageService

logger = get_logger(__name__)


async def run_bot() -> None:
    """Initialize app and start polling."""
    logger.info("Starting application...")

    cache_service = CacheService()
    database_service = DatabaseService()
    message_service = MessageService(cache_service)
    logger.info("Services initialized")

    bot = Bot(token=settings.bot_token)
    dp = Dispatcher()
    dp.include_router(router)

    logger.info("Bot started and polling...")
    await dp.start_polling(bot, message_service=message_service, database_service=database_service)


def main() -> None:
    """Entry point for the application."""
    asyncio.run(run_bot())


if __name__ == "__main__":
    main()
