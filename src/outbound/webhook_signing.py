"""One HMAC signature scheme for every outbound DeltaLLM webhook."""

from __future__ import annotations

import hashlib
import hmac

from pydantic import SecretStr


def webhook_signature(*, signing_secret: SecretStr | str, timestamp: int, body: bytes) -> str:
    """Return ``v1=<hex>``: HMAC-SHA256 over ``"<timestamp>." + body``."""

    secret = (
        signing_secret.get_secret_value()
        if isinstance(signing_secret, SecretStr)
        else str(signing_secret)
    )
    signed_payload = str(int(timestamp)).encode("ascii") + b"." + body
    digest = hmac.new(secret.encode("utf-8"), signed_payload, hashlib.sha256).hexdigest()
    return f"v1={digest}"
