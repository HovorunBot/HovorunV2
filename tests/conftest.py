"""Shared test configuration and fixtures."""

import asyncio
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import StaticPool

from hovorunv2.infrastructure.container import Container
from hovorunv2.infrastructure.database.models.chat import Base

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator


@pytest.fixture(scope="session")
def event_loop() -> asyncio.AbstractEventLoop:
    """Create an instance of the default event loop for each test case."""
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop


@pytest.fixture(scope="session")
async def test_engine() -> AsyncGenerator[AsyncEngine]:
    """Create a test database engine."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///file:testdb?mode=memory&cache=shared",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def test_container(test_engine: AsyncEngine) -> AsyncGenerator[Container]:
    """Provide a container with real services but test database/cache."""
    container = Container()
    container.engine = test_engine
    await container.init()

    with (
        patch("hovorunv2.interface.telegram.commands.whitelist.container", container),
        patch("hovorunv2.interface.telegram.commands.tiktok.container", container),
        patch("hovorunv2.interface.telegram.commands.twitter.container", container),
        patch("hovorunv2.interface.telegram.commands.threads.container", container),
        patch("hovorunv2.interface.telegram.commands.set_language.container", container),
        patch("hovorunv2.interface.telegram.commands.base.container", container),
        patch("hovorunv2.infrastructure.container.container", container),
    ):
        yield container

    await container.close()


@pytest.fixture
async def init_container(test_container: Container) -> Container:
    """Alias for test_container to keep existing tests working."""
    return test_container
