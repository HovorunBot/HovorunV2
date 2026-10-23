"""Dependency injection configuration using dishka."""

from collections.abc import AsyncIterable
from typing import NewType

import aiohttp
from dishka import Provider, Scope, alias, provide
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from hovorunv2.application.clients.base import FACEBOOK_CONFIG, BaseMediaClient
from hovorunv2.application.clients.bluesky import BlueskyService
from hovorunv2.application.clients.instagram import InstagramService
from hovorunv2.application.clients.reddit import RedditService
from hovorunv2.application.clients.threads import ThreadsService
from hovorunv2.application.clients.tiktok import TikTokService
from hovorunv2.application.clients.twitter import TwitterService
from hovorunv2.application.clients.youtube import YoutubeService
from hovorunv2.application.media.downloader import MediaDownloader
from hovorunv2.application.media.extractor import MediaExtractor
from hovorunv2.application.ports import (
    AppSettings,
    BrowserPort,
    CachePort,
    ChatStore,
    CommandStore,
    SystemStore,
    TaskQueuePort,
    UnauthorizedPromptSender,
)
from hovorunv2.application.services.access_service import AccessService
from hovorunv2.application.services.chat_status_service import ChatStatusService
from hovorunv2.application.services.cleanup_service import CleanupService
from hovorunv2.application.services.command_service import CommandService
from hovorunv2.application.services.feedback_service import FeedbackService
from hovorunv2.application.services.language_service import LanguageService
from hovorunv2.application.services.message_service import MessageService
from hovorunv2.application.services.notification_service import NotificationService
from hovorunv2.application.services.reminder_service import ChatReminderService
from hovorunv2.application.services.translation_providers import (
    BaseTranslationService,
    GoogleTranslationService,
)
from hovorunv2.application.services.translation_service import TranslationService
from hovorunv2.infrastructure.browser import BrowserService
from hovorunv2.infrastructure.cache import CacheService
from hovorunv2.infrastructure.config import Settings
from hovorunv2.infrastructure.config import settings as app_settings
from hovorunv2.infrastructure.persistence import (
    SQLAlchemyChatStore,
    SQLAlchemyCommandStore,
    SQLAlchemySystemStore,
)
from hovorunv2.infrastructure.queue import TaskQueueService
from hovorunv2.interface.telegram.handlers.access import AccessCommand
from hovorunv2.interface.telegram.handlers.base import BaseCommand, RichMediaCommand
from hovorunv2.interface.telegram.handlers.bot_join import BotJoinHandler
from hovorunv2.interface.telegram.handlers.debug import DebugCommand
from hovorunv2.interface.telegram.handlers.feedback import FeedbackCommand
from hovorunv2.interface.telegram.handlers.help import HelpCommand
from hovorunv2.interface.telegram.handlers.settings import SettingsCommand
from hovorunv2.interface.telegram.prompts import TelegramUnauthorizedPromptSender

UtilityCommands = NewType("UtilityCommands", list[BaseCommand])


class InfrastructureProvider(Provider):
    """Provider for core infrastructure services."""

    @provide(scope=Scope.APP)
    def get_settings(self) -> Settings:
        """Provide application settings."""
        return app_settings

    @provide(scope=Scope.APP)
    async def get_engine(self, config: Settings) -> AsyncIterable[AsyncEngine]:
        """Provide SQLAlchemy async engine."""
        engine = create_async_engine(f"sqlite+aiosqlite:///{config.db_path}")
        yield engine
        await engine.dispose()

    @provide(scope=Scope.APP)
    def get_session_maker(self, engine: AsyncEngine) -> async_sessionmaker:
        """Provide SQLAlchemy async session maker."""
        return async_sessionmaker(engine, expire_on_commit=False)

    @provide(scope=Scope.APP)
    async def get_http_session(self) -> AsyncIterable[aiohttp.ClientSession]:
        """Provide aiohttp ClientSession."""
        async with aiohttp.ClientSession() as session:
            yield session

    @provide(scope=Scope.APP)
    async def get_cache_service(self) -> AsyncIterable[CacheService]:
        """Provide CacheService (Valkey)."""
        service = CacheService()
        yield service
        await service.close()

    @provide(scope=Scope.APP)
    async def get_browser_service(self, config: Settings) -> AsyncIterable[BrowserService]:
        """Provide BrowserService (DrissionPage)."""
        service = BrowserService(
            max_tabs=config.browser_max_tabs,
            idle_timeout=config.browser_idle_timeout,
        )
        yield service
        await service.close()

    @provide(scope=Scope.APP)
    async def get_task_queue_service(self, config: Settings) -> AsyncIterable[TaskQueueService]:
        """Provide TaskQueueService (SAQ)."""
        service = TaskQueueService(config)
        yield service
        await service.stop_worker()

    settings_port = alias(source=Settings, provides=AppSettings)
    cache_port = alias(source=CacheService, provides=CachePort)
    browser_port = alias(source=BrowserService, provides=BrowserPort)
    task_queue_port = alias(source=TaskQueueService, provides=TaskQueuePort)


class AppProvider(Provider):
    """Provider for application business logic services."""

    scope = Scope.APP

    chat_store_impl = provide(SQLAlchemyChatStore)
    command_store_impl = provide(SQLAlchemyCommandStore)
    system_store_impl = provide(SQLAlchemySystemStore)

    chat_store = alias(source=SQLAlchemyChatStore, provides=ChatStore)
    command_store = alias(source=SQLAlchemyCommandStore, provides=CommandStore)
    system_store = alias(source=SQLAlchemySystemStore, provides=SystemStore)

    prompt_sender_impl = provide(TelegramUnauthorizedPromptSender)
    prompt_sender = alias(source=TelegramUnauthorizedPromptSender, provides=UnauthorizedPromptSender)

    message_service = provide(MessageService)
    notification_service = provide(NotificationService)
    reminder_service = provide(ChatReminderService)
    feedback_service = provide(FeedbackService)
    command_service = provide(CommandService)
    chat_status_service = provide(ChatStatusService)
    access_service = provide(AccessService)
    cleanup_service = provide(CleanupService)
    language_service = provide(LanguageService)
    translation_provider = provide(GoogleTranslationService, provides=BaseTranslationService)
    translation_service = provide(TranslationService)
    media_downloader = provide(MediaDownloader)
    media_extractor = provide(MediaExtractor)
    tiktok_service = provide(TikTokService)
    twitter_service = provide(TwitterService)
    threads_service = provide(ThreadsService)
    instagram_service = provide(InstagramService)
    bluesky_service = provide(BlueskyService)
    youtube_service = provide(YoutubeService)
    reddit_service = provide(RedditService)

    # Utility Commands & Handlers
    help_command = provide(HelpCommand)
    feedback_command = provide(FeedbackCommand)
    settings_command = provide(SettingsCommand)
    bot_join_handler = provide(BotJoinHandler)
    debug_command = provide(DebugCommand)

    @provide(scope=Scope.APP)
    def get_media_clients(
        self,
        translation_service: TranslationService,
        bluesky_service: BlueskyService,
        instagram_service: InstagramService,
        threads_service: ThreadsService,
        tiktok_service: TikTokService,
        twitter_service: TwitterService,
        youtube_service: YoutubeService,
        reddit_service: RedditService,
    ) -> list[BaseMediaClient]:
        """Provide all registered media provider clients."""
        return [
            bluesky_service,
            BaseMediaClient(translation_service).with_config(FACEBOOK_CONFIG),
            instagram_service,
            threads_service,
            tiktok_service,
            twitter_service,
            youtube_service,
            reddit_service,
        ]

    @provide(scope=Scope.APP)
    def get_rich_media_commands(
        self,
        media_clients: list[BaseMediaClient],
        media_downloader: MediaDownloader,
        media_extractor: MediaExtractor,
        session: aiohttp.ClientSession,
        command_service: CommandService,
    ) -> list[RichMediaCommand]:
        """Wrap all registered media clients into generalized RichMediaCommand handlers."""
        return [
            RichMediaCommand(
                primary_client=client,
                media_downloader=media_downloader,
                media_extractor=media_extractor,
                session=session,
                command_service=command_service,
            )
            for client in media_clients
        ]

    @provide(scope=Scope.APP)
    def get_access_command(
        self,
        chat_status_service: ChatStatusService,
        command_service: CommandService,
        access_service: AccessService,
        settings: Settings,
        rich_media_commands: list[RichMediaCommand],
    ) -> AccessCommand:
        """Provide AccessCommand with its dependencies."""
        return AccessCommand(
            chat_status_service=chat_status_service,
            command_service=command_service,
            access_service=access_service,
            settings=settings,
            commands=rich_media_commands,
        )

    @provide(scope=Scope.APP)
    def get_utility_commands(
        self,
        help_command: HelpCommand,
        feedback_command: FeedbackCommand,
        settings_command: SettingsCommand,
        debug_command: DebugCommand,
    ) -> UtilityCommands:
        """Provide list of utility/infrastructure commands."""
        return UtilityCommands(
            [
                help_command,
                feedback_command,
                settings_command,
                debug_command,
            ]
        )

    @provide(scope=Scope.APP)
    def get_commands(
        self,
        access_command: AccessCommand,
        rich_media_commands: list[RichMediaCommand],
        utility_commands: UtilityCommands,
    ) -> list[BaseCommand]:
        """Provide list of all bot commands."""
        return [
            access_command,
            *rich_media_commands,
            *utility_commands,
        ]
