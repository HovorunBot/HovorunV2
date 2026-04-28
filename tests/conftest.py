"""Shared test configuration and fixtures."""

import asyncio
from collections.abc import AsyncGenerator

import pytest
from dishka import AsyncContainer, Scope, make_async_container, provide
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from hovorunv2.domain.chat import Base
from hovorunv2.infrastructure.di import AppProvider, InfrastructureProvider
from hovorunv2.infrastructure.fixtures import setup_fixtures


@pytest.fixture(scope="session")
def event_loop() -> asyncio.AbstractEventLoop:
    """Create an instance of the default event loop for each test case."""
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop


@pytest.fixture
async def test_engine() -> AsyncGenerator[AsyncEngine]:
    """Create a test database engine."""
    # Use unique name for each test if using shared cache, or just use pure memory
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


class TestInfrastructureProvider(InfrastructureProvider):
    """Infrastructure provider for tests."""

    def __init__(self, engine: AsyncEngine) -> None:
        super().__init__()
        self._engine = engine

    @provide(scope=Scope.APP)
    async def get_engine(self) -> AsyncEngine:
        """Provide test engine."""
        return self._engine


@pytest.fixture
async def test_container(test_engine: AsyncEngine) -> AsyncGenerator[AsyncContainer]:
    """Provide a dishka container for tests."""
    container = make_async_container(TestInfrastructureProvider(test_engine), AppProvider())

    # Setup fixtures
    session_maker = await container.get(async_sessionmaker)
    await setup_fixtures(session_maker)

    yield container
    await container.close()


@pytest.fixture
async def init_container(test_container: AsyncContainer) -> AsyncContainer:
    """Alias for test_container to keep existing tests working."""
    return test_container
