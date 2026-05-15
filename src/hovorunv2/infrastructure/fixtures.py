"""Database fixtures for initial setup."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from hovorunv2.application.data.constants import CommandName
from hovorunv2.domain.command import CommandDB
from hovorunv2.infrastructure.logger import get_logger

logger = get_logger(__name__)

INITIAL_COMMANDS = [
    {"name": CommandName.DEBUG, "description": "Debug information"},
    {"name": CommandName.ALLOW_CHAT, "description": "Whitelist a chat"},
    {"name": CommandName.SETTINGS, "description": "Bot configuration"},
    {"name": CommandName.HELP, "description": "Help information"},
    {"name": CommandName.START, "description": "Start the bot"},
    {"name": CommandName.TIKTOK, "description": "Process TikTok links"},
    {"name": CommandName.TWITTER, "description": "Process Twitter links"},
    {"name": CommandName.INSTAGRAM, "description": "Process Instagram links"},
    {"name": CommandName.FACEBOOK, "description": "Process Facebook links"},
    {"name": CommandName.THREADS, "description": "Process Threads links"},
    {"name": CommandName.BLUESKY, "description": "Process Bluesky links"},
    {"name": CommandName.YOUTUBE, "description": "Process YouTube links"},
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
