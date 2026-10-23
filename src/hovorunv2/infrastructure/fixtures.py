"""Database fixtures for initial setup."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from hovorunv2.domain.command import CommandDB
from hovorunv2.domain.enums import PROVIDER_SPLIT_COMMANDS, CommandName
from hovorunv2.infrastructure.logger import get_logger

logger = get_logger(__name__)

INITIAL_COMMANDS: list[dict[str, str]] = [
    {"name": CommandName.DEBUG, "description": "Debug information"},
    {"name": CommandName.ALLOW_CHAT, "description": "Request bot approval"},
    {"name": CommandName.SETTINGS, "description": "Bot configuration"},
    {"name": CommandName.HELP, "description": "Help information"},
    {"name": CommandName.FEEDBACK, "description": "Send feedback to bot developers"},
    {"name": CommandName.START, "description": "Start the bot"},
    *[
        entry
        for provider, (video_cmd, post_cmd) in PROVIDER_SPLIT_COMMANDS.items()
        for entry in (
            {"name": video_cmd, "description": f"Process {provider.capitalize()} video links"},
            {"name": post_cmd, "description": f"Process {provider.capitalize()} image/text links"},
        )
    ],
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
