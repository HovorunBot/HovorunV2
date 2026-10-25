"""Shared async database dependency providers for bot and offline administration."""

from collections.abc import AsyncIterable
from typing import Any

from dishka import Provider, Scope, provide
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from hovorunv2.infrastructure.config import DatabaseSettings


class DatabaseProvider(Provider):
    """Own database resources without initializing bot or network resources."""

    @provide(scope=Scope.APP)
    async def get_engine(self, config: DatabaseSettings) -> AsyncIterable[AsyncEngine]:
        """Provide SQLAlchemy async engine with foreign keys enabled."""
        engine = create_async_engine(f"sqlite+aiosqlite:///{config.db_path}")

        @event.listens_for(engine.sync_engine, "connect")
        def set_sqlite_pragma(dbapi_connection: Any, _connection_record: Any) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        try:
            yield engine
        finally:
            await engine.dispose()

    @provide(scope=Scope.APP)
    def get_session_maker(self, engine: AsyncEngine) -> async_sessionmaker:
        """Provide SQLAlchemy async session maker."""
        return async_sessionmaker(engine, expire_on_commit=False)
