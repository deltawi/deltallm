from __future__ import annotations

import json

from pydantic import SecretStr

from src.batch.webhooks.models import (
    BatchWebhookRequest,
    canonical_batch_webhook_config_bytes,
    parse_batch_webhook_request,
)
from src.secret_envelope import (
    SecretEnvelopeCipher,
    SecretEnvelopeError,
    decode_envelope_key,
)
from src.secret_envelope import _NONCE_BYTES as _NONCE_BYTES  # noqa: F401  (test helper)

_KEY_NAME = "batch webhook encryption key"
_AAD = b"deltallm:batch-webhook:v1"


class BatchWebhookCryptoError(ValueError):
    """Raised when webhook key material or an encrypted envelope is invalid."""


def decode_batch_webhook_encryption_key(value: SecretStr | str) -> bytes:
    try:
        return decode_envelope_key(value, key_name=_KEY_NAME)
    except SecretEnvelopeError as exc:
        raise BatchWebhookCryptoError(str(exc)) from None


class BatchWebhookCipher:
    def __init__(self, key: bytes) -> None:
        try:
            self._envelope = SecretEnvelopeCipher(key, key_name=_KEY_NAME)
        except SecretEnvelopeError as exc:
            raise BatchWebhookCryptoError(str(exc)) from None

    @classmethod
    def from_config(cls, value: SecretStr | str) -> "BatchWebhookCipher":
        return cls(decode_batch_webhook_encryption_key(value))

    @property
    def key_id(self) -> str:
        return self._envelope.key_id

    def encrypt(self, config: BatchWebhookRequest) -> str:
        return self._envelope.encrypt(canonical_batch_webhook_config_bytes(config), aad=_AAD)

    def _try_decrypt(self, envelope: str) -> BatchWebhookRequest | None:
        plaintext: bytes | None = None
        decoded: object | None = None
        try:
            plaintext = self._envelope.try_decrypt(envelope, aad=_AAD)
            if plaintext is None:
                return None
            decoded = json.loads(plaintext.decode("utf-8"))
            if not isinstance(decoded, dict):
                return None
            return parse_batch_webhook_request(decoded, allow_http=True, allowed_ports=None)
        except (
            BatchWebhookCryptoError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            RecursionError,
            TypeError,
            ValueError,
        ):
            return None
        finally:
            plaintext = None
            decoded = None

    def decrypt(self, envelope: str) -> BatchWebhookRequest:
        config = self._try_decrypt(envelope)
        if config is None:
            raise BatchWebhookCryptoError("batch webhook encrypted configuration is invalid")
        return config
