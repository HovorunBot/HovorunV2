"""Tests for LanguageService."""

import pytest
from dishka import AsyncContainer

from hovorunv2.application.services.language_service import LanguageService
from hovorunv2.infrastructure.config import Settings


@pytest.mark.asyncio
async def test_toggling_ignored_langs_does_not_mutate_settings(init_container: AsyncContainer) -> None:
    """Verify get_chat_settings returns a copy and does not mutate global default settings."""
    language_service = await init_container.get(LanguageService)
    settings = await init_container.get(Settings)

    original_defaults = list(settings.translation_ignored_langs)

    # 1. Fetch settings for a chat with no custom settings
    _target, ignored = await language_service.get_chat_settings(chat_id=98765)
    assert ignored == original_defaults

    # Mutate the returned list in place (as settings menu toggling did)
    ignored.append("fr")
    ignored.remove(original_defaults[0])

    # 2. Fetch settings again; verify settings.translation_ignored_langs is intact
    _target2, ignored2 = await language_service.get_chat_settings(chat_id=98765)
    assert ignored2 == original_defaults
    assert settings.translation_ignored_langs == original_defaults


@pytest.mark.asyncio
async def test_update_and_get_chat_settings(init_container: AsyncContainer) -> None:
    """Verify updating and retrieving chat-specific language settings."""
    language_service = await init_container.get(LanguageService)

    chat_id = 555123
    await language_service.update_settings(chat_id=chat_id, target_lang="es", ignored_langs=["es", "it"])

    target, ignored = await language_service.get_chat_settings(chat_id=chat_id)
    assert target == "es"
    assert ignored == ["es", "it"]
