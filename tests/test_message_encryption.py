"""Tests for MessageService per-chat encryption."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram import types

from hovorunv2.application.services.message_service import MessageService


@pytest.fixture
def mock_cache_service() -> MagicMock:
    """Create a mock cache service."""
    service = MagicMock()
    service.set = AsyncMock()
    service.get = AsyncMock()
    return service


@pytest.fixture
def mock_chat_service() -> MagicMock:
    """Create a mock chat service."""
    service = MagicMock()
    service.get_or_create_chat = AsyncMock()
    service.set_encryption_salt = AsyncMock()
    service.update_chat = AsyncMock()
    return service


@pytest.fixture
def mock_settings() -> MagicMock:
    """Create mock settings."""
    settings = MagicMock()
    settings.cache_encryption_key = "test_encryption_key_at_least_32_chars_long_12345"
    return settings


@pytest.mark.asyncio
async def test_cache_message_encryption(
    mock_cache_service: MagicMock,
    mock_chat_service: MagicMock,
    mock_settings: MagicMock,
) -> None:
    """Test that message is encrypted before caching."""
    service = MessageService(mock_cache_service, mock_chat_service, mock_settings)

    chat_id = 123
    message_id = 456

    # Create a real-ish message dict that satisfies pydantic
    message_dict = {
        "message_id": message_id,
        "date": 123456789,
        "chat": {"id": chat_id, "type": "private"},
        "text": "hello",
    }

    message = MagicMock(spec=types.Message)
    message.chat = MagicMock()
    message.chat.id = chat_id
    message.message_id = message_id
    message.model_dump.return_value = message_dict

    chat = MagicMock()
    chat.encryption_salt = None
    mock_chat_service.get_or_create_chat.return_value = chat

    await service.cache_message(message)

    # Verify salt was generated
    mock_chat_service.set_encryption_salt.assert_called_once()
    assert chat.encryption_salt is not None

    # Verify encrypted data was sent to cache
    mock_cache_service.set.assert_called_once()
    key, payload = mock_cache_service.set.call_args[0]
    assert key == f"msg:{chat_id}:{message_id}"
    assert "hello" not in payload  # Should be encrypted


@pytest.mark.asyncio
async def test_get_message_decryption(
    mock_cache_service: MagicMock,
    mock_chat_service: MagicMock,
    mock_settings: MagicMock,
) -> None:
    """Test that message is decrypted after retrieval."""
    service = MessageService(mock_cache_service, mock_chat_service, mock_settings)

    chat_id = 123
    message_id = 456
    original_data = {
        "message_id": message_id,
        "date": 123456789,
        "chat": {"id": chat_id, "type": "private"},
        "text": "secret",
    }

    chat = MagicMock()
    chat.encryption_salt = "0123456789abcdef" * 2
    mock_chat_service.get_or_create_chat.return_value = chat

    message = MagicMock(spec=types.Message)
    message.chat = MagicMock()
    message.chat.id = chat_id
    message.message_id = message_id
    message.model_dump.return_value = original_data

    await service.cache_message(message)
    encrypted_payload = mock_cache_service.set.call_args[0][1]

    mock_cache_service.get.return_value = encrypted_payload

    retrieved = await service.get_message(chat_id, message_id)

    assert retrieved is not None
    assert retrieved.text == "secret"


@pytest.mark.asyncio
async def test_get_message_no_encryption_fails_when_enabled(
    mock_cache_service: MagicMock,
    mock_chat_service: MagicMock,
    mock_settings: MagicMock,
) -> None:
    """Test that message retrieval fails if not encrypted when encryption is enabled."""
    service = MessageService(mock_cache_service, mock_chat_service, mock_settings)

    chat_id = 123
    message_id = 456
    original_data = {
        "message_id": message_id,
        "date": 123456789,
        "chat": {"id": chat_id, "type": "private"},
        "text": "plain",
    }

    chat = MagicMock()
    chat.encryption_salt = "0123456789abcdef" * 2
    mock_chat_service.get_or_create_chat.return_value = chat

    # Store plain JSON in cache
    mock_cache_service.get.return_value = json.dumps(original_data)

    # Retrieval should fail because it expects encrypted data
    retrieved = await service.get_message(chat_id, message_id)

    assert retrieved is None


@pytest.mark.asyncio
async def test_fernet_derivation_cached_per_chat(
    mock_cache_service: MagicMock,
    mock_chat_service: MagicMock,
    mock_settings: MagicMock,
) -> None:
    """Verify PBKDF2 Fernet derivation is cached per chat in-memory."""
    service = MessageService(mock_cache_service, mock_chat_service, mock_settings)
    chat_id = 999
    chat = MagicMock(encryption_salt="0123456789abcdef" * 2)
    mock_chat_service.get_or_create_chat.return_value = chat

    with patch("hovorunv2.application.services.message_service.get_fernet_for_chat") as mock_kdf:
        mock_kdf.side_effect = lambda _key, _salt: MagicMock()

        f1 = await service._get_fernet_for_chat(chat_id)  # noqa: SLF001
        f2 = await service._get_fernet_for_chat(chat_id)  # noqa: SLF001
        f3 = await service._get_fernet_for_chat(chat_id)  # noqa: SLF001

        assert f1 is not None
        assert f1 is f2
        assert f2 is f3
        assert mock_kdf.call_count == 1
