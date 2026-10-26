"""Audited ingestion of public group metadata."""

from functools import partial

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from hovorunv2.application.ports import AuditedUnitOfWork
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.chat import ChatDB
from hovorunv2.infrastructure.repositories.chat_repository import SQLAlchemyChatRepository


class SQLAlchemyGroupIdentityStore:
    """Observe group identity without modifying access, settings or presence."""

    def __init__(self, session_maker: async_sessionmaker, audited_uow: AuditedUnitOfWork) -> None:
        self._sessions = session_maker
        self._audit = audited_uow

    async def ingest_group(self, telegram_id: int, title: str | None, chat_type: str) -> ChatDB | None:
        """Persist only current public group metadata, respecting tombstones."""
        result = await self._audit.execute(
            actor=ActorContext.system("telegram_identity_evidence"),
            target_type="chat",
            target_id=telegram_id,
            action="observe_identity",
            operation=partial(self._observe, telegram_id=telegram_id, title=title, chat_type=chat_type),
            changed_fields=lambda result: result[1],
            audit_if=lambda result: result[1] is not None,
        )
        return result[0]

    async def _observe(
        self, session: AsyncSession, telegram_id: int, title: str | None, chat_type: str
    ) -> tuple[ChatDB | None, dict[str, object] | None]:
        repo = SQLAlchemyChatRepository(session)
        chat = await repo.get_by_id(telegram_id)
        if chat is None:
            if await repo.get_deleted_by_chat_id(telegram_id) is not None:
                return None, None
            chat = ChatDB(chat_id=telegram_id, title=title, chat_type=chat_type)
            repo.add(chat)
            await session.flush()
            return chat, {"telegram_id": telegram_id, "title": title, "chat_type": chat_type}
        if chat.chat_id != telegram_id:
            return chat, None
        if chat.title == title and chat.chat_type == chat_type:
            return chat, None
        changes = {"title": [chat.title, title], "chat_type": [chat.chat_type, chat_type]}
        chat.title, chat.chat_type = title, chat_type
        return chat, changes
