"""Persistence adapter for chat storage operations."""

from datetime import UTC, datetime
from functools import partial

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from hovorunv2.application.auditing import AuditedUnitOfWork
from hovorunv2.application.groups.ports import ChatStore
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.chat import ChatDB
from hovorunv2.domain.enums import ChatStatus, Platform
from hovorunv2.domain.group_route import GroupRouteDB
from hovorunv2.infrastructure.persistence.locking import MutationLocks, lock_async_mutation
from hovorunv2.infrastructure.persistence.repositories.chat_repository import SQLAlchemyChatRepository


class SQLAlchemyChatStore(ChatStore):
    """SQLAlchemy persistence store for chats with audited mutations."""

    def __init__(
        self,
        session_maker: async_sessionmaker,
        audited_uow: AuditedUnitOfWork,
    ) -> None:
        """Initialize with session maker and audited unit of work."""
        self._session_maker = session_maker
        self._audited_uow = audited_uow

    async def get_chat(self, chat_id: int, platform: str = "telegram") -> ChatDB | None:
        """Fetch chat from database.

        Args:
            chat_id: Numeric chat ID or canonical route to resolve.
            platform: Platform of the chat identity.

        Returns:
            Active chat matching the ID and platform, or None if absent.
        """
        async with self._session_maker() as session:
            repo = SQLAlchemyChatRepository(session)
            return await repo.get_by_id(chat_id, platform)

    async def list_groups(self, after_id: int, limit: int, *, present_only: bool) -> list[ChatDB]:
        """Read one bounded page of active canonical group candidates.

        Args:
            after_id: Exclusive lower bound for keyset pagination.
            limit: Maximum number of groups to return.
            present_only: Whether to require bot presence on the returned groups.

        Returns:
            Active canonical group candidates ordered by ID.
        """
        async with self._session_maker() as session:
            return await SQLAlchemyChatRepository(session).list_groups(after_id, limit, present_only=present_only)

    async def get_all_by_status(self, status: ChatStatus, platform: str = "telegram") -> list[ChatDB]:
        """Fetch all chats with a specific status from database.

        Args:
            status: Lifecycle status to filter on.
            platform: Platform of the chat identities.

        Returns:
            Active chats matching the status.
        """
        async with self._session_maker() as session:
            repo = SQLAlchemyChatRepository(session)
            return await repo.get_all_by_status(status, platform)

    async def get_historical_by_chat_id(self, chat_id: int, platform: str = "telegram") -> list[ChatDB]:
        """Fetch all chat generations for recovery workflows.

        Args:
            chat_id: Numeric chat ID of the chat.
            platform: Platform of the chat identity.

        Returns:
            All generations ordered by descending generation.
        """
        async with self._session_maker() as session:
            repo = SQLAlchemyChatRepository(session)
            return await repo.get_historical_by_chat_id(chat_id, platform)

    async def _execute_soft_delete(
        self,
        session: AsyncSession,
        chat_id: int,
        actor_id: int | None,
        reason: str,
        platform: str,
        deleted_at: datetime,
    ) -> bool:
        """Execute chat soft-deletion on session."""
        await lock_async_mutation(session, MutationLocks(chats=(chat_id,), platform=platform))
        repo = SQLAlchemyChatRepository(session)
        return await repo.soft_delete(
            chat_id=chat_id,
            actor_id=actor_id,
            reason=reason,
            platform=platform,
            deleted_at=deleted_at,
        )

    async def soft_delete_chat(
        self, chat_id: int, actor: ActorContext, reason: str, platform: str = "telegram"
    ) -> bool:
        """Soft-delete an active chat identity recording actor and deletion reason.

        Args:
            chat_id: Numeric chat ID of the chat to delete.
            actor: Actor context recorded on the audit event.
            reason: Deletion reason recorded on the tombstone.
            platform: Platform of the chat identity.

        Returns:
            True if an active chat was soft-deleted, False if none was found.
        """
        now = datetime.now(UTC)
        operation = partial(
            self._execute_soft_delete,
            chat_id=chat_id,
            actor_id=actor.actor_id,
            reason=reason,
            platform=platform,
            deleted_at=now,
        )

        return await self._audited_uow.execute(
            actor=actor,
            target_type="chat",
            target_id=str(chat_id),
            action="delete",
            operation=operation,
            changed_fields={"deleted_at": now.isoformat(), "reason": reason},
            audit_if=bool,
        )

    async def _check_historical_route(self, session: AsyncSession, chat_id: int) -> None:
        """Never provision a second identity from a historical routing alias."""
        if await session.scalar(select(GroupRouteDB.group_id).where(GroupRouteDB.telegram_id == chat_id)) is not None:
            msg = "Historical routing belongs to an unavailable group generation."
            raise ValueError(msg)

    async def _execute_update_status(
        self,
        session: AsyncSession,
        chat_id: int,
        status: ChatStatus,
        platform: str,
    ) -> ChatStatus | None:
        """Update chat status on session."""
        await lock_async_mutation(session, MutationLocks(chats=(chat_id,), platform=platform))
        repo = SQLAlchemyChatRepository(session)
        chat = await repo.get_by_id(chat_id, platform)
        old_status: ChatStatus | None = None
        if chat:
            old_status = chat.status
            chat.status = status
        else:
            await self._check_historical_route(session, chat_id)
            chat = ChatDB(chat_id=chat_id, platform=Platform(platform), status=status)
            repo.add(chat)
        return old_status

    async def _execute_update_translation_settings(
        self,
        session: AsyncSession,
        chat_id: int,
        target_lang: str,
        ignored_langs: list[str],
        platform: str,
    ) -> None:
        """Update translation settings on session."""
        await lock_async_mutation(session, MutationLocks(chats=(chat_id,), platform=platform))
        repo = SQLAlchemyChatRepository(session)
        chat = await repo.get_by_id(chat_id, platform)
        if not chat:
            await self._check_historical_route(session, chat_id)
            chat = ChatDB(chat_id=chat_id, platform=Platform(platform))
            repo.add(chat)

        chat.target_lang = target_lang
        chat.ignored_langs = ignored_langs

    async def _execute_create_chat(
        self,
        session: AsyncSession,
        chat_id: int,
        platform: str,
    ) -> tuple[ChatDB, bool]:
        """Create chat if missing on session."""
        await lock_async_mutation(session, MutationLocks(chats=(chat_id,), platform=platform))
        repo = SQLAlchemyChatRepository(session)
        existing = await repo.get_by_id(chat_id, platform)
        if existing:
            return existing, False
        await self._check_historical_route(session, chat_id)
        new_chat = ChatDB(chat_id=chat_id, platform=Platform(platform))
        repo.add(new_chat)
        return new_chat, True

    async def _execute_set_encryption_salt(
        self,
        session: AsyncSession,
        chat_id: int,
        salt: str,
        platform: str,
    ) -> None:
        """Set encryption salt on session."""
        await lock_async_mutation(session, MutationLocks(chats=(chat_id,), platform=platform))
        repo = SQLAlchemyChatRepository(session)
        chat = await repo.get_by_id(chat_id, platform)
        if not chat:
            await self._check_historical_route(session, chat_id)
            chat = ChatDB(chat_id=chat_id, platform=Platform(platform))
            repo.add(chat)
        chat.encryption_salt = salt

    async def update_status(
        self, chat_id: int, status: ChatStatus, platform: str = "telegram", actor: ActorContext | None = None
    ) -> None:
        """Update lifecycle status for a chat.

        Args:
            chat_id: Numeric chat ID of the chat.
            status: New lifecycle status.
            platform: Platform of the chat identity.
            actor: Optional actor context recorded on the audit event.
        """
        operation = partial(
            self._execute_update_status,
            chat_id=chat_id,
            status=status,
            platform=platform,
        )

        await self._audited_uow.execute(
            actor=actor or ActorContext.system("update_status"),
            target_type="chat",
            target_id=str(chat_id),
            action="update_status",
            operation=operation,
            changed_fields=lambda old: {
                "status": status.value if hasattr(status, "value") else str(status),
                "old_status": old.value if hasattr(old, "value") else (str(old) if old else None),
            },
        )

    async def update_translation_settings(
        self,
        chat_id: int,
        target_lang: str,
        ignored_langs: list[str],
        platform: str = "telegram",
        actor: ActorContext | None = None,
    ) -> None:
        """Update translation settings for a chat.

        Args:
            chat_id: Numeric chat ID of the chat.
            target_lang: New target language.
            ignored_langs: New ignored languages.
            platform: Platform of the chat identity.
            actor: Optional actor context recorded on the audit event.
        """
        operation = partial(
            self._execute_update_translation_settings,
            chat_id=chat_id,
            target_lang=target_lang,
            ignored_langs=ignored_langs,
            platform=platform,
        )

        await self._audited_uow.execute(
            actor=actor or ActorContext.system("update_translation_settings"),
            target_type="chat",
            target_id=str(chat_id),
            action="update_translation_settings",
            operation=operation,
            changed_fields={"target_lang": target_lang, "ignored_langs": ignored_langs},
        )

    async def get_or_create_chat(
        self, chat_id: int, platform: str = "telegram", actor: ActorContext | None = None
    ) -> ChatDB:
        """Fetch chat from database or create it if not exists.

        Args:
            chat_id: Numeric chat ID of the chat.
            platform: Platform of the chat identity.
            actor: Optional actor context recorded on the audit event.

        Returns:
            Existing or newly created chat.
        """
        chat = await self.get_chat(chat_id, platform)
        if chat:
            return chat

        operation = partial(
            self._execute_create_chat,
            chat_id=chat_id,
            platform=platform,
        )

        res = await self._audited_uow.execute(
            actor=actor or ActorContext.system("get_or_create_chat"),
            target_type="chat",
            target_id=str(chat_id),
            action="create",
            operation=operation,
            changed_fields={"platform": platform},
            audit_if=lambda r: r[1],
        )
        return res[0]

    async def set_encryption_salt(
        self, chat_id: int, salt: str, platform: str = "telegram", actor: ActorContext | None = None
    ) -> None:
        """Set encryption salt for a chat.

        Args:
            chat_id: Numeric chat ID of the chat.
            salt: New encryption salt.
            platform: Platform of the chat identity.
            actor: Optional actor context recorded on the audit event.
        """
        operation = partial(
            self._execute_set_encryption_salt,
            chat_id=chat_id,
            salt=salt,
            platform=platform,
        )

        await self._audited_uow.execute(
            actor=actor or ActorContext.system("set_encryption_salt"),
            target_type="chat",
            target_id=str(chat_id),
            action="set_encryption_salt",
            operation=operation,
            changed_fields={"encryption_salt": salt},
        )
