"""Application service for managing language preferences."""

import json

from hovorunv2.application.data.chat_service import ChatService
from hovorunv2.infrastructure.config import settings


class LanguageService:
    """Service to handle language-related business logic.

    Manages target language and ignored languages for chats,
    falling back to global settings when chat-specific ones are missing.
    """

    def __init__(self, chat_service: ChatService) -> None:
        """Initialize the service.

        Args:
            chat_service: Data service for chat persistence.

        """
        self._chat_service = chat_service
        self._default_target_lang = settings.translation_target_lang
        self._default_ignored_langs = settings.translation_ignored_langs

    async def get_chat_settings(self, chat_id: int, platform: str = "telegram") -> tuple[str, list[str]]:
        """Get target language and ignored languages for a chat.

        Args:
            chat_id: The ID of the chat.
            platform: Messaging platform identifier.

        Returns:
            A tuple containing (target_lang, ignored_langs).

        """
        chat = await self._chat_service.get_chat(chat_id, platform)

        target_lang = self._default_target_lang
        ignored_langs = list(self._default_ignored_langs)

        if chat:
            if chat.target_lang:
                target_lang = chat.target_lang
            if chat.ignored_langs:
                if isinstance(chat.ignored_langs, list):
                    ignored_langs = chat.ignored_langs
                elif isinstance(chat.ignored_langs, str):
                    try:
                        chat_ignored = json.loads(chat.ignored_langs)
                        if isinstance(chat_ignored, list):
                            ignored_langs = chat_ignored
                    except json.JSONDecodeError, TypeError:
                        pass

        return target_lang, ignored_langs

    async def update_settings(
        self, chat_id: int, target_lang: str, ignored_langs: list[str], platform: str = "telegram"
    ) -> None:
        """Update language settings for a chat.

        Args:
            chat_id: The ID of the chat.
            target_lang: The new target language code.
            ignored_langs: List of language codes to ignore.
            platform: Messaging platform identifier.

        """
        await self._chat_service.update_translation_settings(
            chat_id=chat_id, platform=platform, target_lang=target_lang, ignored_langs=ignored_langs
        )
