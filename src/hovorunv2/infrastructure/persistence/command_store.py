"""Persistence adapter for command association operations."""

from functools import partial

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from hovorunv2.application.ports import AuditedUnitOfWork, CommandStore
from hovorunv2.domain.audit import ActorContext, AuditEventDB
from hovorunv2.domain.chat import ChatDB
from hovorunv2.domain.command import CommandDB
from hovorunv2.domain.enums import ChatStatus, Platform
from hovorunv2.domain.group_route import GroupRouteDB
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

    async def legacy_capture_allowed(self, chat_id: int, platform: str = "telegram") -> bool:
        """Read compatibility only for an active migrated empty configuration."""
        async with self._session_maker() as session:
            chat = await SQLAlchemyChatRepository(session).get_by_id(chat_id, platform)
            return chat is not None and chat.legacy_empty_capture and not chat.commands

    async def _initialize_defaults(
        self, session: AsyncSession, chat_id: int, commands: tuple[str, ...], platform: str
    ) -> bool:
        await session.execute(text("BEGIN IMMEDIATE"))
        repository = SQLAlchemyChatRepository(session)
        chat = await repository.get_by_id(chat_id, platform)
        if chat is None or chat.commands or chat.legacy_empty_capture:
            return False
        history = await session.scalar(
            select(AuditEventDB.id)
            .where(
                AuditEventDB.target_type.in_(("command_association", "command_selection")),
                (
                    (AuditEventDB.changed_fields["chat_id"].as_integer() == chat.chat_id)
                    | AuditEventDB.changed_fields["chat_id"]
                    .as_integer()
                    .in_(select(GroupRouteDB.telegram_id).where(GroupRouteDB.group_id == chat.id))
                ),
            )
            .limit(1)
        )
        if history is not None:
            return False
        definitions = list((await session.scalars(select(CommandDB).where(CommandDB.name.in_(commands)))).all())
        if len(definitions) != len(commands):
            msg = "Default feature definitions are incomplete"
            raise ValueError(msg)
        chat.commands.extend(definitions)
        return True

    async def initialize_defaults(self, chat_id: int, commands: tuple[str, ...], platform: str = "telegram") -> None:
        """Apply first-configuration defaults atomically and preserve deliberate empty state."""
        await self._audited_uow.execute(
            actor=ActorContext.system("initialize_defaults"),
            target_type="command_selection",
            target_id=str(chat_id),
            action="initialize_defaults",
            operation=partial(self._initialize_defaults, chat_id=chat_id, commands=commands, platform=platform),
            changed_fields={"chat_id": chat_id, "platform": platform, "commands": list(commands)},
            audit_if=bool,
        )

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
    ) -> tuple[bool, bool, bool]:
        """Associate a command with a chat on session."""
        await session.execute(text("BEGIN IMMEDIATE"))
        chat_repo = SQLAlchemyChatRepository(session)
        cmd_repo = SQLAlchemyCommandRepository(session)

        command = await cmd_repo.get_by_name(command_name)
        if not command:
            return False, False, False

        chat = await chat_repo.get_by_id(chat_id, platform)
        if not chat:
            if await chat_repo.get_deleted_by_chat_id(chat_id, platform) is not None:
                return False, False, False
            chat = ChatDB(chat_id=chat_id, platform=Platform(platform), status=ChatStatus.UNAUTHORIZED)
            session.add(chat)

        legacy = bool(chat.legacy_empty_capture)
        chat.legacy_empty_capture = False
        chat_commands = await chat.awaitable_attrs.commands
        if command not in chat_commands:
            chat_commands.append(command)
            return True, True, legacy
        return True, legacy, legacy

    async def _execute_disable_command(
        self,
        session: AsyncSession,
        chat_id: int,
        command_name: str,
        platform: str,
    ) -> tuple[bool, bool, bool]:
        """Remove association and end legacy compatibility in the same transaction."""
        await session.execute(text("BEGIN IMMEDIATE"))
        repo = SQLAlchemyChatRepository(session)
        chat = await repo.get_by_id(chat_id, platform)
        if not chat:
            return False, False, False
        if await SQLAlchemyCommandRepository(session).get_by_name(command_name) is None:
            return False, False, False
        legacy = chat.legacy_empty_capture
        chat.legacy_empty_capture = False

        chat_commands = await chat.awaitable_attrs.commands
        for cmd in list(chat_commands):
            if cmd.name == command_name:
                chat_commands.remove(cmd)
                return True, legacy, True
        history = await session.scalar(
            select(AuditEventDB.id)
            .where(
                AuditEventDB.target_type.in_(("command_association", "command_selection")),
                (
                    (AuditEventDB.changed_fields["chat_id"].as_integer() == chat.chat_id)
                    | AuditEventDB.changed_fields["chat_id"]
                    .as_integer()
                    .in_(select(GroupRouteDB.telegram_id).where(GroupRouteDB.group_id == chat.id))
                ),
            )
            .limit(1)
        )
        # A first explicit off selection must prevent later default initialization.
        return legacy, legacy, legacy or history is None

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

        res: tuple[bool, bool, bool] = await self._audited_uow.execute(
            actor=actor or ActorContext.system("enable_command"),
            target_type="command_association",
            target_id=f"{chat_id}:{command_name}",
            action="enable_command",
            operation=operation,
            changed_fields=lambda result: (
                {"chat_id": chat_id, "platform": platform, "command": command_name}
                | ({"legacy_empty_capture": False} if result[2] else {})
            ),
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

        result: tuple[bool, bool, bool] = await self._audited_uow.execute(
            actor=actor or ActorContext.system("disable_command"),
            target_type="command_association",
            target_id=f"{chat_id}:{command_name}",
            action="disable_command",
            operation=operation,
            changed_fields=lambda result: (
                {"chat_id": chat_id, "platform": platform, "command": command_name}
                | ({"legacy_empty_capture": False} if result[1] else {})
            ),
            audit_if=lambda result: result[2],
        )
        return result[0]
