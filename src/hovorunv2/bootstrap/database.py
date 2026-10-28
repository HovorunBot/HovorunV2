"""Shared async database dependency providers for bot and offline administration."""

from collections.abc import AsyncIterable

from dishka import Provider, Scope, provide
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from hovorunv2.infrastructure.config import DatabaseSettings


class DatabaseProvider(Provider):
    """Own database resources without initializing bot or network resources."""

    @provide(scope=Scope.APP)
    async def get_engine(self, config: DatabaseSettings) -> AsyncIterable[AsyncEngine]:
        """Own a PostgreSQL pool local to this process and event loop."""
        engine = create_async_engine(config.database_url, pool_pre_ping=True, hide_parameters=True)

        try:
            yield engine
        finally:
            await engine.dispose()

    @provide(scope=Scope.APP)
    def get_session_maker(self, engine: AsyncEngine) -> async_sessionmaker:
        """Provide SQLAlchemy async session maker."""
        return async_sessionmaker(engine, expire_on_commit=False)
