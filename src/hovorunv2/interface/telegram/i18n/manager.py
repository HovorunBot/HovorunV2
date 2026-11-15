"""Audience-aware locale manager for aiogram-i18n resolving chat and user context."""

from aiogram.types import Chat, User
from aiogram_i18n.managers import BaseManager

from hovorunv2.application.groups.ports import ChatStore
from hovorunv2.application.identity.ports import UserStore
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.enums import Platform
from hovorunv2.interface.telegram.i18n.core import DEFAULT_LOCALE


class AudienceI18nManager(BaseManager):
    """Audience-aware locale manager that resolves group vs private Telegram interaction context."""

    def __init__(
        self,
        chats: ChatStore,
        users: UserStore,
        default_locale: str = DEFAULT_LOCALE,
    ) -> None:
        """Initialize audience manager with chat and user stores and default locale.

        Args:
            chats: Chat store for reading and persisting chat locale preferences.
            users: User store for reading and persisting user locale preferences.
            default_locale: Fallback locale when preferences are unset or unconfigured.
        """
        super().__init__(default_locale=default_locale)
        self._chats = chats
        self._users = users

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
            chat_locale = await self.get_chat_locale(event_chat.id)
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
            await self.set_chat_locale(event_chat.id, locale)
        elif event_from_user is not None:
            await self.set_user_locale(event_from_user.id, locale)

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

    async def get_chat_locale(self, chat_id: int) -> str | None:
        """Get the configured UI locale for a chat, if any.

        Args:
            chat_id: Numeric Telegram chat ID.

        Returns:
            The configured locale code or None.
        """
        chat = await self._chats.get_chat(chat_id, platform=Platform.TELEGRAM.value)
        return chat.ui_locale if chat is not None else None

    async def _get_user_locale(self, telegram_id: int) -> str | None:
        user = await self._users.get_by_telegram_id(telegram_id, platform=Platform.TELEGRAM.value)
        return user.ui_locale if user is not None else None

    async def set_chat_locale(
        self,
        chat_id: int,
        locale: str,
        *,
        actor: ActorContext | None = None,
    ) -> None:
        """Persist UI locale for a chat through the audited, locked ChatStore.

        Args:
            chat_id: Numeric Telegram chat ID.
            locale: Normalized locale code ('uk' or 'en').
            actor: Optional actor context recorded on the audit event.
        """
        await self._chats.set_ui_locale(chat_id, locale, platform=Platform.TELEGRAM.value, actor=actor)

    async def set_user_locale(
        self,
        telegram_id: int,
        locale: str,
        *,
        actor: ActorContext | None = None,
    ) -> None:
        """Persist UI locale for a user through the audited, locked UserStore.

        Args:
            telegram_id: Numeric Telegram user ID.
            locale: Normalized locale code ('uk' or 'en').
            actor: Optional actor context recorded on the audit event.
        """
        await self._users.set_ui_locale(telegram_id, locale, platform=Platform.TELEGRAM.value, actor=actor)
