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
