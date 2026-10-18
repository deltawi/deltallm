"""AES-256-GCM envelope for secrets stored at rest.

The format is ``v1.<key id>.<urlsafe base64(nonce + ciphertext)>``. The key id is
the first 12 hex characters of SHA-256(key). Each owner passes its own
associated data (AAD), so a ciphertext of one feature cannot be used by another.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import SecretStr

_ENVELOPE_VERSION = "v1"
_NONCE_BYTES = 12
_TAG_BYTES = 16
_KEY_ID_LENGTH = 12
_LOWERCASE_HEX_CHARACTERS = frozenset("0123456789abcdef")


class SecretEnvelopeError(ValueError):
    """Raised when key material or an envelope is not valid."""


def _decode_urlsafe_base64(value: str, *, error_message: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    try:
        return base64.b64decode(value + padding, altchars=b"-_", validate=True)
    except (ValueError, TypeError):
        # Do not chain the decoder error: it can quote key material.
        raise SecretEnvelopeError(error_message) from None


def _encode_urlsafe_base64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def decode_envelope_key(value: SecretStr | str, *, key_name: str) -> bytes:
    """Decode a URL-safe base64 key that must hold exactly 32 bytes."""

    raw_value = value.get_secret_value() if isinstance(value, SecretStr) else str(value or "")
    normalized = raw_value.strip()
    if not normalized:
        raise SecretEnvelopeError(f"{key_name} is required")
    decoded = _decode_urlsafe_base64(normalized, error_message=f"{key_name} is not valid base64")
    if len(decoded) != 32:
        raise SecretEnvelopeError(f"{key_name} must decode to exactly 32 bytes")
    return decoded


class SecretEnvelopeCipher:
    def __init__(self, key: bytes, *, key_name: str = "encryption key") -> None:
        if len(key) != 32:
            raise SecretEnvelopeError(f"{key_name} must be exactly 32 bytes")
        self._key_id = hashlib.sha256(bytes(key)).hexdigest()[:_KEY_ID_LENGTH]
        self._cipher = AESGCM(bytes(key))

    @property
    def key_id(self) -> str:
        return self._key_id

    def encrypt(self, plaintext: bytes, *, aad: bytes) -> str:
        nonce = os.urandom(_NONCE_BYTES)
        ciphertext = self._cipher.encrypt(nonce, plaintext, aad)
        return ".".join(
            (_ENVELOPE_VERSION, self._key_id, _encode_urlsafe_base64(nonce + ciphertext))
        )

    def try_decrypt(self, envelope: str, *, aad: bytes) -> bytes | None:
        """Return the plaintext, or None for a wrong key, wrong AAD or a damaged value."""

        try:
            version, key_id, encoded_payload = str(envelope or "").split(".", 2)
        except ValueError:
            return None
        valid_key_id = (
            len(key_id) == _KEY_ID_LENGTH
            and key_id.isascii()
            and all(character in _LOWERCASE_HEX_CHARACTERS for character in key_id)
        )
        if (
            version != _ENVELOPE_VERSION
            or not valid_key_id
            or not hmac.compare_digest(key_id.encode("ascii"), self._key_id.encode("ascii"))
        ):
            return None
        try:
            payload = _decode_urlsafe_base64(encoded_payload, error_message="invalid envelope")
        except SecretEnvelopeError:
            return None
        if len(payload) < _NONCE_BYTES + _TAG_BYTES:
            return None
        try:
            return self._cipher.decrypt(payload[:_NONCE_BYTES], payload[_NONCE_BYTES:], aad)
        except InvalidTag:
            return None
