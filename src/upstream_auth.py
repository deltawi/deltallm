from __future__ import annotations

import re
from string import Formatter

from src.models.errors import InvalidRequestError

DEFAULT_OPENAI_COMPATIBLE_AUTH_HEADER_NAME = "Authorization"
DEFAULT_OPENAI_COMPATIBLE_AUTH_HEADER_FORMAT = "Bearer {api_key}"

CUSTOM_OPENAI_COMPATIBLE_AUTH_PROVIDERS = {
    "openai",
    "openrouter",
    "groq",
    "together",
    "fireworks",
    "deepinfra",
    "perplexity",
    "vllm",
    "lmstudio",
    "ollama",
}

_HTTP_HEADER_NAME_PATTERN = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
_FORMATTER = Formatter()
_RESERVED_AUTH_HEADER_NAMES = {
    "content-type",
}


def supports_custom_openai_compatible_auth(provider: str | None) -> bool:
    return (provider or "").strip().lower() in CUSTOM_OPENAI_COMPATIBLE_AUTH_PROVIDERS


def validate_auth_header_name(value: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError("auth_header_name must not be empty")
    if not _HTTP_HEADER_NAME_PATTERN.fullmatch(normalized):
        raise ValueError("auth_header_name must be a valid HTTP header name")
    if normalized.lower() in _RESERVED_AUTH_HEADER_NAMES:
        raise ValueError(f"auth_header_name cannot use reserved header name '{normalized}'")
    return normalized


def validate_auth_header_format(value: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError("auth_header_format must not be empty")

    fields: list[str] = []
    for _, field_name, format_spec, conversion in _FORMATTER.parse(normalized):
        if field_name is None:
            continue
        if not field_name:
            raise ValueError("auth_header_format must be a valid format string")
        if field_name != "api_key" or format_spec or conversion is not None:
            raise ValueError("auth_header_format only supports the {api_key} placeholder without format specifiers or conversions")
        fields.append(field_name)

    if not fields:
        raise ValueError("auth_header_format must include the {api_key} placeholder")

    try:
        normalized.format(api_key="test-key")
    except (IndexError, KeyError, ValueError) as exc:
        raise ValueError("auth_header_format must be a valid format string") from exc

    return normalized


def build_openai_compatible_auth_headers(
    *,
    provider: str | None,
    api_key: str,
    auth_header_name: str | None = None,
    auth_header_format: str | None = None,
    content_type: str | None = None,
) -> dict[str, str]:
    normalized_api_key = str(api_key or "").strip()
    if not normalized_api_key:
        raise InvalidRequestError(message="Provider API key is missing for selected model")

    if supports_custom_openai_compatible_auth(provider):
        header_name = validate_auth_header_name(auth_header_name or DEFAULT_OPENAI_COMPATIBLE_AUTH_HEADER_NAME)
        header_format = validate_auth_header_format(auth_header_format or DEFAULT_OPENAI_COMPATIBLE_AUTH_HEADER_FORMAT)
    else:
        header_name = DEFAULT_OPENAI_COMPATIBLE_AUTH_HEADER_NAME
        header_format = DEFAULT_OPENAI_COMPATIBLE_AUTH_HEADER_FORMAT

    headers = {header_name: header_format.format(api_key=normalized_api_key)}
    if content_type:
        headers["Content-Type"] = content_type
    return headers
