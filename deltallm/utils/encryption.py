"""Encryption utilities for ProxyLLM.

This module provides Fernet-based encryption for storing sensitive data
like API keys at rest in the database.
"""

import base64
import hashlib
import os
import secrets
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken


class EncryptionError(Exception):
    """Raised when encryption/decryption fails."""
    pass


class EncryptionManager:
    """Manages encryption and decryption of sensitive data.

    Uses Fernet symmetric encryption which provides:
    - AES-128-CBC encryption
    - HMAC-SHA256 authentication
    - Timestamps for key rotation support

    The encryption key is derived from PROXYLLM_ENCRYPTION_KEY environment
    variable using SHA-256 to ensure a valid 32-byte key.
    """

    _instance: Optional["EncryptionManager"] = None
    _fernet: Optional[Fernet] = None

    def __new__(cls) -> "EncryptionManager":
        """Singleton pattern to ensure consistent encryption across the app."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        """Initialize the encryption manager."""
        if self._fernet is None:
            self._initialize_fernet()

    def _initialize_fernet(self) -> None:
        """Initialize Fernet cipher from environment key."""
        key = os.environ.get("PROXYLLM_ENCRYPTION_KEY")

        if not key:
            # Generate a warning but allow operation with a default key
            # This is useful for development/testing but should never be used in production
            import warnings
            warnings.warn(
                "PROXYLLM_ENCRYPTION_KEY not set. Using default key. "
                "This is insecure and should only be used for development.",
                UserWarning,
            )
            # Use a deterministic default key for development
            key = "deltallm-dev-encryption-key-not-for-production"

        # Derive a 32-byte key using SHA-256 and encode for Fernet
        derived_key = hashlib.sha256(key.encode()).digest()
        fernet_key = base64.urlsafe_b64encode(derived_key)

        self._fernet = Fernet(fernet_key)

    def encrypt(self, plaintext: str) -> str:
        """Encrypt a string.

        Args:
            plaintext: The string to encrypt

        Returns:
            Base64-encoded encrypted string

        Raises:
            EncryptionError: If encryption fails
        """
        if not plaintext:
            return ""

        try:
            encrypted = self._fernet.encrypt(plaintext.encode("utf-8"))
            return base64.urlsafe_b64encode(encrypted).decode("utf-8")
        except Exception as e:
            raise EncryptionError(f"Encryption failed: {e}") from e

    def decrypt(self, ciphertext: str) -> str:
        """Decrypt an encrypted string.

        Args:
            ciphertext: Base64-encoded encrypted string

        Returns:
            Decrypted plaintext string

        Raises:
            EncryptionError: If decryption fails (invalid key or corrupted data)
        """
        if not ciphertext:
            return ""

        try:
            encrypted = base64.urlsafe_b64decode(ciphertext.encode("utf-8"))
            decrypted = self._fernet.decrypt(encrypted)
            return decrypted.decode("utf-8")
        except InvalidToken:
            raise EncryptionError(
                "Decryption failed: Invalid token. "
                "This may indicate a wrong encryption key or corrupted data."
            )
        except Exception as e:
            raise EncryptionError(f"Decryption failed: {e}") from e

    @staticmethod
    def generate_key() -> str:
        """Generate a secure encryption key.

        Returns:
            A secure random key suitable for PROXYLLM_ENCRYPTION_KEY
        """
        return secrets.token_urlsafe(32)

    @classmethod
    def reset(cls) -> None:
        """Reset the singleton instance. Useful for testing."""
        cls._instance = None
        cls._fernet = None


# Module-level convenience functions
_manager: Optional[EncryptionManager] = None


def get_encryption_manager() -> EncryptionManager:
    """Get the global encryption manager instance.

    Returns:
        The singleton EncryptionManager instance
    """
    global _manager
    if _manager is None:
        _manager = EncryptionManager()
    return _manager


def encrypt_api_key(api_key: str) -> str:
    """Encrypt an API key for storage.

    Args:
        api_key: The API key to encrypt

    Returns:
        Encrypted API key string
    """
    return get_encryption_manager().encrypt(api_key)


def decrypt_api_key(encrypted_key: str) -> str:
    """Decrypt an API key from storage.

    Args:
        encrypted_key: The encrypted API key

    Returns:
        Decrypted API key string
    """
    return get_encryption_manager().decrypt(encrypted_key)


def generate_encryption_key() -> str:
    """Generate a new encryption key.

    Returns:
        A secure random key for PROXYLLM_ENCRYPTION_KEY
    """
    return EncryptionManager.generate_key()
