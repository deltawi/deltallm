from __future__ import annotations

import hashlib
import json
from collections.abc import Collection
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, field_validator


BATCH_WEBHOOK_MAX_URL_LENGTH = 2_048
BATCH_WEBHOOK_MIN_SECRET_BYTES = 32
BATCH_WEBHOOK_MAX_SECRET_BYTES = 4_096


class BatchWebhookValidationError(ValueError):
    """Safe, stable validation error for the public webhook request boundary."""

    def __init__(self, *, code: str, message: str, field: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.field = field

    def as_detail(self) -> dict[str, str]:
        detail = {"code": self.code, "message": str(self)}
        if self.field is not None:
            detail["field"] = self.field
        return detail


def _normalize_webhook_url(value: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError("webhook url is required")
    try:
        normalized.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError("webhook url must contain valid UTF-8 text") from exc
    if len(normalized) > BATCH_WEBHOOK_MAX_URL_LENGTH:
        raise ValueError(f"webhook url must be at most {BATCH_WEBHOOK_MAX_URL_LENGTH} characters")
    if any(ord(character) <= 0x20 or ord(character) == 0x7F for character in normalized):
        raise ValueError("webhook url must not contain whitespace or control characters")
    if "\\" in normalized:
        raise ValueError("webhook url must not contain backslashes")

    try:
        parsed = urlsplit(normalized)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("webhook url is invalid") from exc

    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"}:
        raise ValueError("webhook url scheme must be https (or http when explicitly enabled)")
    if not parsed.hostname:
        raise ValueError("webhook url must include a hostname")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("webhook url must not include user information")
    if "#" in normalized:
        raise ValueError("webhook url must not include a fragment")

    try:
        hostname = parsed.hostname.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise ValueError("webhook url hostname is invalid") from exc
    if not hostname:
        raise ValueError("webhook url must include a hostname")
    if ":" not in hostname:
        hostname = hostname.rstrip(".")
        if len(hostname) > 253:
            raise ValueError("webhook url hostname is invalid")
        labels = hostname.split(".")
        if not hostname or any(
            len(label) > 63
            or label.startswith("-")
            or label.endswith("-")
            or not all(
                character.isascii() and (character.isalnum() or character == "-")
                for character in label
            )
            for label in labels
        ):
            raise ValueError("webhook url hostname is invalid")

    rendered_host = f"[{hostname}]" if ":" in hostname else hostname
    if port is not None and not (
        (scheme == "https" and port == 443) or (scheme == "http" and port == 80)
    ):
        rendered_host = f"{rendered_host}:{port}"

    canonical_url = urlunsplit((scheme, rendered_host, parsed.path or "/", parsed.query, ""))
    if len(canonical_url) > BATCH_WEBHOOK_MAX_URL_LENGTH:
        raise ValueError(f"webhook url must be at most {BATCH_WEBHOOK_MAX_URL_LENGTH} characters")
    return canonical_url


class BatchWebhookRequest(BaseModel):
    """Validated webhook material supplied while creating a batch.

    The model deliberately allows syntactically valid HTTP URLs. Runtime policy is
    applied by ``parse_batch_webhook_request`` so development deployments can opt in
    without weakening the persisted contract.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    url: str = Field(repr=False, exclude=True)
    signing_secret: SecretStr = Field(repr=False, exclude=True)

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        return _normalize_webhook_url(value)

    @field_validator("signing_secret")
    @classmethod
    def validate_signing_secret(cls, value: SecretStr) -> SecretStr:
        length = len(value.get_secret_value().encode("utf-8"))
        if length < BATCH_WEBHOOK_MIN_SECRET_BYTES:
            raise ValueError(
                f"webhook signing_secret must be at least {BATCH_WEBHOOK_MIN_SECRET_BYTES} UTF-8 bytes"
            )
        if length > BATCH_WEBHOOK_MAX_SECRET_BYTES:
            raise ValueError(
                f"webhook signing_secret must be at most {BATCH_WEBHOOK_MAX_SECRET_BYTES} UTF-8 bytes"
            )
        return value


def _validate_batch_webhook_request(value: dict[str, Any]) -> BatchWebhookRequest:
    safe_error: BatchWebhookValidationError | None = None
    try:
        return BatchWebhookRequest.model_validate(value)
    except ValidationError as exc:
        errors = exc.errors(
            include_url=False,
            include_context=False,
            include_input=False,
        )
        first_error = errors[0] if errors else {}
        location = first_error.get("loc") or ()
        field = location[0] if location and location[0] in {"url", "signing_secret"} else None
        error_type = str(first_error.get("type") or "")

        if error_type == "extra_forbidden":
            safe_error = BatchWebhookValidationError(
                code="unsupported_field",
                message="webhook configuration contains unsupported fields",
            )
        elif field == "url":
            safe_error = BatchWebhookValidationError(
                code="invalid_url",
                message="webhook url is invalid",
                field="url",
            )
        elif field == "signing_secret":
            safe_error = BatchWebhookValidationError(
                code="invalid_signing_secret",
                message=(
                    "webhook signing_secret must be between "
                    f"{BATCH_WEBHOOK_MIN_SECRET_BYTES} and "
                    f"{BATCH_WEBHOOK_MAX_SECRET_BYTES} UTF-8 bytes"
                ),
                field="signing_secret",
            )
        else:
            safe_error = BatchWebhookValidationError(
                code="invalid_config",
                message="webhook configuration is invalid",
            )

    # Raising after the ValidationError handler prevents the original input-bearing
    # exception from being retained as ``__context__``.
    if safe_error is None:  # pragma: no cover - model_validate either returns or raises
        raise RuntimeError("webhook configuration validation failed")
    raise safe_error


def parse_batch_webhook_request(
    value: BatchWebhookRequest | dict[str, Any],
    *,
    allow_http: bool = False,
    allowed_ports: Collection[int] | None = (443,),
) -> BatchWebhookRequest:
    config = value if isinstance(value, BatchWebhookRequest) else _validate_batch_webhook_request(value)

    if urlsplit(config.url).scheme == "http" and not allow_http:
        raise BatchWebhookValidationError(
            code="http_not_allowed",
            message="webhook url must use https unless batch_webhook_allow_http is enabled",
            field="url",
        )
    parsed = urlsplit(config.url)
    effective_port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if allowed_ports is not None and effective_port not in {int(port) for port in allowed_ports}:
        raise BatchWebhookValidationError(
            code="port_not_allowed",
            message=f"webhook url port {effective_port} is not allowed",
            field="url",
        )
    return config


def canonical_batch_webhook_config_bytes(config: BatchWebhookRequest) -> bytes:
    payload = {
        "signing_secret": config.signing_secret.get_secret_value(),
        "url": config.url,
    }
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def batch_webhook_config_fingerprint(config: BatchWebhookRequest) -> str:
    return hashlib.sha256(canonical_batch_webhook_config_bytes(config)).hexdigest()


def redact_batch_webhook_config(config: object | None) -> dict[str, bool] | None:
    if config is None:
        return None
    return {"configured": True}
