"""Persistence adapter for command association operations."""

from datetime import UTC, datetime
from functools import partial

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from hovorunv2.application.access.permissions import PermissionFacts, PermissionTarget
from hovorunv2.application.ports import AuditedUnitOfWork, AuthorizationPolicy, CommandStore
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.ban import BanDB
from hovorunv2.domain.chat import ChatDB
from hovorunv2.domain.command import CommandDB
from hovorunv2.domain.enums import ActorOrigin, ChatStatus, Platform, TargetKind
from hovorunv2.infrastructure.persistence.locking import MutationLocks, lock_async_mutation
from hovorunv2.infrastructure.persistence.repositories.chat_repository import SQLAlchemyChatRepository
from hovorunv2.infrastructure.persistence.repositories.command_repository import SQLAlchemyCommandRepository
from hovorunv2.infrastructure.persistence.repositories.group_beta_repository import SQLAlchemyGroupBetaRepository
from hovorunv2.infrastructure.persistence.repositories.user_repository import SQLAlchemyUserRepository


class SQLAlchemyCommandStore(CommandStore):
    """SQLAlchemy persistence store for commands and chat associations."""

    def __init__(
        self,
        session_maker: async_sessionmaker,
        audited_uow: AuditedUnitOfWork,
        policy: AuthorizationPolicy,
    ) -> None:
        """Initialize with session maker and audited unit of work."""
        self._session_maker = session_maker
        self._audited_uow = audited_uow
        self._policy = policy

    async def _authorize_selection(self, session: AsyncSession, chat_id: int, actor: ActorContext) -> None:
        """Recheck current configuration authority; trusted ingress supplies actor origin."""
        if actor.origin != ActorOrigin.TELEGRAM:
            return
        user = await SQLAlchemyUserRepository(session).get_by_telegram_id(actor.actor_id or 0)
        banned = await session.scalar(
            select(BanDB.id)
            .where(
                BanDB.target_kind == TargetKind.USER,
                BanDB.telegram_id == actor.actor_id,
                BanDB.platform == "telegram",
                BanDB.deleted_at.is_(None),
                BanDB.revoked_at.is_(None),
            )
            .limit(1)
        )
        chat = await SQLAlchemyChatRepository(session).get_by_id(chat_id)
        if user is None or banned is not None or chat is None:
            msg = "Feature configuration requires current active authority."
            raise PermissionError(msg)
        if chat_id > 0:
            allowed = actor.actor_id == chat_id and (
                user.is_trusted
                or self._policy.is_allowed(PermissionFacts(user.role, "bot:administer", PermissionTarget("bot")))
            )
        else:
            admin = await SQLAlchemyGroupBetaRepository(session).is_admin(chat.id, user.id, datetime.now(UTC))
            allowed = self._policy.is_allowed(
                PermissionFacts(
                    user.role,
                    "group:settings",
                    PermissionTarget("group", chat.id, chat.generation, chat.chat_id),
                    admin,
                )
            )
        if not allowed:
            msg = "Feature configuration requires current settings authority."
            raise PermissionError(msg)

    async def legacy_capture_allowed(self, chat_id: int, platform: str = "telegram") -> bool:
        """Read compatibility only for an active migrated empty configuration."""
        async with self._session_maker() as session:
            chat = await SQLAlchemyChatRepository(session).get_by_id(chat_id, platform)
            return chat is not None and chat.legacy_empty_capture and not chat.commands

    async def _initialize_defaults(
        self, session: AsyncSession, chat_id: int, commands: tuple[str, ...], platform: str
    ) -> bool:
        await lock_async_mutation(session, MutationLocks(chats=(chat_id,), platform=platform))
        repository = SQLAlchemyChatRepository(session)
        chat = await repository.get_by_id(chat_id, platform)
        if chat is None or chat.features_configured or chat.commands or chat.legacy_empty_capture:
            return False
        definitions = list((await session.scalars(select(CommandDB).where(CommandDB.name.in_(commands)))).all())
        if len(definitions) != len(commands):
            msg = "Default feature definitions are incomplete"
            raise ValueError(msg)
        chat.commands.extend(definitions)
        chat.features_configured = True
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
        actor: ActorContext,
    ) -> tuple[bool, bool, bool]:
        """Associate a command with a chat on session."""
        await lock_async_mutation(
            session,
            MutationLocks(
                chats=(chat_id,), actors=(actor.actor_id,) if actor.actor_id is not None else (), platform=platform
            ),
        )
        await self._authorize_selection(session, chat_id, actor)
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

        legacy, first_selection = self._record_explicit_selection(chat)
        chat_commands = await chat.awaitable_attrs.commands
        if command not in chat_commands:
            chat_commands.append(command)
            return True, True, legacy
        return True, legacy or first_selection, legacy

    async def _execute_disable_command(
        self,
        session: AsyncSession,
        chat_id: int,
        command_name: str,
        platform: str,
        actor: ActorContext,
    ) -> tuple[bool, bool, bool]:
        """Remove association and end legacy compatibility in the same transaction."""
        await lock_async_mutation(
            session,
            MutationLocks(
                chats=(chat_id,), actors=(actor.actor_id,) if actor.actor_id is not None else (), platform=platform
            ),
        )
        await self._authorize_selection(session, chat_id, actor)
        repo = SQLAlchemyChatRepository(session)
        chat = await repo.get_by_id(chat_id, platform)
        if not chat:
            return False, False, False
        if await SQLAlchemyCommandRepository(session).get_by_name(command_name) is None:
            return False, False, False
        legacy, first_selection = self._record_explicit_selection(chat)

        chat_commands = await chat.awaitable_attrs.commands
        for cmd in list(chat_commands):
            if cmd.name == command_name:
                chat_commands.remove(cmd)
                return True, legacy, True
        # A first explicit off selection must prevent later default initialization.
        return legacy, legacy, legacy or first_selection

    def _record_explicit_selection(self, chat: ChatDB) -> tuple[bool, bool]:
        """End legacy capture and record whether this is the first explicit selection."""
        legacy = bool(chat.legacy_empty_capture)
        first_selection = not chat.features_configured
        chat.legacy_empty_capture = False
        chat.features_configured = True
        return legacy, first_selection

    async def enable_command(
        self, chat_id: int, command_name: str, platform: str = "telegram", actor: ActorContext | None = None
    ) -> bool:
        """Associate a command with a chat."""
        operation = partial(
            self._execute_enable_command,
            actor=actor or ActorContext.system("enable_command"),
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
            actor=actor or ActorContext.system("disable_command"),
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
