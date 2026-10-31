"""Tests for MessageService per-chat encryption."""

import json
from functools import partial
from unittest.mock import patch

import pytest
from aiogram import types
from cryptography.fernet import Fernet
from dishka import AsyncContainer
from redis.exceptions import ConnectionError as RedisConnectionError

import hovorunv2.application.messaging.message_service as message_module
from hovorunv2.application.messaging.crypto import (
    decrypt_payload,
    encrypt_payload,
    get_fernet_for_chat,
    verify_encryption_key,
)
from hovorunv2.application.messaging.message_service import MessageService
from hovorunv2.infrastructure.runtime.cache import CacheService


@pytest.fixture
async def encryption_service(test_container: AsyncContainer) -> MessageService:
    """Use real PostgreSQL chat storage and worker-isolated Valkey."""
    service = await test_container.get(MessageService)
    await service.cache.delete("msg:123:456")
    await service.cache.delete("msg:12345:67890")
    return service


def encrypted_message(text: str, chat_id: int = 123, message_id: int = 456) -> types.Message:
    """Build the minimum real Telegram message."""
    return types.Message(message_id=message_id, date=123456789, chat=types.Chat(id=chat_id, type="private"), text=text)


async def test_cache_message_encryption(encryption_service: MessageService) -> None:
    """Encryption generates and persists a salt before writing ciphertext."""
    await encryption_service.cache_message(encrypted_message("hello"))
    chat = await encryption_service.chat_service.get_chat(123)
    assert chat is not None
    assert chat.encryption_salt
    payload = await encryption_service.cache.get_text("msg:123:456")
    assert payload is not None
    assert "hello" not in payload


async def test_get_message_decryption(encryption_service: MessageService) -> None:
    """Decrypt an actual stored ciphertext."""
    await encryption_service.cache_message(encrypted_message("secret"))
    retrieved = await encryption_service.get_message(123, 456)
    assert retrieved is not None
    assert retrieved.text == "secret"


async def test_get_message_no_encryption_fails_when_enabled(encryption_service: MessageService) -> None:
    """Reject plaintext stored in the real cache."""
    await encryption_service.cache.set("msg:123:456", json.dumps(encrypted_message("plain").model_dump(mode="json")))
    with pytest.raises(ValueError, match="Cached message is unreadable"):
        await encryption_service.get_message(123, 456)


async def test_fernet_derivation_cached_per_chat(
    encryption_service: MessageService, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reuse the same derived cryptographic object for a chat."""
    calls: list[str] = []
    monkeypatch.setattr(message_module, "get_fernet_for_chat", partial(_record_derivation, calls))
    f1 = await encryption_service._get_fernet_for_chat(999)  # noqa: SLF001
    f2 = await encryption_service._get_fernet_for_chat(999)  # noqa: SLF001
    f3 = await encryption_service._get_fernet_for_chat(999)  # noqa: SLF001
    assert f1 is f2 is f3
    assert len(calls) == 1


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
    with patch("hovorunv2.application.messaging.crypto.PBKDF2HMAC.derive", side_effect=RuntimeError("KDF error")):
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


async def test_cache_message_rejects_missing_encryption_key(encryption_service: MessageService) -> None:
    """Missing encryption key never writes plaintext."""
    encryption_service.settings.cache_encryption_key = None
    with pytest.raises(ValueError, match="encryption key is unavailable"):
        await encryption_service.cache_message(encrypted_message("sensitive message", 12345, 67890))
    assert await encryption_service.cache.get_text("msg:12345:67890") is None


async def test_get_message_rejects_missing_encryption_key(encryption_service: MessageService) -> None:
    """Missing key rejects an existing cache entry."""
    encryption_service.settings.cache_encryption_key = None
    await encryption_service.cache.set("msg:12345:67890", "some_encrypted_or_plain_data")
    with pytest.raises(ValueError, match="encryption key is unavailable"):
        await encryption_service.get_message(12345, 67890)


async def test_get_message_rejects_corrupted_ciphertext(encryption_service: MessageService) -> None:
    """Corrupt ciphertext is unreadable."""
    await encryption_service.cache.set("msg:123:456", "gAAAAABcorrupt_not_valid_fernet_data_here")
    with pytest.raises(ValueError, match="Cached message is unreadable"):
        await encryption_service.get_message(123, 456)


async def test_cache_message_propagates_storage_failure(encryption_service: MessageService) -> None:
    """An unreachable real Valkey endpoint propagates its connection failure."""
    cache = CacheService("redis://127.0.0.1:1/1")
    service = MessageService(cache, encryption_service.chat_service, encryption_service.settings)
    try:
        with pytest.raises(RedisConnectionError):
            await service.cache_message(encrypted_message("hello"))
    finally:
        await cache.close()


def test_encryption_self_check_suppresses_underlying_key_error() -> None:
    """An unusable synthetic passphrase never becomes a chained cryptographic exception."""
    with pytest.raises(ValueError, match="invalid or unusable") as error:
        verify_encryption_key("synthetic-\ud800")
    assert error.value.__cause__ is None
    assert error.value.__suppress_context__


def _record_derivation(calls: list[str], key: str, salt: str) -> Fernet:
    """Count cryptographic derivations while executing the real implementation."""
    calls.append(salt)
    return get_fernet_for_chat(key, salt)
