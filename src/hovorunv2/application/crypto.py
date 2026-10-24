"""Cryptographic helpers for per-chat message encryption."""

import base64
from typing import Final

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

PBKDF2_ITERATIONS: Final = 100_000


def get_fernet_for_chat(encryption_key: str, salt_hex: str) -> Fernet:
    """Derive a unique Fernet key for a chat using PBKDF2 and DB salt.

    Args:
        encryption_key: Global encryption key from settings.
        salt_hex: Per-chat hex-encoded salt from database.

    Returns:
        Fernet: Initialized Fernet instance.

    """
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=bytes.fromhex(salt_hex),
        iterations=PBKDF2_ITERATIONS,
    )
    derived_key = kdf.derive(encryption_key.encode())
    fernet_key = base64.urlsafe_b64encode(derived_key)
    return Fernet(fernet_key)


def encrypt_payload(data: str, fernet: Fernet) -> str:
    """Encrypt string payload using Fernet.

    Args:
        data: Plaintext string.
        fernet: Fernet instance.

    Returns:
        str: Encrypted base64 string.

    """
    return fernet.encrypt(data.encode()).decode()


def decrypt_payload(encrypted_data: str, fernet: Fernet) -> str:
    """Decrypt Fernet-encrypted payload.

    Args:
        encrypted_data: Encrypted base64 string.
        fernet: Fernet instance.

    Returns:
        str: Decrypted plaintext string.

    """
    return fernet.decrypt(encrypted_data.encode()).decode()


def verify_encryption_key(key: str | None) -> None:
    """Validate that master encryption key is configured, non-blank, and usable.

    Performs a derivation, encryption, and decryption self-test using a fixed synthetic salt.
    Does not log or expose the key value in exceptions.

    Args:
        key: Master encryption passphrase from configuration.

    Raises:
        ValueError: If key is absent, blank, or fails cryptographic self-check.

    """
    if not key or not key.strip():
        msg = "Master encryption key is missing or blank. Please configure CACHE_ENCRYPTION_KEY."
        raise ValueError(msg)

    # Use a fixed synthetic 16-byte hex salt for startup self-check
    synthetic_salt_hex = "00" * 16
    try:
        fernet = get_fernet_for_chat(key, synthetic_salt_hex)
        test_payload = "hovorun_startup_encryption_self_check"
        encrypted = encrypt_payload(test_payload, fernet)
        decrypted = decrypt_payload(encrypted, fernet)
        is_match = decrypted == test_payload
    except Exception as exc:
        msg = "Master encryption key is invalid or unusable for key derivation."
        raise ValueError(msg) from exc

    if not is_match:
        msg = "Encryption self-check failed: decrypted payload mismatch."
        raise ValueError(msg)
