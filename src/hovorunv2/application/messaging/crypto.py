"""Per-chat key derivation and required application encryption-key validation."""

import base64

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from hovorunv2.utils.crypto import decrypt_payload, encrypt_payload

PBKDF2_ITERATIONS = 100_000


def get_fernet_for_chat(encryption_key: str, salt_hex: str) -> Fernet:
    """Derive a unique Fernet key for a chat using PBKDF2 and DB salt."""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=bytes.fromhex(salt_hex),
        iterations=PBKDF2_ITERATIONS,
    )
    derived_key = kdf.derive(encryption_key.encode())
    fernet_key = base64.urlsafe_b64encode(derived_key)
    return Fernet(fernet_key)


def verify_encryption_key(key: str | None) -> None:
    """Validate that master encryption key is configured, non-blank, and usable."""
    if not key or not key.strip():
        msg = "Master encryption key is missing or blank. Please configure CACHE_ENCRYPTION_KEY."
        raise ValueError(msg)

    _require_encryption_roundtrip(key)


def _require_encryption_roundtrip(key: str) -> None:
    """Require a usable key without exposing underlying cryptographic exceptions."""
    synthetic_salt_hex = "00" * 16
    try:
        fernet = get_fernet_for_chat(key, synthetic_salt_hex)
        test_payload = "hovorun_startup_encryption_self_check"
        encrypted = encrypt_payload(test_payload, fernet)
        decrypted = decrypt_payload(encrypted, fernet)
        is_match = decrypted == test_payload
    except Exception:
        msg = "Master encryption key is invalid or unusable for key derivation."
        raise ValueError(msg) from None

    if not is_match:
        msg = "Encryption self-check failed: decrypted payload mismatch."
        raise ValueError(msg)
