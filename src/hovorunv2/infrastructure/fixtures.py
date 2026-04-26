"""Database fixtures for initial setup."""

from typing import TYPE_CHECKING

from sqlalchemy import select

from hovorunv2.domain.command import CommandDB
from hovorunv2.infrastructure.logger import get_logger

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import async_sessionmaker

logger = get_logger(__name__)

INITIAL_COMMANDS = [
    {"name": "debug", "description": "Debug information"},
    {"name": "allow_chat", "description": "Whitelist a chat"},
    {"name": "enable_cmd", "description": "Enable a specific command"},
    {"name": "disable_cmd", "description": "Disable a specific command"},
    {"name": "tiktok", "description": "Process TikTok links"},
    {"name": "twitter", "description": "Process Twitter links"},
    {"name": "instagram", "description": "Process Instagram links"},
    {"name": "facebook", "description": "Process Facebook links"},
    {"name": "threads", "description": "Process Threads links"},
    {"name": "youtube", "description": "Process YouTube links"},
]


async def setup_fixtures(session_maker: async_sessionmaker) -> None:
    """Populate database with initial commands if they don't exist."""
    async with session_maker() as session:
        for cmd_data in INITIAL_COMMANDS:
            stmt = select(CommandDB).where(CommandDB.name == cmd_data["name"])
            result = await session.execute(stmt)
            if not result.scalar_one_or_none():
                logger.info("Adding command fixture: %s", cmd_data["name"])
                session.add(CommandDB(**cmd_data))
        await session.commit()
