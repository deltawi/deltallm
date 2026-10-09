"""Bounded signature fields authenticate private accounting requests."""

from __future__ import annotations

import hashlib
import hmac
import math
import time

ACCOUNTING_INTERNAL_PREFIX = "/internal/accounting/v1"
ACCOUNTING_SIGNATURE_HEADER = "x-deltallm-accounting-signature"
ACCOUNTING_TIMESTAMP_HEADER = "x-deltallm-accounting-timestamp"
ACCOUNTING_MAX_BODY_BYTES = 1_048_576
ACCOUNTING_MAX_RESPONSE_BYTES = 1_048_576
ACCOUNTING_SIGNATURE_SKEW_SECONDS = 30


def accounting_signature(secret: str, *, timestamp: str, path: str, body: bytes) -> str:
    message = timestamp.encode() + b"\n" + path.encode() + b"\n" + body
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def verify_accounting_signature(
    secret: str, *, timestamp: str, signature: str, path: str, body: bytes, now: float | None = None
) -> bool:
    if (
        not secret
        or not timestamp.isascii()
        or not timestamp.isdecimal()
        or not 1 <= len(timestamp) <= 12
        or len(signature) != 64
        or not signature.isascii()
        or len(body) > ACCOUNTING_MAX_BODY_BYTES
    ):
        return False
    current = time.time() if now is None else now
    if (
        not math.isfinite(current)
        or abs(current - int(timestamp)) > ACCOUNTING_SIGNATURE_SKEW_SECONDS
    ):
        return False
    expected = accounting_signature(secret, timestamp=timestamp, path=path, body=body)
    return hmac.compare_digest(expected, signature)
