"""Main application entry point."""

import asyncio
import signal
from contextlib import suppress

from aiogram import Bot, Dispatcher
from dishka import make_async_container
from dishka.integrations.aiogram import setup_dishka
from sqlalchemy.ext.asyncio import async_sessionmaker

from hovorunv2.application.crypto import verify_encryption_key
from hovorunv2.application.services.access_service import AccessService
from hovorunv2.application.services.cleanup_service import CleanupService
from hovorunv2.application.services.membership_reconciliation import MembershipReconciliationService
from hovorunv2.application.services.message_service import MessageService
from hovorunv2.application.services.notification_service import NotificationService
from hovorunv2.application.services.reminder_service import ChatReminderService
from hovorunv2.application.services.role_service import RoleService
from hovorunv2.bootstrap.container import AppProvider, InfrastructureProvider
from hovorunv2.infrastructure.config import Settings
from hovorunv2.infrastructure.fixtures import setup_fixtures
from hovorunv2.infrastructure.logger import AsyncTelegramHandler, get_logger, setup_logging, setup_telegram_logging
from hovorunv2.infrastructure.queue import TaskQueueService
from hovorunv2.interface.telegram.bot import build_router
from hovorunv2.interface.telegram.handlers.base import BaseCommand
from hovorunv2.interface.telegram.handlers.bot_join import BotJoinHandler
from hovorunv2.interface.telegram.identity import IdentityMiddleware
from hovorunv2.interface.telegram.middlewares import LogContextMiddleware

logger = get_logger(__name__)


def _setup_signal_handlers() -> None:
    """Handle SIGTSTP (Ctrl+Z) as graceful termination alongside SIGINT/SIGTERM."""
    loop = asyncio.get_running_loop()
    current_task = asyncio.current_task()
    if current_task and hasattr(signal, "SIGTSTP"):
        with suppress(NotImplementedError, RuntimeError):
            loop.add_signal_handler(signal.SIGTSTP, current_task.cancel)


async def run_bot() -> None:
    """Initialize app and start polling."""
    setup_logging()
    logger.info("Starting application...")

    # Initialize DI container
    container = make_async_container(InfrastructureProvider(), AppProvider())
    bot: Bot | None = None
    cleanup_service: CleanupService | None = None
    task_queue_service: TaskQueueService | None = None
    telegram_log_handler: AsyncTelegramHandler | None = None

    try:
        # Populate fixtures
        session_maker = await container.get(async_sessionmaker)
        await setup_fixtures(session_maker)

        # Resolve configuration and services
        settings = await container.get(Settings)
        verify_encryption_key(settings.cache_encryption_key)
        message_service = await container.get(MessageService)
        access_service = await container.get(AccessService)
        commands = await container.get(list[BaseCommand])
        bot_join_handler = await container.get(BotJoinHandler)
        bot = await container.get(Bot)

        router = build_router(
            commands=commands,
            message_service=message_service,
            access_service=access_service,
            settings=settings,
            identity_middleware=await container.get(IdentityMiddleware),
            bot_join_handler=bot_join_handler,
        )

        # Start persistent cleanup loop
        cleanup_service = await container.get(CleanupService)
        await cleanup_service.start_cleanup_loop(bot)

        # Start generic background task queue worker
        task_queue_service = await container.get(TaskQueueService)
        reminder_service = await container.get(ChatReminderService)
        reminder_service.register_jobs()
        membership_reconciliation = await container.get(MembershipReconciliationService)
        membership_reconciliation.register_jobs()
        await membership_reconciliation.run_cycle()
        await task_queue_service.start_worker(
            context={
                "bot": bot,
                "reminder_service": reminder_service,
                "membership_reconciliation": membership_reconciliation,
            }
        )

        # Start polling

        # Send update notifications if any
        notification_service = await container.get(NotificationService)
        await notification_service.notify_updates(bot)

        dp = Dispatcher()
        dp.update.outer_middleware(LogContextMiddleware())
        dp.include_router(router)

        # Wire dishka with aiogram
        setup_dishka(container=container, router=dp)

        # Initialize improved logging with Telegram DM handler
        if settings.error_dm_enabled:
            telegram_log_handler = setup_telegram_logging(bot, await container.get(RoleService))

        # Handle SIGTSTP (Ctrl+Z) as graceful termination alongside SIGINT/SIGTERM
        _setup_signal_handlers()

        logger.info("Bot started and polling...")
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    except asyncio.CancelledError:
        logger.info("Bot shutdown requested via signal.")
    finally:
        if task_queue_service:
            await task_queue_service.stop_worker()
        if cleanup_service:
            await cleanup_service.stop_cleanup_loop()
        if telegram_log_handler:
            await telegram_log_handler.shutdown()
        await container.close()


def main() -> None:
    """Entry point for the application."""
    with suppress(KeyboardInterrupt):
        asyncio.run(run_bot())


if __name__ == "__main__":
    main()
