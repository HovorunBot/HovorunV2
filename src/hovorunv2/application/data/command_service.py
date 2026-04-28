"""Application service for low-level command data operations."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from hovorunv2.domain.chat import ChatDB
from hovorunv2.domain.command import CommandDB
from hovorunv2.infrastructure.repositories.chat_repository import SQLAlchemyChatRepository


class CommandDataService:
    """Service to handle database transactions for commands and their relations to chats."""

    def __init__(self, session_maker: async_sessionmaker) -> None:
        """Initialize with session maker."""
        self._session_maker = session_maker

    async def get_allowed_commands(self, chat_id: int, platform: str) -> list[str]:
        """Fetch allowed commands for a chat."""
        async with self._session_maker() as session:
            repo = SQLAlchemyChatRepository(session)
            chat = await repo.get_by_id(chat_id, platform)
            if not chat:
                return []
            commands = await chat.awaitable_attrs.commands
            return [cmd.name for cmd in commands]

    async def enable_command(self, chat_id: int, platform: str, command_name: str) -> bool:
        """Associate a command with a chat."""
        async with self._session_maker() as session:
            repo = SQLAlchemyChatRepository(session)
            chat = await repo.get_by_id(chat_id, platform)
            if not chat:
                chat = ChatDB(chat_id=chat_id, platform=platform, is_whitelisted=False)
                session.add(chat)

            # Check if command exists
            stmt = select(CommandDB).where(CommandDB.name == command_name)
            result = await session.execute(stmt)
            command = result.scalar_one_or_none()
            if not command:
                return False

            chat_commands = await chat.awaitable_attrs.commands
            if command not in chat_commands:
                chat_commands.append(command)
                await session.commit()
            return True

    async def disable_command(self, chat_id: int, platform: str, command_name: str) -> bool:
        """Remove association of a command from a chat."""
        async with self._session_maker() as session:
            repo = SQLAlchemyChatRepository(session)
            chat = await repo.get_by_id(chat_id, platform)
            if not chat:
                return False

            chat_commands = await chat.awaitable_attrs.commands
            found = False
            for cmd in chat_commands:
                if cmd.name == command_name:
                    chat_commands.remove(cmd)
                    found = True
                    break

            if found:
                await session.commit()
            return found
