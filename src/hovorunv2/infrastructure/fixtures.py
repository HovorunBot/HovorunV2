"""Database fixtures for initial setup."""

from functools import partial

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from hovorunv2.application.features import FEATURE_REGISTRY
from hovorunv2.application.ports import AuditedUnitOfWork
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.command import CommandDB
from hovorunv2.domain.enums import CommandName
from hovorunv2.infrastructure.logger import get_logger
from hovorunv2.infrastructure.persistence.audited_transaction import SQLAlchemyAuditedTransaction
from hovorunv2.infrastructure.persistence.locking import MutationLocks, lock_async_mutation

logger = get_logger(__name__)

INITIAL_COMMANDS: list[dict[str, str]] = [
    {"name": CommandName.DEBUG, "description": "Debug information"},
    {"name": CommandName.ALLOW_CHAT, "description": "Request bot approval"},
    {"name": CommandName.SETTINGS, "description": "Bot configuration"},
    {"name": CommandName.HELP, "description": "Help information"},
    {"name": CommandName.FEEDBACK, "description": "Send feedback to bot developers"},
    {"name": CommandName.START, "description": "Start the bot"},
    *[{"name": feature.key, "description": f"Process {feature.display_name}"} for feature in FEATURE_REGISTRY.all()],
]


async def seed_command_fixtures(
    session: AsyncSession,
    missing_commands: list[dict[str, str]],
) -> list[str]:
    """Return only definitions actually inserted by this transaction."""
    added: list[str] = []
    await lock_async_mutation(session, MutationLocks(keys=("fixtures:commands",)))
    for cmd_data in missing_commands:
        logger.info("Adding command fixture: %s", cmd_data["name"])
        name = await session.scalar(
            insert(CommandDB).values(**cmd_data).on_conflict_do_nothing().returning(CommandDB.name)
        )
        if name is not None:
            added.append(name)
    return added


async def setup_fixtures(
    session_maker: async_sessionmaker,
    audited_uow: AuditedUnitOfWork | None = None,
) -> None:
    """Populate database with initial commands if they don't exist, auditing the operation."""
    async with session_maker() as session:
        stmt = select(CommandDB.name)
        result = await session.execute(stmt)
        existing_names = set(result.scalars().all())

    missing = [cmd_data for cmd_data in INITIAL_COMMANDS if cmd_data["name"] not in existing_names]
    if not missing:
        return

    uow = audited_uow or SQLAlchemyAuditedTransaction(session_maker)
    operation = partial(seed_command_fixtures, missing_commands=missing)

    await uow.execute(
        actor=ActorContext.system(reason="fixture_seed"),
        target_type="command_fixture",
        target_id="initial_commands",
        action="seed",
        operation=operation,
        changed_fields=lambda added: {"added_commands": added},
        audit_if=bool,
    )
