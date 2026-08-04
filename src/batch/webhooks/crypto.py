from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import SecretStr

from src.batch.webhooks.models import (
    BatchWebhookRequest,
    canonical_batch_webhook_config_bytes,
    parse_batch_webhook_request,
)


_ENVELOPE_VERSION = "v1"
_NONCE_BYTES = 12
_AAD = b"deltallm:batch-webhook:v1"


class BatchWebhookCryptoError(ValueError):
    """Raised when webhook key material or an encrypted envelope is invalid."""


def _decode_urlsafe_base64(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    try:
        return base64.b64decode(value + padding, altchars=b"-_", validate=True)
    except (ValueError, TypeError) as exc:
        raise BatchWebhookCryptoError("batch webhook encryption key is not valid base64") from exc


def _encode_urlsafe_base64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def decode_batch_webhook_encryption_key(value: SecretStr | str) -> bytes:
    raw_value = value.get_secret_value() if isinstance(value, SecretStr) else str(value or "")
    normalized = raw_value.strip()
    if not normalized:
        raise BatchWebhookCryptoError("batch webhook encryption key is required")
    decoded = _decode_urlsafe_base64(normalized)
    if len(decoded) != 32:
        raise BatchWebhookCryptoError(
            "batch webhook encryption key must decode to exactly 32 bytes"
        )
    return decoded


class BatchWebhookCipher:
    def __init__(self, key: bytes) -> None:
        if len(key) != 32:
            raise BatchWebhookCryptoError("batch webhook encryption key must be exactly 32 bytes")
        self._key = bytes(key)
        self._key_id = hashlib.sha256(self._key).hexdigest()[:12]
        self._cipher = AESGCM(self._key)

    @classmethod
    def from_config(cls, value: SecretStr | str) -> "BatchWebhookCipher":
        return cls(decode_batch_webhook_encryption_key(value))

    @property
    def key_id(self) -> str:
        return self._key_id

    def encrypt(self, config: BatchWebhookRequest) -> str:
        nonce = os.urandom(_NONCE_BYTES)
        ciphertext = self._cipher.encrypt(
            nonce,
            canonical_batch_webhook_config_bytes(config),
            _AAD,
        )
        return ".".join(
            (
                _ENVELOPE_VERSION,
                self._key_id,
                _encode_urlsafe_base64(nonce + ciphertext),
            )
        )

    def decrypt(self, envelope: str) -> BatchWebhookRequest:
        try:
            version, key_id, encoded_payload = str(envelope or "").split(".", 2)
            if version != _ENVELOPE_VERSION or not hmac.compare_digest(key_id, self._key_id):
                raise BatchWebhookCryptoError("batch webhook encrypted configuration is invalid")
            try:
                payload = _decode_urlsafe_base64(encoded_payload)
            except BatchWebhookCryptoError as exc:
                raise BatchWebhookCryptoError(
                    "batch webhook encrypted configuration is invalid"
                ) from exc
            if len(payload) < _NONCE_BYTES + 16:
                raise BatchWebhookCryptoError("batch webhook encrypted configuration is invalid")
            plaintext = self._cipher.decrypt(payload[:_NONCE_BYTES], payload[_NONCE_BYTES:], _AAD)
            decoded = json.loads(plaintext.decode("utf-8"))
            if not isinstance(decoded, dict):
                raise BatchWebhookCryptoError("batch webhook encrypted configuration is invalid")
            return parse_batch_webhook_request(decoded, allow_http=True, allowed_ports=None)
        except BatchWebhookCryptoError:
            raise
        except (
            InvalidTag,
            UnicodeDecodeError,
            json.JSONDecodeError,
            ValueError,
        ) as exc:
            raise BatchWebhookCryptoError(
                "batch webhook encrypted configuration is invalid"
            ) from exc
