"""Service container for application dependencies."""

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from hovorunv2.application.services.message_service import MessageService
from hovorunv2.application.services.whitelist_service import WhitelistService
from hovorunv2.infrastructure.cache import CacheService
from hovorunv2.infrastructure.config import settings
from hovorunv2.infrastructure.database.repositories.chat_repository import SQLAlchemyChatRepository


class Container:
    """Holder for application-wide services."""

    def __init__(self) -> None:
        """Initialize container with None values."""
        self.engine = None
        self.session_maker = None
        self.cache_service = None
        self.message_service = None
        self.whitelist_service = None
        self._session = None

    async def init(self) -> None:
        """Initialize all services in correct order."""
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{settings.db_path}")
        self.session_maker = async_sessionmaker(self.engine, expire_on_commit=False)

        self.cache_service = CacheService()
        self.message_service = MessageService(self.cache_service)

        # For simple use cases, we keep one session per container instance for now
        # or services can open their own via session_maker.
        # Given current repo structure, repo needs a session.
        self._session = self.session_maker()
        chat_repository = SQLAlchemyChatRepository(self._session)
        self.whitelist_service = WhitelistService(chat_repository)


# Global instance
container = Container()
