"""Persistence adapter for command association operations."""

from functools import partial

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from hovorunv2.application.ports import AuditedUnitOfWork, CommandStore
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.chat import ChatDB
from hovorunv2.domain.enums import ChatStatus, Platform
from hovorunv2.infrastructure.repositories.chat_repository import SQLAlchemyChatRepository
from hovorunv2.infrastructure.repositories.command_repository import SQLAlchemyCommandRepository


class SQLAlchemyCommandStore(CommandStore):
    """SQLAlchemy persistence store for commands and chat associations."""

    def __init__(
        self,
        session_maker: async_sessionmaker,
        audited_uow: AuditedUnitOfWork,
    ) -> None:
        """Initialize with session maker and audited unit of work."""
        self._session_maker = session_maker
        self._audited_uow = audited_uow

    async def get_allowed_commands(self, chat_id: int, platform: str = "telegram") -> list[str]:
        """Fetch allowed commands for a chat."""
        async with self._session_maker() as session:
            repo = SQLAlchemyChatRepository(session)
            chat = await repo.get_by_id(chat_id, platform)
            if not chat:
                return []
            commands = await chat.awaitable_attrs.commands
            return [cmd.name for cmd in commands]

    async def _execute_enable_command(
        self,
        session: AsyncSession,
        chat_id: int,
        command_name: str,
        platform: str,
    ) -> tuple[bool, bool]:
        """Associate a command with a chat on session."""
        chat_repo = SQLAlchemyChatRepository(session)
        cmd_repo = SQLAlchemyCommandRepository(session)

        command = await cmd_repo.get_by_name(command_name)
        if not command:
            return False, False

        chat = await chat_repo.get_by_id(chat_id, platform)
        if not chat:
            chat = ChatDB(chat_id=chat_id, platform=Platform(platform), status=ChatStatus.UNAUTHORIZED)
            session.add(chat)

        chat_commands = await chat.awaitable_attrs.commands
        if command not in chat_commands:
            chat_commands.append(command)
            return True, True
        return True, False

    async def _execute_disable_command(
        self,
        session: AsyncSession,
        chat_id: int,
        command_name: str,
        platform: str,
    ) -> bool:
        """Remove association of a command from a chat on session."""
        repo = SQLAlchemyChatRepository(session)
        chat = await repo.get_by_id(chat_id, platform)
        if not chat:
            return False

        chat_commands = await chat.awaitable_attrs.commands
        for cmd in list(chat_commands):
            if cmd.name == command_name:
                chat_commands.remove(cmd)
                return True
        return False

    async def enable_command(
        self, chat_id: int, command_name: str, platform: str = "telegram", actor: ActorContext | None = None
    ) -> bool:
        """Associate a command with a chat."""
        operation = partial(
            self._execute_enable_command,
            chat_id=chat_id,
            command_name=command_name,
            platform=platform,
        )

        res: tuple[bool, bool] = await self._audited_uow.execute(
            actor=actor or ActorContext.system("enable_command"),
            target_type="command_association",
            target_id=f"{chat_id}:{command_name}",
            action="enable_command",
            operation=operation,
            changed_fields={"chat_id": chat_id, "platform": platform, "command": command_name},
            audit_if=lambda r: r[1],
        )
        return res[0]

    async def disable_command(
        self, chat_id: int, command_name: str, platform: str = "telegram", actor: ActorContext | None = None
    ) -> bool:
        """Remove association of a command from a chat."""
        operation = partial(
            self._execute_disable_command,
            chat_id=chat_id,
            command_name=command_name,
            platform=platform,
        )

        return await self._audited_uow.execute(
            actor=actor or ActorContext.system("disable_command"),
            target_type="command_association",
            target_id=f"{chat_id}:{command_name}",
            action="disable_command",
            operation=operation,
            changed_fields={"chat_id": chat_id, "platform": platform, "command": command_name},
            audit_if=bool,
        )
