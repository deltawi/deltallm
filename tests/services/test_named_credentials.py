from __future__ import annotations

import pytest

from src.config_runtime.secrets import SecretResolver
from src.db.named_credentials import NamedCredentialRecord
from src.services.named_credentials import resolve_named_credential_connection_config, resolve_named_credential_record
from src.upstream_auth import validate_auth_header_format, validate_auth_header_name


class _FakeAWSSecretsManager:
    def get_secret(self, path: str) -> str | None:
        if path == "providers/openai":
            return '{"api_key":"aws-secret","api_base":"https://aws.example/v1"}'
        return None


def test_resolve_named_credential_connection_config_supports_external_secret_manager_refs() -> None:
    resolver = SecretResolver(aws=_FakeAWSSecretsManager())

    resolved = resolve_named_credential_connection_config(
        {
            "api_key": "aws.secretsmanager/providers/openai#api_key",
            "api_base": "aws.secretsmanager/providers/openai#api_base",
        },
        secret_resolver=resolver,
    )

    assert resolved["api_key"] == "aws-secret"
    assert resolved["api_base"] == "https://aws.example/v1"


def test_resolve_named_credential_record_preserves_metadata_and_resolves_config() -> None:
    resolver = SecretResolver(aws=_FakeAWSSecretsManager())
    record = NamedCredentialRecord(
        credential_id="cred-1",
        name="OpenAI prod",
        provider="openai",
        connection_config={"api_key": "aws.secretsmanager/providers/openai#api_key"},
        metadata={"env": "prod"},
    )

    resolved = resolve_named_credential_record(record, secret_resolver=resolver)

    assert resolved is not None
    assert resolved.credential_id == "cred-1"
    assert resolved.metadata == {"env": "prod"}
    assert resolved.connection_config["api_key"] == "aws-secret"


def test_validate_auth_header_name_accepts_http_header_tokens() -> None:
    assert validate_auth_header_name("X-API-Key") == "X-API-Key"


def test_validate_auth_header_name_rejects_reserved_header_names() -> None:
    with pytest.raises(ValueError, match="reserved header name"):
        validate_auth_header_name("Content-Type")


def test_validate_auth_header_format_rejects_non_api_key_placeholders() -> None:
    with pytest.raises(ValueError, match=r"only supports the \{api_key\} placeholder"):
        validate_auth_header_format("Bearer {token}")


def test_validate_auth_header_format_rejects_escaped_api_key_placeholder() -> None:
    with pytest.raises(ValueError, match=r"must include the \{api_key\} placeholder"):
        validate_auth_header_format("Token {{api_key}}")


@pytest.mark.parametrize("value", ["Token {api_key!r}", "Token {api_key:>10}"])
def test_validate_auth_header_format_rejects_format_features(value: str) -> None:
    with pytest.raises(ValueError, match=r"only supports the \{api_key\} placeholder"):
        validate_auth_header_format(value)
