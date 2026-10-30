"""Tests for MessageService per-chat encryption."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram import types

from hovorunv2.application.crypto import (
    decrypt_payload,
    encrypt_payload,
    get_fernet_for_chat,
    verify_encryption_key,
)
from hovorunv2.application.services.message_service import MessageService


@pytest.fixture
def mock_cache_service() -> MagicMock:
    """Create a mock cache service."""
    service = MagicMock()
    service.set = AsyncMock()
    service.get_text = AsyncMock()
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

    mock_cache_service.get_text.return_value = encrypted_payload

    retrieved = await service.get_message(chat_id, message_id)

    assert retrieved is not None
    assert retrieved.text == "secret"


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
    mock_cache_service.get_text.return_value = json.dumps(original_data)

    # Retrieval rejects plaintext instead of treating it as a missing entry.
    with pytest.raises(ValueError, match="Cached message is unreadable"):
        await service.get_message(chat_id, message_id)


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


# ==============================================================================
# Startup Encryption Validation & Compatibility Tests
# ==============================================================================


def test_verify_encryption_key_rejects_missing_or_blank() -> None:
    """Validate that absent or blank master keys fail with ValueError."""
    with pytest.raises(ValueError, match="Master encryption key is missing or blank"):
        verify_encryption_key(None)

    with pytest.raises(ValueError, match="Master encryption key is missing or blank"):
        verify_encryption_key("")

    with pytest.raises(ValueError, match="Master encryption key is missing or blank"):
        verify_encryption_key("   \t\n  ")


def test_verify_encryption_key_accepts_usable_passphrase() -> None:
    """Validate that any non-blank passphrase passes the derivation self-check."""
    # Standard passphrase, does not need to be a raw 32-byte Fernet key
    verify_encryption_key("hovorun_super_secret_production_passphrase_2026")
    verify_encryption_key("short_simple_passphrase")


def test_verify_encryption_key_does_not_leak_key_value() -> None:
    """Validate that exceptions and logs never expose the secret passphrase."""
    secret_passphrase = "TOP_SECRET_SUPER_SENSITIVE_PASSPHRASE_NEVER_LOG_THIS"  # noqa: S105

    # Simulate derivation failure by patching PBKDF2HMAC to raise
    with patch("hovorunv2.application.crypto.PBKDF2HMAC.derive", side_effect=RuntimeError("KDF error")):
        with pytest.raises(ValueError, match="Master encryption key is invalid or unusable") as exc_info:
            verify_encryption_key(secret_passphrase)

        assert secret_passphrase not in str(exc_info.value)


def test_existing_passphrase_format_decrypts_synthetic_salt_fixture() -> None:
    """Verify an existing PBKDF2 passphrase decrypts a fixture encrypted with a fixed synthetic salt."""
    passphrase = "my_legacy_bot_passphrase"  # noqa: S105
    fixed_synthetic_salt_hex = "0123456789abcdef" * 2
    plain_text = "test_compatibility_payload_with_synthetic_salt"

    # Encrypt with existing PBKDF2 derivation
    fernet = get_fernet_for_chat(passphrase, fixed_synthetic_salt_hex)
    ciphertext = encrypt_payload(plain_text, fernet)

    # Prove independent derivation with same passphrase and salt decrypts ciphertext
    decryption_fernet = get_fernet_for_chat(passphrase, fixed_synthetic_salt_hex)
    decrypted = decrypt_payload(ciphertext, decryption_fernet)

    assert decrypted == plain_text


# ==============================================================================
# Removal of Plaintext Cache Fallbacks
# ==============================================================================


async def test_cache_message_rejects_missing_encryption_key(
    mock_cache_service: MagicMock,
    mock_chat_service: MagicMock,
) -> None:
    """Verify caching fails safely when encryption key is not configured."""
    settings = MagicMock()
    settings.cache_encryption_key = None
    service = MessageService(mock_cache_service, mock_chat_service, settings)

    message = MagicMock(spec=types.Message)
    message.chat = MagicMock(id=12345)
    message.message_id = 67890
    message.model_dump.return_value = {"text": "sensitive message"}

    with pytest.raises(ValueError, match="encryption key is unavailable"):
        await service.cache_message(message)

    # Cache write must be skipped; never store plaintext
    mock_cache_service.set.assert_not_called()


async def test_get_message_rejects_missing_encryption_key(
    mock_cache_service: MagicMock,
    mock_chat_service: MagicMock,
) -> None:
    """Verify retrieval fails when encryption key is not configured."""
    settings = MagicMock()
    settings.cache_encryption_key = None
    service = MessageService(mock_cache_service, mock_chat_service, settings)

    mock_cache_service.get_text.return_value = "some_encrypted_or_plain_data"

    with pytest.raises(ValueError, match="encryption key is unavailable"):
        await service.get_message(12345, 67890)


async def test_get_message_rejects_corrupted_ciphertext(
    mock_cache_service: MagicMock,
    mock_chat_service: MagicMock,
    mock_settings: MagicMock,
) -> None:
    """Verify corrupt ciphertext is rejected as unreadable."""
    service = MessageService(mock_cache_service, mock_chat_service, mock_settings)
    chat = MagicMock(encryption_salt="0123456789abcdef" * 2)
    mock_chat_service.get_or_create_chat.return_value = chat

    mock_cache_service.get_text.return_value = "gAAAAABcorrupt_not_valid_fernet_data_here"

    with pytest.raises(ValueError, match="Cached message is unreadable"):
        await service.get_message(123, 456)


async def test_cache_message_propagates_storage_failure(
    mock_cache_service: MagicMock,
    mock_chat_service: MagicMock,
    mock_settings: MagicMock,
) -> None:
    """Verify the required storage operation propagates a Valkey failure."""
    service = MessageService(mock_cache_service, mock_chat_service, mock_settings)
    chat = MagicMock(encryption_salt="0123456789abcdef" * 2)
    mock_chat_service.get_or_create_chat.return_value = chat
    mock_cache_service.set.side_effect = ConnectionError("Valkey connection refused")

    message = MagicMock(spec=types.Message)
    message.chat = MagicMock(id=123)
    message.message_id = 456
    message.model_dump.return_value = {"text": "hello"}

    # Should not raise exception
    with pytest.raises(ConnectionError):
        await service.cache_message(message)


def test_encryption_self_check_suppresses_underlying_key_error() -> None:
    """An unusable synthetic passphrase never becomes a chained cryptographic exception."""
    with pytest.raises(ValueError, match="invalid or unusable") as error:
        verify_encryption_key("synthetic-\ud800")
    assert error.value.__cause__ is None
    assert error.value.__suppress_context__
