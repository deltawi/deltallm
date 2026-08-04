from __future__ import annotations

import base64
import os

import pytest

from src.batch.webhooks.crypto import (
    BatchWebhookCipher,
    BatchWebhookCryptoError,
    decode_batch_webhook_encryption_key,
)
from src.batch.webhooks.models import parse_batch_webhook_request


def _key(value: bytes | None = None) -> str:
    return base64.urlsafe_b64encode(value or os.urandom(32)).decode("ascii")


def test_webhook_cipher_round_trips_without_plaintext_leakage() -> None:
    config = parse_batch_webhook_request(
        {
            "url": "https://callbacks.example.com/batches",
            "signing_secret": "signing-secret-with-at-least-32-bytes",
        }
    )
    cipher = BatchWebhookCipher.from_config(_key())

    first_envelope = cipher.encrypt(config)
    second_envelope = cipher.encrypt(config)

    assert first_envelope.startswith(f"v1.{cipher.key_id}.")
    assert first_envelope != second_envelope
    assert config.url not in first_envelope
    assert config.signing_secret.get_secret_value() not in first_envelope
    assert cipher.decrypt(first_envelope) == config
    assert cipher.decrypt(second_envelope) == config


def test_webhook_cipher_accepts_unpadded_urlsafe_key() -> None:
    encoded = _key(bytes(range(32))).rstrip("=")
    assert decode_batch_webhook_encryption_key(encoded) == bytes(range(32))


@pytest.mark.parametrize("value", ["", "not-base64!", _key(b"short")])
def test_webhook_cipher_rejects_invalid_keys(value: str) -> None:
    with pytest.raises(BatchWebhookCryptoError, match="encryption key"):
        BatchWebhookCipher.from_config(value)


def test_webhook_cipher_rejects_wrong_key_tampering_and_malformed_envelopes() -> None:
    config = parse_batch_webhook_request(
        {
            "url": "https://callbacks.example.com/batches",
            "signing_secret": "signing-secret-with-at-least-32-bytes",
        }
    )
    cipher = BatchWebhookCipher.from_config(_key(bytes(range(32))))
    envelope = cipher.encrypt(config)

    wrong_cipher = BatchWebhookCipher.from_config(_key(bytes(reversed(range(32)))))
    with pytest.raises(BatchWebhookCryptoError, match="configuration is invalid"):
        wrong_cipher.decrypt(envelope)

    version, key_id, encoded = envelope.split(".", 2)
    replacement = "A" if encoded[len(encoded) // 2] != "A" else "B"
    tampered = encoded[: len(encoded) // 2] + replacement + encoded[len(encoded) // 2 + 1 :]
    with pytest.raises(BatchWebhookCryptoError, match="configuration is invalid"):
        cipher.decrypt(f"{version}.{key_id}.{tampered}")

    for malformed in ("", "v1", f"v1.{key_id}.not+urlsafe"):
        with pytest.raises(BatchWebhookCryptoError, match="configuration is invalid"):
            cipher.decrypt(malformed)
