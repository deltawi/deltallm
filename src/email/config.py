from __future__ import annotations

from urllib.parse import urlparse

from src.email.models import EmailConfigurationError


def normalize_email_base_url(value: str | None) -> str:
    normalized = str(value or "").strip().rstrip("/")
    if not normalized:
        raise EmailConfigurationError("email_base_url is required when email is enabled")
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise EmailConfigurationError("email_base_url must be an absolute http(s) URL")
    return normalized
