"""Application service for managing language preferences."""

import json
from typing import TYPE_CHECKING

from hovorunv2.infrastructure.config import settings

if TYPE_CHECKING:
    from hovorunv2.application.data.chat_service import ChatService


class LanguageService:
    """Service to handle language-related business logic."""

    def __init__(self, chat_service: ChatService) -> None:
        """Initialize with data service."""
        self._chat_service = chat_service
        self._default_target_lang = settings.translation_target_lang
        self._default_ignored_langs = settings.translation_ignored_langs

    async def get_chat_settings(self, chat_id: int, platform: str = "telegram") -> tuple[str, list[str]]:
        """Get target language and ignored languages for a chat."""
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
                    except (json.JSONDecodeError, TypeError):
                        pass

        return target_lang, ignored_langs

    async def update_settings(
        self, chat_id: int, target_lang: str, ignored_langs: list[str], platform: str = "telegram"
    ) -> None:
        """Update language settings for a chat."""
        await self._chat_service.update_translation_settings(
            chat_id=chat_id, platform=platform, target_lang=target_lang, ignored_langs=ignored_langs
        )
