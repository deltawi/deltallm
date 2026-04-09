from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException, status

from src.db.named_credentials import NamedCredentialRecord
from src.upstream_auth import (
    supports_custom_openai_compatible_auth,
    validate_auth_header_format,
    validate_auth_header_name,
)
from src.providers.resolution import PROVIDER_PRESETS, is_openai_compatible_provider, resolve_provider

if TYPE_CHECKING:
    from src.config_runtime.secrets import SecretResolver

_NON_SECRET_CONNECTION_KEYS = {
    "api_base",
    "api_version",
    "auth_header_format",
    "auth_header_name",
    "region",
    "provider",
    "endpoint",
    "base_url",
}

_MASK = "***REDACTED***"
_BEDROCK_FIELDS = {
    "api_base",
    "region",
    "aws_access_key_id",
    "aws_secret_access_key",
    "aws_session_token",
}
_API_KEY_PROVIDER_FIELDS = {
    "api_key",
    "api_base",
    "api_version",
}
_CUSTOM_AUTH_PROVIDER_FIELDS = _API_KEY_PROVIDER_FIELDS | {
    "auth_header_name",
    "auth_header_format",
}
_PROVIDER_LABEL_ALIASES = {
    "azure": "azure_openai",
}
INLINE_CONNECTION_FIELDS = (
    "api_key",
    "api_base",
    "api_version",
    "auth_header_name",
    "auth_header_format",
    "region",
    "aws_access_key_id",
    "aws_secret_access_key",
    "aws_session_token",
)


def canonicalize_named_credential_provider(provider: str | None) -> str:
    normalized = str(provider or "").strip().lower()
    return _PROVIDER_LABEL_ALIASES.get(normalized, normalized)


def named_credential_provider_for_params(params: dict[str, Any]) -> str:
    return canonicalize_named_credential_provider(resolve_provider(params))


def normalize_named_credential_payload(
    payload: dict[str, Any],
    *,
    existing: NamedCredentialRecord | None = None,
) -> tuple[str, str, dict[str, Any], dict[str, Any] | None]:
    name = str(payload.get("name") or (existing.name if existing is not None else "")).strip()
    if not name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="name is required")

    provider = canonicalize_named_credential_provider(payload.get("provider") or (existing.provider if existing is not None else ""))
    if not provider:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="provider is required")
    if provider not in PROVIDER_PRESETS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="provider is invalid")
    if existing is not None and provider != existing.provider:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="provider cannot be changed once a named credential is created")

    raw_connection_config = payload.get("connection_config")
    if raw_connection_config is None:
        connection_config = dict(existing.connection_config if existing is not None else {})
    elif isinstance(raw_connection_config, dict):
        connection_config = _merge_connection_config(
            existing.connection_config if existing is not None else None,
            raw_connection_config,
        )
    else:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="connection_config must be an object")

    if not connection_config:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="connection_config must include at least one field")
    _validate_connection_config(provider, connection_config)

    raw_metadata = payload.get("metadata")
    if raw_metadata is None:
        metadata = dict(existing.metadata) if existing is not None and existing.metadata is not None else None
    elif isinstance(raw_metadata, dict):
        metadata = dict(raw_metadata)
    else:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="metadata must be an object")

    return name, provider, connection_config, metadata


def _merge_connection_config(
    existing_connection_config: dict[str, Any] | None,
    incoming_connection_config: dict[str, Any],
) -> dict[str, Any]:
    merged = dict(existing_connection_config or {})
    for raw_key, raw_value in incoming_connection_config.items():
        key = str(raw_key).strip()
        if not key:
            continue
        if raw_value is None:
            merged.pop(key, None)
            continue
        if isinstance(raw_value, str):
            value = raw_value.strip()
            if not value:
                continue
            merged[key] = value
            continue
        merged[key] = raw_value
    return merged


def _validate_connection_config(provider: str, connection_config: dict[str, Any]) -> None:
    allowed_fields = _allowed_fields_for_provider(provider)
    unknown_fields = sorted(str(key) for key in connection_config if key not in allowed_fields)
    if unknown_fields:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"connection_config includes unsupported fields for provider '{provider}': {', '.join(unknown_fields)}",
        )

    if provider == "bedrock":
        access_key = str(connection_config.get("aws_access_key_id") or "").strip()
        secret_key = str(connection_config.get("aws_secret_access_key") or "").strip()
        session_token = str(connection_config.get("aws_session_token") or "").strip()
        if bool(access_key) != bool(secret_key):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="bedrock credentials require both aws_access_key_id and aws_secret_access_key when either is provided",
            )
        if session_token and not (access_key and secret_key):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="bedrock aws_session_token requires aws_access_key_id and aws_secret_access_key",
            )

    if supports_custom_openai_compatible_auth(provider):
        auth_header_name = connection_config.get("auth_header_name")
        if auth_header_name is not None:
            try:
                validate_auth_header_name(str(auth_header_name))
            except ValueError as exc:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

        auth_header_format = connection_config.get("auth_header_format")
        if auth_header_format is not None:
            try:
                validate_auth_header_format(str(auth_header_format))
            except ValueError as exc:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


def _allowed_fields_for_provider(provider: str) -> set[str]:
    if provider == "bedrock":
        return _BEDROCK_FIELDS
    if provider == "azure_openai":
        return _API_KEY_PROVIDER_FIELDS
    if provider == "anthropic":
        return _API_KEY_PROVIDER_FIELDS
    if provider == "gemini":
        return _API_KEY_PROVIDER_FIELDS
    if supports_custom_openai_compatible_auth(provider):
        return _CUSTOM_AUTH_PROVIDER_FIELDS
    if is_openai_compatible_provider(provider):
        return _API_KEY_PROVIDER_FIELDS
    return set()


def extract_connection_config_from_params(params: dict[str, Any]) -> dict[str, Any]:
    extracted: dict[str, Any] = {}
    for field in INLINE_CONNECTION_FIELDS:
        value = params.get(field)
        if value is None:
            continue
        if isinstance(value, str):
            normalized = value.strip()
            if not normalized:
                continue
            extracted[field] = normalized
            continue
        extracted[field] = value
    return extracted


def clear_connection_fields(params: dict[str, Any]) -> dict[str, Any]:
    cleaned = dict(params)
    for field in INLINE_CONNECTION_FIELDS:
        cleaned.pop(field, None)
    return cleaned


def connection_fingerprint(provider: str, connection_config: dict[str, Any]) -> str:
    payload = {
        "provider": provider,
        "connection_config": {
            key: connection_config[key]
            for key in sorted(connection_config.keys())
        },
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def named_credential_credentials_present(connection_config: dict[str, Any] | None) -> bool:
    config = connection_config or {}
    return any(_has_meaningful_value(value) for value in config.values())


def redact_connection_config(connection_config: dict[str, Any] | None) -> dict[str, Any] | None:
    if connection_config is None:
        return None
    return {str(key): _redact_value(str(key), value) for key, value in connection_config.items()}


def serialize_named_credential(record: NamedCredentialRecord) -> dict[str, Any]:
    return {
        "credential_id": record.credential_id,
        "name": record.name,
        "provider": record.provider,
        "connection_config": redact_connection_config(record.connection_config),
        "credentials_present": named_credential_credentials_present(record.connection_config),
        "metadata": record.metadata,
        "created_by_account_id": record.created_by_account_id,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }


def merge_named_credential_params(
    params: dict[str, Any],
    named_credential: NamedCredentialRecord | None,
) -> dict[str, Any]:
    resolved = dict(params)
    if named_credential is None:
        return resolved
    resolved.update(named_credential.connection_config)
    return resolved


def resolve_named_credential_connection_config(
    connection_config: dict[str, Any] | None,
    *,
    secret_resolver: "SecretResolver | None" = None,
) -> dict[str, Any]:
    raw = dict(connection_config or {})
    if not raw:
        return {}
    if secret_resolver is None:
        return raw
    resolver = secret_resolver
    resolved = resolver.resolve_tree(raw)
    return resolved if isinstance(resolved, dict) else {}


def resolve_named_credential_record(
    named_credential: NamedCredentialRecord | None,
    *,
    secret_resolver: "SecretResolver | None" = None,
) -> NamedCredentialRecord | None:
    if named_credential is None:
        return None
    return NamedCredentialRecord(
        credential_id=named_credential.credential_id,
        name=named_credential.name,
        provider=named_credential.provider,
        connection_config=resolve_named_credential_connection_config(
            named_credential.connection_config,
            secret_resolver=secret_resolver,
        ),
        metadata=dict(named_credential.metadata) if named_credential.metadata is not None else None,
        created_by_account_id=named_credential.created_by_account_id,
        created_at=named_credential.created_at,
        updated_at=named_credential.updated_at,
    )


def _redact_value(key: str, value: Any) -> Any:
    lowered = key.lower()
    if lowered in _NON_SECRET_CONNECTION_KEYS:
        return value
    if lowered in {"api_key", "aws_access_key_id", "aws_secret_access_key", "aws_session_token"}:
        return _MASK
    if "password" in lowered or "secret" in lowered or "token" in lowered:
        return _MASK
    if lowered.endswith("_key") or lowered == "key":
        return _MASK
    if isinstance(value, dict):
        return {str(child_key): _redact_value(str(child_key), child_value) for child_key, child_value in value.items()}
    if isinstance(value, list):
        return [_MASK if _has_meaningful_value(item) else item for item in value]
    return value


def _has_meaningful_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set)):
        return any(_has_meaningful_value(item) for item in value)
    if isinstance(value, dict):
        return any(_has_meaningful_value(item) for item in value.values())
    return True
