"""Main application entry point."""

import asyncio
import signal
from contextlib import suppress
from pathlib import Path
from tempfile import gettempdir

from aiogram import Bot, Dispatcher, Router
from aiogram_i18n import I18nMiddleware
from dishka import AsyncContainer, make_async_container
from dishka.integrations.aiogram import setup_dishka
from sqlalchemy.ext.asyncio import async_sessionmaker

from hovorunv2.application.access.access_service import AccessService
from hovorunv2.application.access.role_service import RoleService
from hovorunv2.application.features.beta_prompts import BetaPromptService
from hovorunv2.application.identity.membership_reconciliation import MembershipReconciliationService
from hovorunv2.application.messaging.crypto import verify_encryption_key
from hovorunv2.application.messaging.message_service import MessageService
from hovorunv2.application.messaging.ports import CachePort
from hovorunv2.application.operations.reminder_service import ChatReminderService
from hovorunv2.bootstrap.container import AppProvider
from hovorunv2.bootstrap.providers.infrastructure import InfrastructureProvider
from hovorunv2.infrastructure.config import Settings
from hovorunv2.infrastructure.fixtures import setup_fixtures
from hovorunv2.infrastructure.logger import AsyncTelegramHandler, get_logger, setup_logging, setup_telegram_logging
from hovorunv2.interface.telegram.bot import build_router
from hovorunv2.interface.telegram.handlers.base import BaseCommand
from hovorunv2.interface.telegram.handlers.bot_join import BotJoinHandler
from hovorunv2.interface.telegram.middlewares import LogContextMiddleware
from hovorunv2.interface.telegram.transport.identity import IdentityMiddleware

logger = get_logger(__name__)
BOT_READY_PATH = Path(gettempdir()) / "hovorun-bot-ready"


def _setup_signal_handlers() -> None:
    """Handle SIGTSTP (Ctrl+Z) as graceful termination alongside SIGINT/SIGTERM."""
    loop = asyncio.get_running_loop()
    current_task = asyncio.current_task()
    if current_task and hasattr(signal, "SIGTSTP"):
        with suppress(NotImplementedError, RuntimeError):
            loop.add_signal_handler(signal.SIGTSTP, current_task.cancel)


async def build_bot_router(container: AsyncContainer, settings: Settings) -> Router:
    """Resolve dependencies and build main telegram router."""
    message_service = await container.get(MessageService)
    access_service = await container.get(AccessService)
    commands = await container.get(list[BaseCommand])
    bot_join_handler = await container.get(BotJoinHandler)
    identity_middleware = await container.get(IdentityMiddleware)
    cache = await container.get(CachePort)
    reminders = await container.get(ChatReminderService)
    reconciliation = await container.get(MembershipReconciliationService)
    return build_router(
        commands=commands,
        message_service=message_service,
        access_service=access_service,
        settings=settings,
        identity_middleware=identity_middleware,
        cache=cache,
        reminders=reminders,
        reconciliation=reconciliation,
        bot_join_handler=bot_join_handler,
    )


def configure_dispatcher(
    container: AsyncContainer,
    router: Router,
    i18n_middleware: I18nMiddleware | None = None,
) -> Dispatcher:
    """Configure aiogram dispatcher with outer middleware and DI wireup."""
    dp = Dispatcher()
    dp.update.outer_middleware(LogContextMiddleware())
    if i18n_middleware is not None:
        i18n_middleware.setup(dispatcher=dp)
    dp.include_router(router)
    setup_dishka(container=container, router=dp)
    return dp


async def teardown_services(
    telegram_log_handler: AsyncTelegramHandler | None,
    container: AsyncContainer,
) -> None:
    """Ensure complete cleanup of logging handler and DI container."""
    logger.info("Tearing down application services...")
    if telegram_log_handler:
        await telegram_log_handler.shutdown()
    await container.close()
    logger.info("Application teardown complete.")


async def run_bot() -> None:
    """Initialize app and start polling."""
    await asyncio.to_thread(BOT_READY_PATH.unlink, missing_ok=True)
    setup_logging()
    logger.info("Starting application...")

    container = make_async_container(InfrastructureProvider(), AppProvider())
    telegram_log_handler: AsyncTelegramHandler | None = None

    try:
        session_maker = await container.get(async_sessionmaker)
        logger.info("Setting up database fixtures...")
        await setup_fixtures(session_maker)

        settings = await container.get(Settings)
        verify_encryption_key(settings.cache_encryption_key)
        bot = await container.get(Bot)
        logger.info("Checking new beta announcements...")
        await (await container.get(BetaPromptService)).announce_new()

        logger.info("Building Telegram router and dispatcher...")
        router = await build_bot_router(container, settings)
        i18n_middleware = await container.get(I18nMiddleware)
        dp = configure_dispatcher(container, router, i18n_middleware)

        if settings.error_dm_enabled:
            telegram_log_handler = setup_telegram_logging(bot, await container.get(RoleService))

        _setup_signal_handlers()

        await bot.me()
        logger.info("Refreshing membership evidence before polling...")
        await (await container.get(MembershipReconciliationService)).run_cycle()
        await asyncio.to_thread(BOT_READY_PATH.touch)
        logger.info("Bot started and polling...")
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    except asyncio.CancelledError:
        logger.info("Bot shutdown requested via signal.")
    finally:
        try:
            await asyncio.to_thread(BOT_READY_PATH.unlink, missing_ok=True)
        finally:
            await teardown_services(telegram_log_handler, container)


def main() -> None:
    """Entry point for the application."""
    with suppress(KeyboardInterrupt):
        asyncio.run(run_bot())


if __name__ == "__main__":
    main()
