"""Generic Fernet payload encryption helpers."""

from cryptography.fernet import Fernet


def encrypt_payload(data: str, fernet: Fernet) -> str:
    """Encrypt string payload using Fernet."""
    return fernet.encrypt(data.encode()).decode()


def decrypt_payload(encrypted_data: str, fernet: Fernet) -> str:
    """Decrypt Fernet-encrypted payload."""
    return fernet.decrypt(encrypted_data.encode()).decode()
