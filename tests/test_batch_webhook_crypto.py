from __future__ import annotations

import base64
import json
import os

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from src.batch.webhooks import crypto as webhook_crypto
from src.batch.webhooks.crypto import (
    BatchWebhookCipher,
    BatchWebhookCryptoError,
    decode_batch_webhook_encryption_key,
)
from src.batch.webhooks.models import parse_batch_webhook_request


def _key(value: bytes | None = None) -> str:
    return base64.urlsafe_b64encode(value or os.urandom(32)).decode("ascii")


def _authenticated_envelope(
    cipher: BatchWebhookCipher,
    *,
    key: bytes,
    plaintext: bytes,
) -> str:
    nonce = b"\x01" * webhook_crypto._NONCE_BYTES
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, webhook_crypto._AAD)
    encoded_payload = base64.urlsafe_b64encode(nonce + ciphertext).decode("ascii").rstrip("=")
    return f"v1.{cipher.key_id}.{encoded_payload}"


def _assert_sanitized_crypto_error(
    error: BatchWebhookCryptoError,
    *,
    sensitive_value: str | None = None,
) -> None:
    assert error.__cause__ is None
    assert error.__context__ is None
    if sensitive_value is None:
        return

    assert sensitive_value not in str(error)
    assert sensitive_value not in repr(error)
    traceback = error.__traceback__
    while traceback is not None:
        frame = traceback.tb_frame
        if frame.f_code.co_filename == webhook_crypto.__file__:
            assert sensitive_value not in repr(frame.f_locals)
        traceback = traceback.tb_next


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
    with pytest.raises(BatchWebhookCryptoError, match="configuration is invalid") as exc_info:
        wrong_cipher.decrypt(envelope)
    _assert_sanitized_crypto_error(exc_info.value)

    version, key_id, encoded = envelope.split(".", 2)
    replacement = "A" if encoded[len(encoded) // 2] != "A" else "B"
    tampered = encoded[: len(encoded) // 2] + replacement + encoded[len(encoded) // 2 + 1 :]
    with pytest.raises(BatchWebhookCryptoError, match="configuration is invalid") as exc_info:
        cipher.decrypt(f"{version}.{key_id}.{tampered}")
    _assert_sanitized_crypto_error(exc_info.value)

    for malformed in (
        "",
        "v1",
        f"v1.{key_id}.not+urlsafe",
        "v1.é.payload",
        "v1.12345678901g.payload",
    ):
        with pytest.raises(BatchWebhookCryptoError, match="configuration is invalid") as exc_info:
            cipher.decrypt(malformed)
        _assert_sanitized_crypto_error(exc_info.value)


@pytest.mark.parametrize(
    "plaintext",
    [
        b'{"url":"https://callbacks.example.com/batches",'
        b'"signing_secret":"sensitive-signing-secret-1234567890",',
        json.dumps(
            {
                "url": "https://callbacks.example.com/batches",
                "signing_secret": "sensitive-signing-secret-1234567890",
                "unsupported": True,
            }
        ).encode("utf-8"),
    ],
)
def test_webhook_cipher_discards_plaintext_from_decryption_errors(plaintext: bytes) -> None:
    key = bytes(range(32))
    sensitive_value = "sensitive-signing-secret-1234567890"
    cipher = BatchWebhookCipher(key)
    envelope = _authenticated_envelope(cipher, key=key, plaintext=plaintext)

    with pytest.raises(BatchWebhookCryptoError, match="configuration is invalid") as exc_info:
        cipher.decrypt(envelope)

    _assert_sanitized_crypto_error(exc_info.value, sensitive_value=sensitive_value)
