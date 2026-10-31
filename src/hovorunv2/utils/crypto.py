"""Generic Fernet payload encryption helpers."""

from cryptography.fernet import Fernet


def encrypt_payload(data: str, fernet: Fernet) -> str:
    """Encrypt a string payload into a Fernet token."""
    return fernet.encrypt(data.encode()).decode()


def decrypt_payload(encrypted_data: str, fernet: Fernet) -> str:
    """Decrypt a Fernet token into its plaintext string."""
    return fernet.decrypt(encrypted_data.encode()).decode()
