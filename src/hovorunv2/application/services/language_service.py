"""Application service for managing language preferences."""

import json

from hovorunv2.application.data.chat_service import ChatService
from hovorunv2.infrastructure.config import settings
from hovorunv2.infrastructure.logger import get_logger

logger = get_logger(__name__)


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
        """Get target language and ignored languages for a chat."""
        chat = await self._chat_service.get_chat(chat_id, platform)
        if not chat:
            return self._default_target_lang, self._default_ignored_langs

        target_lang = chat.target_lang or self._default_target_lang
        ignored_langs = self._default_ignored_langs

        match chat.ignored_langs:
            case list() as langs:
                ignored_langs = langs
            case str() as raw_langs:
                ignored_langs = self._parse_ignored_langs(raw_langs) or ignored_langs

        return target_lang, ignored_langs

    def _parse_ignored_langs(self, raw_langs: str) -> list[str] | None:
        """Safely parse ignored languages from JSON string."""
        try:
            parsed = json.loads(raw_langs)
            if isinstance(parsed, list):
                return parsed
        except (json.JSONDecodeError, TypeError) as e:
            logger.warning("Failed to decode ignored_langs: %s", e)
        return None

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
