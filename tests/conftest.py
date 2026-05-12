"""Shared test configuration and fixtures."""

import asyncio
import os
import time
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
from dishka import AsyncContainer, Scope, make_async_container, provide
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from hovorunv2.domain.chat import Base
from hovorunv2.infrastructure.browser import BrowserService
from hovorunv2.infrastructure.config import Settings
from hovorunv2.infrastructure.di import AppProvider, InfrastructureProvider
from hovorunv2.infrastructure.fixtures import setup_fixtures
from tests.utils.browser_cache import TestBrowserService


@pytest.fixture
async def test_engine() -> AsyncGenerator[AsyncEngine]:
    """Create a test database engine."""
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

    @provide(scope=Scope.APP)
    async def get_browser_service(self, config: Settings) -> AsyncGenerator[BrowserService]:
        """Provide TestBrowserService (DrissionPage) with caching."""
        service = TestBrowserService(
            max_tabs=config.browser_max_tabs,
            idle_timeout=config.browser_idle_timeout,
        )
        yield service
        await service.close()


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


@pytest.fixture(autouse=True)
def manage_vcr_cassettes(request: pytest.FixtureRequest) -> None:
    """Delete cassettes older than 7 days to force re-recording."""
    # VCR cassettes with pytest-recording are stored in tests/cassettes/test_module/test_name.yaml
    vcr_marker = request.node.get_closest_marker("vcr")
    if not vcr_marker:
        return

    module_name = request.node.fspath.purebasename
    test_name = request.node.name
    cassette_path = Path("tests/cassettes") / module_name / f"{test_name}.yaml"

    if cassette_path.exists():
        mtime = os.path.getmtime(cassette_path)
        if (time.time() - mtime) > (7 * 24 * 60 * 60):  # 7 days
            os.remove(cassette_path)
