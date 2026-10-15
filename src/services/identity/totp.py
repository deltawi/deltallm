"""RFC 6238 TOTP helpers for DeltaLLM account MFA (SHA-1, 6 digits, 30-second steps)."""

from __future__ import annotations

from datetime import UTC, datetime
import base64
import hashlib
import hmac
import secrets
import struct
import urllib.parse


def generate_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def totp_uri(*, issuer: str, secret: str, account_name: str) -> str:
    label = urllib.parse.quote(f"{issuer}:{account_name}", safe="")
    encoded_issuer = urllib.parse.quote(issuer, safe="")
    return f"otpauth://totp/{label}?secret={secret}&issuer={encoded_issuer}&algorithm=SHA1&digits=6&period=30"


def verify_totp(secret: str, code: str) -> bool:
    """Accept the current code and one step on each side for clock drift."""
    if not code.isdigit() or len(code) != 6:
        return False
    for offset in (-1, 0, 1):
        if totp_code(secret, step_offset=offset) == code:
            return True
    return False


def totp_code(secret: str, step_offset: int = 0) -> str:
    normalized = secret.upper() + "=" * ((8 - len(secret) % 8) % 8)
    key = base64.b32decode(normalized.encode("ascii"), casefold=True)
    counter = int(datetime.now(UTC).timestamp() // 30) + step_offset
    msg = struct.pack(">Q", counter)
    digest = hmac.new(key, msg, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    binary = (
        ((digest[offset] & 0x7F) << 24)
        | ((digest[offset + 1] & 0xFF) << 16)
        | ((digest[offset + 2] & 0xFF) << 8)
        | (digest[offset + 3] & 0xFF)
    )
    otp = binary % 1_000_000
    return f"{otp:06d}"
