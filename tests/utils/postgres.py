"""Isolated real PostgreSQL schemas; never truncate or drop a caller's database."""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

DEFAULT_TEST_DATABASE_URL = "postgresql+psycopg://hovorun:hovorun@localhost:5432/hovorun"


@asynccontextmanager
async def postgres_database() -> AsyncIterator[str]:
    """Create and drop only a fresh random schema, including when migration or tests fail."""
    url = make_url(os.environ.get("TEST_DATABASE_URL", DEFAULT_TEST_DATABASE_URL))
    schema = "test_" + uuid4().hex
    admin = create_async_engine(url, poolclass=NullPool)
    try:
        async with admin.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        scoped = url.update_query_dict({"options": "-csearch_path=" + schema})
        yield scoped.render_as_string(hide_password=False)
    finally:
        async with admin.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin.dispose()


async def reject_audit(session: AsyncSession, name: str, condition: str, message: str) -> None:
    """Install a real conditional PostgreSQL audit failure using only fixed synthetic test SQL."""
    identifier = session.get_bind().dialect.identifier_preparer.quote(name)
    literal = message.replace("'", "''")
    await session.execute(
        text(
            f"CREATE OR REPLACE FUNCTION {identifier}() RETURNS trigger LANGUAGE plpgsql AS $test$ "
            f"BEGIN RAISE EXCEPTION '{literal}' USING ERRCODE = '23514'; END; $test$"
        )
    )
    await session.execute(
        text(
            f"CREATE TRIGGER {identifier} BEFORE INSERT ON audit_events FOR EACH ROW "
            f"WHEN ({condition}) EXECUTE FUNCTION {identifier}()"
        )
    )
