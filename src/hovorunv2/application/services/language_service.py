"""Application service for managing language preferences."""

import logging

from hovorunv2.application.ports import AppSettings, ChatStore

logger = logging.getLogger(__name__)


class LanguageService:
    """Service to handle language-related business logic.

    Manages target language and ignored languages for chats,
    falling back to global settings when chat-specific ones are missing.
    """

    def __init__(self, chat_service: ChatStore, settings: AppSettings) -> None:
        """Initialize the service.

        Args:
            chat_service: Data service for chat persistence.
            settings: Application settings port.

        """
        self._chat_service = chat_service
        self._default_target_lang = settings.translation_target_lang
        self._default_ignored_langs = settings.translation_ignored_langs

    async def get_chat_settings(self, chat_id: int) -> tuple[str, list[str]]:
        """Get target language and ignored languages for a chat."""
        chat = await self._chat_service.get_chat(chat_id)
        if not chat:
            return self._default_target_lang, list(self._default_ignored_langs)

        target_lang = chat.target_lang or self._default_target_lang
        ignored_langs = (
            list(chat.ignored_langs) if chat.ignored_langs is not None else list(self._default_ignored_langs)
        )

        return target_lang, ignored_langs

    async def update_settings(self, chat_id: int, target_lang: str, ignored_langs: list[str]) -> None:
        """Update language settings for a chat.

        Args:
            chat_id: The ID of the chat.
            target_lang: The new target language code.
            ignored_langs: List of language codes to ignore.

        """
        await self._chat_service.update_translation_settings(
            chat_id=chat_id, target_lang=target_lang, ignored_langs=ignored_langs
        )
