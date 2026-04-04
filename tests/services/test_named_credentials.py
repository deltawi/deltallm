from __future__ import annotations

from src.config_runtime.secrets import SecretResolver
from src.db.named_credentials import NamedCredentialRecord
from src.services.named_credentials import resolve_named_credential_connection_config, resolve_named_credential_record


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
