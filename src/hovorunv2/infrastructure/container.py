"""Service container for application dependencies."""

from typing import Any

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from hovorunv2.application.services.media_service import MediaService
from hovorunv2.application.services.message_service import MessageService
from hovorunv2.application.services.tiktok_service import TikTokService
from hovorunv2.application.services.translation_service import TranslationService
from hovorunv2.application.services.twitter_service import TwitterService
from hovorunv2.application.services.whitelist_service import WhitelistService
from hovorunv2.application.utils import UNDEFINED
from hovorunv2.infrastructure.cache import CacheService
from hovorunv2.infrastructure.config import settings
from hovorunv2.infrastructure.database.repositories.chat_repository import SQLAlchemyChatRepository


class Container:
    """Holder for application-wide services."""

    def __init__(self) -> None:
        """Initialize container with UNDEFINED values."""
        self.engine: Any = UNDEFINED
        self.session_maker: Any = UNDEFINED
        self.cache_service: CacheService = UNDEFINED
        self.message_service: MessageService = UNDEFINED
        self.whitelist_service: WhitelistService = UNDEFINED
        self.translation_service: TranslationService = UNDEFINED
        self.media_service: MediaService = UNDEFINED
        self.tiktok_service: TikTokService = UNDEFINED
        self.twitter_service: TwitterService = UNDEFINED

        self._session: Any = UNDEFINED
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

        self.cache_service = CacheService()
        self.message_service = MessageService(self.cache_service)
        self.media_service = MediaService()

        # Repository needs a session
        self._session = self.session_maker()
        chat_repository = SQLAlchemyChatRepository(self._session)
        self.whitelist_service = WhitelistService(chat_repository)
        self.translation_service = TranslationService(chat_repository)

        # Services with dependencies
        self.tiktok_service = TikTokService(self.translation_service)
        self.twitter_service = TwitterService(self.translation_service)

        self._is_initialized = True

    async def close(self) -> None:
        """Close all services and resources."""
        if not self._is_initialized:
            return

        if self._session is not UNDEFINED:
            await self._session.close()

        if self.cache_service is not UNDEFINED:
            await self.cache_service.close()

        self._is_initialized = False


# Global instance
container = Container()
