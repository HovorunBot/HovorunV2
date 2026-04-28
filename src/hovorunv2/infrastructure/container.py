"""Service container for application dependencies."""

from typing import Any

import aiohttp
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

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
from hovorunv2.application.utils import UNDEFINED
from hovorunv2.infrastructure.browser import BrowserService
from hovorunv2.infrastructure.cache import CacheService
from hovorunv2.infrastructure.config import settings
from hovorunv2.infrastructure.fixtures import setup_fixtures


class Container:
    """Holder for application-wide services."""

    def __init__(self) -> None:
        """Initialize container with UNDEFINED values."""
        self.engine: Any = UNDEFINED
        self.session_maker: Any = UNDEFINED
        self.cache_service: CacheService = UNDEFINED
        self.message_service: MessageService = UNDEFINED
        self.chat_service: ChatService = UNDEFINED
        self.command_data_service: CommandDataService = UNDEFINED
        self.command_service: CommandService = UNDEFINED
        self.whitelist_service: WhitelistService = UNDEFINED
        self.language_service: LanguageService = UNDEFINED
        self.translation_service: TranslationService = UNDEFINED
        self.media_downloader: MediaDownloader = UNDEFINED
        self.tiktok_service: TikTokService = UNDEFINED
        self.twitter_service: TwitterService = UNDEFINED
        self.threads_service: ThreadsService = UNDEFINED
        self.instagram_service: InstagramService = UNDEFINED
        self.facebook_service: FacebookService = UNDEFINED
        self.bluesky_service: BlueskyService = UNDEFINED
        self.media_extractor: MediaExtractor = UNDEFINED
        self.browser_service: BrowserService = UNDEFINED
        self.http_session: aiohttp.ClientSession = UNDEFINED

        self._is_initialized: bool = False

    @property
    def is_initialized(self) -> bool:
        """Check if container is initialized."""
        return self._is_initialized

    async def init(self) -> None:
        """Initialize all services in correct order."""
        if self._is_initialized:
            return

        if self.engine is UNDEFINED:
            self.engine = create_async_engine(f"sqlite+aiosqlite:///{settings.db_path}")

        if self.session_maker is UNDEFINED:
            self.session_maker = async_sessionmaker(self.engine, expire_on_commit=False)

        self.http_session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))
        self.cache_service = CacheService()
        self.message_service = MessageService(self.cache_service)
        self.media_downloader = MediaDownloader(self.http_session)

        self.browser_service = BrowserService(
            max_tabs=settings.playwright_max_tabs,
            idle_timeout=settings.playwright_idle_timeout,
        )

        self.chat_service = ChatService(self.session_maker)
        self.command_data_service = CommandDataService(self.session_maker)
        self.command_service = CommandService(self.command_data_service)
        self.whitelist_service = WhitelistService(self.chat_service)

        # Populate fixtures
        await setup_fixtures(self.session_maker)

        self.language_service = LanguageService(self.chat_service)
        self.translation_service = TranslationService(self.language_service, self.http_session)

        self.media_extractor = MediaExtractor(translation_service=self.translation_service)
        self.tiktok_service = TikTokService(translation_service=self.translation_service)
        self.twitter_service = TwitterService(translation_service=self.translation_service)
        self.threads_service = ThreadsService(
            translation_service=self.translation_service, browser_service=self.browser_service
        )
        self.instagram_service = InstagramService(
            translation_service=self.translation_service, media_extractor=self.media_extractor
        )
        self.facebook_service = FacebookService(
            translation_service=self.translation_service, media_extractor=self.media_extractor
        )
        self.bluesky_service = BlueskyService(translation_service=self.translation_service)

        self._is_initialized = True

    async def close(self) -> None:
        """Close all services and resources."""
        if not self._is_initialized:
            return

        if self.browser_service is not UNDEFINED:
            await self.browser_service.close()

        if self.cache_service is not UNDEFINED:
            await self.cache_service.close()

        if self.http_session is not UNDEFINED:
            await self.http_session.close()

        if self.engine is not UNDEFINED:
            await self.engine.dispose()

        self._is_initialized = False


# Global instance
container = Container()
