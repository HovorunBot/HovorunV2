"""Audience-aware locale manager for aiogram-i18n resolving chat and user context."""

from aiogram.types import Chat, User
from aiogram_i18n.managers import BaseManager
from sqlalchemy.ext.asyncio import async_sessionmaker

from hovorunv2.domain.enums import Platform
from hovorunv2.infrastructure.persistence.repositories.chat_repository import SQLAlchemyChatRepository
from hovorunv2.infrastructure.persistence.repositories.user_repository import SQLAlchemyUserRepository
from hovorunv2.interface.telegram.i18n.core import DEFAULT_LOCALE


class AudienceI18nManager(BaseManager):
    """Audience-aware locale manager that resolves group vs private Telegram interaction context."""

    def __init__(
        self,
        session_maker: async_sessionmaker,
        default_locale: str = DEFAULT_LOCALE,
    ) -> None:
        """Initialize audience manager with database session factory and default locale.

        Args:
            session_maker: Async session maker for reading user and chat preferences.
            default_locale: Fallback locale when preferences are unset or unconfigured.
        """
        super().__init__(default_locale=default_locale)
        self._session_maker = session_maker

    async def _resolve_locale(
        self,
        event_chat: Chat | None = None,
        event_from_user: User | None = None,
    ) -> str:
        """Resolve effective locale for incoming Telegram event based on audience rules.

        Args:
            event_chat: Chat associated with the incoming event, if any.
            event_from_user: User associated with the incoming event, if any.

        Returns:
            Resolved locale code ('uk' or 'en', falling back to default).
        """
        if event_chat is not None and event_chat.type in ("group", "supergroup"):
            chat_locale = await self._get_chat_locale(event_chat.id)
            if chat_locale:
                return chat_locale
        elif event_from_user is not None:
            user_locale = await self._get_user_locale(event_from_user.id)
            if user_locale:
                return user_locale

        return self.default_locale or DEFAULT_LOCALE

    async def _persist_locale(
        self,
        locale: str,
        event_chat: Chat | None = None,
        event_from_user: User | None = None,
    ) -> None:
        """Persist updated locale for active audience.

        Args:
            locale: Normalized locale code ('uk' or 'en').
            event_chat: Chat associated with the event, if updating group locale.
            event_from_user: User associated with the event, if updating personal locale.
        """
        if event_chat is not None and event_chat.type in ("group", "supergroup"):
            await self._set_chat_locale(event_chat.id, locale)
        elif event_from_user is not None:
            await self._set_user_locale(event_from_user.id, locale)

    get_locale = _resolve_locale
    set_locale = _persist_locale

    async def get_user_locale(self, telegram_id: int) -> str:
        """Explicitly resolve user locale for private alerts in group contexts.

        Args:
            telegram_id: Numeric Telegram ID of the user.

        Returns:
            User's configured locale or default fallback.
        """
        user_locale = await self._get_user_locale(telegram_id)
        return user_locale or (self.default_locale or DEFAULT_LOCALE)

    async def _get_chat_locale(self, chat_id: int) -> str | None:
        async with self._session_maker() as session:
            repo = SQLAlchemyChatRepository(session)
            chat = await repo.get_by_id(chat_id, platform=Platform.TELEGRAM.value)
            return chat.ui_locale if chat is not None else None

    async def _get_user_locale(self, telegram_id: int) -> str | None:
        async with self._session_maker() as session:
            repo = SQLAlchemyUserRepository(session)
            user = await repo.get_by_telegram_id(telegram_id, platform=Platform.TELEGRAM.value)
            return user.ui_locale if user is not None else None

    async def _set_chat_locale(self, chat_id: int, locale: str) -> None:
        async with self._session_maker() as session:
            repo = SQLAlchemyChatRepository(session)
            await repo.set_ui_locale(chat_id, locale, platform=Platform.TELEGRAM.value)
            await session.commit()

    async def _set_user_locale(self, telegram_id: int, locale: str) -> None:
        async with self._session_maker() as session:
            repo = SQLAlchemyUserRepository(session)
            await repo.set_ui_locale(telegram_id, locale, platform=Platform.TELEGRAM.value)
            await session.commit()
