"""Main application entry point."""

import asyncio

from aiogram import Bot, Dispatcher

from hovorunv2.infrastructure.config import settings
from hovorunv2.infrastructure.container import container
from hovorunv2.infrastructure.database.models.chat import Base
from hovorunv2.infrastructure.logger import get_logger
from hovorunv2.interface.telegram.bot import router

logger = get_logger(__name__)


async def run_bot() -> None:
    """Initialize app and start polling."""
    logger.info("Starting application...")

    # Initialize container
    await container.init()

    # Initialize database tables
    assert container.engine is not None, "Database engine is not initialized"
    async with container.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database schema created")

    bot = Bot(token=settings.bot_token)
    dp = Dispatcher()
    dp.include_router(router)

    logger.info("Bot started and polling...")
    await dp.start_polling(bot)


def main() -> None:
    """Entry point for the application."""
    asyncio.run(run_bot())


if __name__ == "__main__":
    main()
