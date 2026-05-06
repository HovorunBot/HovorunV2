"""Dependency injection configuration using dishka."""

from collections.abc import AsyncIterable

import aiohttp
from dishka import Provider, Scope, provide
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from hovorunv2.application.clients.bluesky import BlueskyService
from hovorunv2.application.clients.facebook import FacebookService
from hovorunv2.application.clients.instagram import InstagramService
from hovorunv2.application.clients.threads import ThreadsService
from hovorunv2.application.clients.tiktok import TikTokService
from hovorunv2.application.clients.twitter import TwitterService
from hovorunv2.application.data.chat_service import ChatService
from hovorunv2.application.data.command_service import CommandDataService
from hovorunv2.application.media.downloader import MediaDownloader
from hovorunv2.application.media.extractor import MediaExtractor
from hovorunv2.application.services.command_service import CommandService
from hovorunv2.application.services.language_service import LanguageService
from hovorunv2.application.services.message_service import MessageService
from hovorunv2.application.services.translation_service import TranslationService
from hovorunv2.application.services.whitelist_service import WhitelistService
from hovorunv2.infrastructure.browser import BrowserService
from hovorunv2.infrastructure.cache import CacheService
from hovorunv2.infrastructure.config import Settings
from hovorunv2.infrastructure.config import settings as app_settings
from hovorunv2.interface.telegram.handlers.base import BaseCommand, RichMediaCommand
from hovorunv2.interface.telegram.handlers.bluesky import BlueskyCommand
from hovorunv2.interface.telegram.handlers.commands_config import DisableCommand, EnableCommand
from hovorunv2.interface.telegram.handlers.debug import DebugCommand
from hovorunv2.interface.telegram.handlers.facebook import FacebookCommand
from hovorunv2.interface.telegram.handlers.instagram import InstagramCommand
from hovorunv2.interface.telegram.handlers.set_language import SetLanguageCommand
from hovorunv2.interface.telegram.handlers.threads import ThreadsCommand
from hovorunv2.interface.telegram.handlers.tiktok import TikTokCommand
from hovorunv2.interface.telegram.handlers.twitter import TwitterCommand
from hovorunv2.interface.telegram.handlers.whitelist import AllowBotCommand
from hovorunv2.interface.telegram.handlers.youtube import YoutubeShortsCommand


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
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
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


class AppProvider(Provider):
    """Provider for application business logic services."""

    scope = Scope.APP

    message_service = provide(MessageService)
    chat_service = provide(ChatService)
    command_data_service = provide(CommandDataService)
    command_service = provide(CommandService)
    whitelist_service = provide(WhitelistService)
    language_service = provide(LanguageService)
    translation_service = provide(TranslationService)
    media_downloader = provide(MediaDownloader)
    media_extractor = provide(MediaExtractor)
    tiktok_service = provide(TikTokService)
    twitter_service = provide(TwitterService)
    threads_service = provide(ThreadsService)
    instagram_service = provide(InstagramService)
    facebook_service = provide(FacebookService)
    bluesky_service = provide(BlueskyService)

    # Commands
    bluesky_command = provide(BlueskyCommand)
    facebook_command = provide(FacebookCommand)
    instagram_command = provide(InstagramCommand)
    threads_command = provide(ThreadsCommand)
    tiktok_command = provide(TikTokCommand)
    twitter_command = provide(TwitterCommand)
    youtube_command = provide(YoutubeShortsCommand)
    set_language_command = provide(SetLanguageCommand)
    debug_command = provide(DebugCommand)
    enable_cmd_command = provide(EnableCommand)
    disable_cmd_command = provide(DisableCommand)

    @provide(scope=Scope.APP)
    def get_instagram_command(
        self,
        instagram_service: InstagramService,
        media_downloader: MediaDownloader,
        media_extractor: MediaExtractor,
        browser_service: BrowserService,
        session: aiohttp.ClientSession,
        config: Settings,
    ) -> InstagramCommand:
        """Provide InstagramCommand with its dependencies."""
        return InstagramCommand(
            instagram_service=instagram_service,
            media_downloader=media_downloader,
            media_extractor=media_extractor,
            browser_service=browser_service,
            session=session,
            settings=config,
        )

    @provide(scope=Scope.APP)
    def get_rich_media_commands(
        self,
        bluesky_command: BlueskyCommand,
        facebook_command: FacebookCommand,
        instagram_command: InstagramCommand,
        threads_command: ThreadsCommand,
        tiktok_command: TikTokCommand,
        twitter_command: TwitterCommand,
        youtube_command: YoutubeShortsCommand,
    ) -> list[RichMediaCommand]:
        """Provide list of rich media commands."""
        return [
            bluesky_command,
            facebook_command,
            instagram_command,
            threads_command,
            tiktok_command,
            twitter_command,
            youtube_command,
        ]

    @provide(scope=Scope.APP)
    def get_allow_bot_command(
        self,
        whitelist_service: WhitelistService,
        command_service: CommandService,
        settings: Settings,
        rich_media_commands: list[RichMediaCommand],
    ) -> AllowBotCommand:
        """Provide AllowBotCommand with its dependencies."""
        # Note: we cast rich_media_commands to list[BaseCommand] because AllowBotCommand expects that
        return AllowBotCommand(
            whitelist_service=whitelist_service,
            command_service=command_service,
            settings=settings,
            commands=rich_media_commands,
        )

    @provide(scope=Scope.APP)
    def get_commands(
        self,
        allow_bot_command: AllowBotCommand,
        rich_media_commands: list[RichMediaCommand],
        set_language_command: SetLanguageCommand,
        debug_command: DebugCommand,
        enable_cmd_command: EnableCommand,
        disable_cmd_command: DisableCommand,
    ) -> list[BaseCommand]:
        """Provide list of all bot commands."""
        commands: list[BaseCommand] = [
            allow_bot_command,
            *rich_media_commands,
            set_language_command,
            debug_command,
            enable_cmd_command,
            disable_cmd_command,
        ]
        return commands
