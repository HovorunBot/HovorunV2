"""Shared test configuration and fixtures."""

import time
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.types import Chat, Message, User
from dishka import AsyncContainer, Scope, make_async_container, provide
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from hovorunv2.application.membership import AdministratorEvidence
from hovorunv2.application.membership_store import MembershipStore
from hovorunv2.application.ports import ChatStore, UserStore
from hovorunv2.application.services.role_service import RoleService
from hovorunv2.application.services.trust_service import TrustService
from hovorunv2.bootstrap.container import AppProvider, InfrastructureProvider
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.base import Base
from hovorunv2.domain.enums import CommandName, Role
from hovorunv2.infrastructure.browser import BrowserService
from hovorunv2.infrastructure.config import Settings
from hovorunv2.infrastructure.fixtures import setup_fixtures
from hovorunv2.interface.telegram.handlers.base import RichMediaCommand
from tests.utils.browser_cache import TestBrowserService
from tests.utils.postgres import postgres_database


async def provision_role(container: AsyncContainer, user_id: int = 101, role: Role = Role.ADMIN) -> int:
    """Provision synthetic test authority through the audited application service."""
    roles = await container.get(RoleService)
    await roles.set_role(user_id, role, actor=ActorContext.cli())
    return user_id


async def get_media_command(container: AsyncContainer, name: CommandName) -> RichMediaCommand:
    """Resolve a RichMediaCommand instance for a specific provider from the DI container."""
    commands = await container.get(list[RichMediaCommand])
    for cmd in commands:
        if cmd.name == str(name):
            return cmd
    msg = f"RichMediaCommand for {name} not found in container"
    raise LookupError(msg)


def create_mock_message(text: str | None, *, is_bot: bool = False, chat_id: int = 456, user_id: int = 123) -> MagicMock:
    """Create a standardized mock Telegram message for command tests."""
    message = MagicMock(spec=Message)
    message.text = text
    message.message_id = 12345
    message.from_user = MagicMock(spec=User)
    message.from_user.id = user_id
    message.from_user.is_bot = is_bot
    message.sender_chat = None
    message.from_user.full_name = "Mock User"
    message.from_user.username = "mockuser"
    message.chat = MagicMock(spec=Chat)
    message.chat.id = chat_id
    message.chat.type = "group"
    message.chat.title = "Mock Group"
    message.answer = AsyncMock()
    message.delete = AsyncMock()
    message.__class__ = Message
    return message


@pytest.fixture
async def postgres_database_url(monkeypatch: pytest.MonkeyPatch) -> AsyncGenerator[str]:
    """Point every application container at one isolated empty real PostgreSQL schema."""
    async with postgres_database() as url:
        monkeypatch.setenv("DATABASE_URL", url)
        yield url


@pytest.fixture
async def test_engine(postgres_database_url: str) -> AsyncGenerator[AsyncEngine]:
    """Use PostgreSQL constraints and independent pooled connections in application tests."""
    engine = create_async_engine(postgres_database_url, hide_parameters=True)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        yield engine
    finally:
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


@pytest.fixture(scope="module")
def vcr_config() -> dict[str, Any]:
    """Configure pytest-recording to record once when cassette is missing."""
    return {"record_mode": "once"}


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
        mtime = cassette_path.stat().st_mtime
        if (time.time() - mtime) > (7 * 24 * 60 * 60):  # 7 days
            cassette_path.unlink()


async def verify_group_admins(container: AsyncContainer, telegram_id: int) -> None:
    """Explicitly provide a fresh empty administrator snapshot before member events."""
    chats = await container.get(ChatStore)
    group = await chats.get_or_create_chat(telegram_id)
    memberships = await container.get(MembershipStore)
    now = datetime.now(UTC) - timedelta(minutes=3)
    await memberships.save_admins(
        group.id, AdministratorEvidence(telegram_id, True, (), now, now + timedelta(minutes=10)), ActorContext.cli()
    )


def create_private_media_message(text: str | None, *, chat_id: int = 456, is_bot: bool = False) -> MagicMock:
    """Use a consistent private Telegram transport and matching actor identity."""
    message = create_mock_message(text, chat_id=chat_id, user_id=chat_id, is_bot=is_bot)
    message.chat.type = "private"
    return message


async def authorize_private_media(container: AsyncContainer, telegram_id: int) -> None:
    """Seed actual private-access identity and trust before exercising extraction."""
    users = await container.get(UserStore)
    await users.ingest_user(telegram_id)
    trust = await container.get(TrustService)
    await trust.set_trust(str(telegram_id), is_trusted=True, actor=ActorContext.cli())
