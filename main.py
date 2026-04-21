"""Main application entry point."""

import asyncio

from aiogram import Bot, Dispatcher

from cache import CacheService
from config import settings
from controllers.bot import router
from logger_conf import get_logger
from message_service import MessageService

logger = get_logger(__name__)


async def main() -> None:
    """Initialize app and start polling."""
    logger.info("Starting application...")

    cache_service = CacheService()
    message_service = MessageService(cache_service)
    logger.info("Services initialized")

    bot = Bot(token=settings.bot_token)
    dp = Dispatcher()
    dp.include_router(router)

    logger.info("Bot started and polling...")
    await dp.start_polling(bot, message_service=message_service)


if __name__ == "__main__":
    asyncio.run(main())
