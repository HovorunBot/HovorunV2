"""Shared test configuration and fixtures."""

import ast
import os
import time
import urllib.parse
from collections.abc import AsyncGenerator, Generator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
import redis
from aiogram import Bot
from dishka import AsyncContainer, Scope, make_async_container, provide
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import hovorunv2.domain  # noqa: F401
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
from tests.utils.browser_cache import CACHE_EXPIRY_SECONDS, TestBrowserService
from tests.utils.postgres import DEFAULT_TEST_DATABASE_URL, postgres_database
from tests.utils.telegram import BOT_CREDENTIAL, create_mock_message, create_private_media_message
from tests.utils.telegram.session import RecordingSession

__all__ = [
    "DEFAULT_TEST_VALKEY_URL",
    "create_mock_message",
    "create_private_media_message",
    "get_media_command",
    "provision_role",
    "resolve_worker_valkey_url",
    "worker_postgres_schema",
    "worker_valkey_url",
]


def pytest_sessionstart(session: pytest.Session) -> None:
    """Verify that test modules never import from other test modules."""
    _check_no_cross_test_imports()


def _node_violation(file_name: str, node: ast.AST) -> str | None:
    if (
        isinstance(node, ast.ImportFrom)
        and node.module
        and (node.module.startswith("tests.test_") or ".test_" in node.module)
    ):
        return f"{file_name}:{node.lineno}: from {node.module} import ..."
    if isinstance(node, ast.Import):
        for alias in node.names:
            if alias.name.startswith("tests.test_") or ".test_" in alias.name:
                return f"{file_name}:{node.lineno}: import {alias.name}"
    return None


def _file_violations(py_file: Path) -> list[str]:
    try:
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
    except SyntaxError:
        return []
    violations: list[str] = []
    for node in ast.walk(tree):
        violation = _node_violation(py_file.name, node)
        if violation is not None:
            violations.append(violation)
    return violations


def _check_no_cross_test_imports(tests_root: Path | None = None) -> None:
    root = tests_root if tests_root is not None else Path(__file__).resolve().parent
    violations = [v for py_file in root.rglob("*.py") for v in _file_violations(py_file)]
    if violations:
        msg = "Forbidden cross-test imports detected:\n" + "\n".join(violations)
        raise pytest.UsageError(msg)


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


DEFAULT_TEST_VALKEY_URL: str = "redis://localhost:6379/1"


def resolve_worker_valkey_url(worker_id: str, base_url: str | None = None) -> str:
    """Resolve an isolated Valkey database URL for an xdist worker or serial run.

    Databases 1-15 are reserved for testing. Database 0 is strictly forbidden to protect
    developer and application state.
    """
    raw_base = base_url if base_url is not None else os.environ.get("TEST_VALKEY_URL", DEFAULT_TEST_VALKEY_URL)
    parsed = urllib.parse.urlparse(raw_base)
    db_str = parsed.path.lstrip("/")
    base_db = int(db_str) if db_str.isdigit() else 1

    worker_offset = int(worker_id[2:]) if worker_id.startswith("gw") else 0
    target_db = base_db + worker_offset

    if target_db < 1 or target_db > 15:
        msg = f"Valkey test database index {target_db} out of allowed test range (1-15). DB 0 is strictly forbidden."
        raise ValueError(msg)

    new_path = f"/{target_db}"
    return urllib.parse.urlunparse(parsed._replace(path=new_path))


@pytest.fixture(scope="session")
def worker_valkey_url(request: pytest.FixtureRequest) -> Generator[str]:
    """Isolate real Valkey storage per parallel test worker away from DB 0."""
    worker_id = getattr(request.config, "workerinput", {}).get("workerid", "master")
    valkey_url = resolve_worker_valkey_url(worker_id)
    os.environ["VALKEY_URL"] = valkey_url
    client = redis.from_url(valkey_url)
    try:
        client.flushdb()
    finally:
        client.close()
    yield valkey_url
    client = redis.from_url(valkey_url)
    try:
        client.flushdb()
    finally:
        client.close()


TRUNCATE_TABLES_SQL = text(
    "TRUNCATE TABLE "
    + ", ".join(f'"{table.name}"' for table in Base.metadata.sorted_tables)
    + " RESTART IDENTITY CASCADE"
)


@pytest.fixture(scope="session")
def worker_postgres_schema(request: pytest.FixtureRequest) -> Generator[str]:
    """Create one isolated real PostgreSQL schema per xdist worker for the test session."""
    worker_id = getattr(request.config, "workerinput", {}).get("workerid", "master")
    base_url = make_url(os.environ.get("TEST_DATABASE_URL", DEFAULT_TEST_DATABASE_URL))
    schema = f"test_worker_{worker_id}_{uuid4().hex[:8]}"

    admin_engine = create_engine(base_url.render_as_string(hide_password=False), poolclass=NullPool)
    with admin_engine.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))

    scoped_url = base_url.update_query_dict({"options": f"-csearch_path={schema}"})
    scoped_url_str = scoped_url.render_as_string(hide_password=False)

    worker_engine = create_engine(scoped_url_str, poolclass=NullPool)
    with worker_engine.begin() as conn:
        Base.metadata.create_all(conn)
    worker_engine.dispose()

    yield scoped_url_str

    with admin_engine.begin() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
    admin_engine.dispose()


@pytest.fixture
async def postgres_database_url(
    request: pytest.FixtureRequest,
    worker_postgres_schema: str,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncGenerator[str]:
    """Provide isolated PostgreSQL URL: fresh schema for @pytest.mark.fresh_schema, else worker schema."""
    if request.node.get_closest_marker("fresh_schema"):
        async with postgres_database() as fresh_url:
            monkeypatch.setenv("DATABASE_URL", fresh_url)
            yield fresh_url
    else:
        monkeypatch.setenv("DATABASE_URL", worker_postgres_schema)
        yield worker_postgres_schema


@pytest.fixture
async def test_engine(
    request: pytest.FixtureRequest,
    postgres_database_url: str,
) -> AsyncGenerator[AsyncEngine]:
    """Use PostgreSQL constraints and independent pooled connections in application tests."""
    engine = create_async_engine(postgres_database_url, hide_parameters=True)
    try:
        if request.node.get_closest_marker("fresh_schema"):
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
        else:
            async with engine.begin() as connection:
                await connection.execute(TRUNCATE_TABLES_SQL)
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


@pytest.fixture(scope="module")
def vcr_config() -> dict[str, Any]:
    """Configure pytest-recording to record once when cassette is missing."""
    return {"record_mode": "once"}


@pytest.fixture(autouse=True)
def manage_vcr_cassettes(request: pytest.FixtureRequest) -> None:
    """Delete cassettes older than 30 days to force re-recording."""
    # VCR cassettes with pytest-recording are stored in tests/cassettes/test_module/test_name.yaml
    vcr_marker = request.node.get_closest_marker("vcr")
    if not vcr_marker:
        return

    module_name = request.node.fspath.purebasename
    test_name = request.node.name
    cassette_path = Path(__file__).resolve().parent / "cassettes" / module_name / f"{test_name}.yaml"

    if cassette_path.exists():
        mtime = cassette_path.stat().st_mtime
        if (time.time() - mtime) > CACHE_EXPIRY_SECONDS:
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


async def authorize_private_media(container: AsyncContainer, telegram_id: int) -> None:
    """Seed actual private-access identity and trust before exercising extraction."""
    users = await container.get(UserStore)
    await users.ingest_user(telegram_id)
    trust = await container.get(TrustService)
    await trust.set_trust(str(telegram_id), is_trusted=True, actor=ActorContext.cli())


@pytest.fixture
async def recording_bot() -> AsyncGenerator[Bot]:
    """Use real Telegram models with a recording external transport."""
    async with Bot(BOT_CREDENTIAL, session=RecordingSession()) as bot:
        yield bot


@pytest.fixture(autouse=True)
def require_test_valkey(request: pytest.FixtureRequest) -> None:
    """Initialize worker storage only for tests allowed to use infrastructure."""
    if request.node.get_closest_marker("unit") is None:
        request.getfixturevalue("worker_valkey_url")


@pytest.fixture(scope="module")
def vcr_cassette_dir(request: pytest.FixtureRequest) -> str:
    """Keep provider cassette paths stable across architecture directories."""
    return str(Path(__file__).resolve().parent / "cassettes" / request.path.stem)


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Mark pure tests and reject infrastructure fixture dependencies before execution."""
    pure_modules = {
        "test_provider_parsing",
        "test_http_responses",
        "test_changelog",
        "test_telegram_delivery_contracts",
        "test_media_finalization",
        "test_suite_hygiene",
        "test_threads_metrics_extraction",
    }
    forbidden = {
        "test_container",
        "test_engine",
        "postgres_database_url",
        "worker_postgres_schema",
        "worker_valkey_url",
    }
    for item in items:
        if "domain" in item.path.parts or item.path.stem in pure_modules:
            item.add_marker(pytest.mark.unit)
        if item.get_closest_marker("unit") is None:
            continue
        dependencies = forbidden.intersection(item.fixturenames)
        if dependencies:
            msg = f"Unit test {item.nodeid} requests infrastructure fixtures: {sorted(dependencies)}"
            raise pytest.UsageError(msg)
