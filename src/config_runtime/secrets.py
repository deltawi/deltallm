from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class SecretRef:
    provider: str
    path: str
    field: str | None = None


class BaseSecretManager:
    def get_secret(self, path: str) -> str | None:
        raise NotImplementedError


class AWSSecretManager(BaseSecretManager):
    """AWS Secrets Manager resolver using boto3 when available."""

    def __init__(self, region_name: str | None = None) -> None:
        self.region_name = region_name
        self._client: Any | None = None

    def _get_client(self) -> Any | None:
        if self._client is not None:
            return self._client
        try:
            import boto3  # type: ignore
        except Exception:
            logger.debug("boto3 not available; AWS secret references will be skipped")
            return None

        self._client = boto3.client("secretsmanager", region_name=self.region_name)
        return self._client

    def get_secret(self, path: str) -> str | None:
        client = self._get_client()
        if client is None:
            return None

        try:
            payload = client.get_secret_value(SecretId=path)
        except Exception as exc:
            logger.warning("failed to resolve AWS secret '%s': %s", path, exc)
            return None

        secret = payload.get("SecretString")
        if isinstance(secret, str):
            return secret

        binary = payload.get("SecretBinary")
        if isinstance(binary, (bytes, bytearray)):
            return binary.decode("utf-8")

        return None


class GCPSecretManager(BaseSecretManager):
    """GCP Secret Manager resolver using google-cloud-secret-manager when available."""

    def __init__(self) -> None:
        self._client: Any | None = None

    def _get_client(self) -> Any | None:
        if self._client is not None:
            return self._client

        try:
            from google.cloud import secretmanager  # type: ignore
        except Exception:
            logger.debug("google-cloud-secret-manager not available; GCP secret references will be skipped")
            return None

        self._client = secretmanager.SecretManagerServiceClient()
        return self._client

    def get_secret(self, path: str) -> str | None:
        client = self._get_client()
        if client is None:
            return None

        name = path
        if "/versions/" not in name:
            name = f"{path}/versions/latest"

        try:
            response = client.access_secret_version(name=name)
            data = response.payload.data
            if isinstance(data, (bytes, bytearray)):
                return data.decode("utf-8")
            if isinstance(data, str):
                return data
        except Exception as exc:
            logger.warning("failed to resolve GCP secret '%s': %s", path, exc)

        return None


class AzureSecretManager(BaseSecretManager):
    """Azure Key Vault resolver using azure SDKs when available."""

    def __init__(self) -> None:
        self._cache: dict[str, Any] = {}

    def _get_client(self, vault_url: str) -> Any | None:
        if vault_url in self._cache:
            return self._cache[vault_url]

        try:
            from azure.identity import DefaultAzureCredential  # type: ignore
            from azure.keyvault.secrets import SecretClient  # type: ignore
        except Exception:
            logger.debug("azure SDK not available; Azure secret references will be skipped")
            return None

        try:
            client = SecretClient(vault_url=vault_url, credential=DefaultAzureCredential())
        except Exception as exc:
            logger.warning("failed to create azure secret client for '%s': %s", vault_url, exc)
            return None

        self._cache[vault_url] = client
        return client

    def get_secret(self, path: str) -> str | None:
        # Expected: https://{vault}.vault.azure.net/secrets/{name}[/{version}]
        if "/secrets/" not in path:
            logger.warning("invalid Azure secret reference '%s'", path)
            return None

        vault_url, secret_path = path.split("/secrets/", 1)
        vault_url = vault_url.rstrip("/")
        parts = [part for part in secret_path.split("/") if part]
        if not parts:
            return None

        name = parts[0]
        version = parts[1] if len(parts) > 1 else None

        client = self._get_client(vault_url)
        if client is None:
            return None

        try:
            secret = client.get_secret(name=name, version=version)
            return secret.value
        except Exception as exc:
            logger.warning("failed to resolve Azure secret '%s': %s", path, exc)
            return None


class SecretResolver:
    """Resolves inline env/cloud secret tokens in config values."""

    def __init__(
        self,
        aws: BaseSecretManager | None = None,
        gcp: BaseSecretManager | None = None,
        azure: BaseSecretManager | None = None,
    ) -> None:
        self.aws = aws or AWSSecretManager()
        self.gcp = gcp or GCPSecretManager()
        self.azure = azure or AzureSecretManager()

    @staticmethod
    def _parse_ref(value: str) -> SecretRef | None:
        if value.startswith("os.environ/"):
            return SecretRef(provider="env", path=value.split("/", 1)[1])

        providers = {
            "aws.secretsmanager/": "aws",
            "gcp.secretmanager/": "gcp",
            "azure.keyvault/": "azure",
        }

        for prefix, provider in providers.items():
            if value.startswith(prefix):
                payload = value.split("/", 1)[1]
                if "#" in payload:
                    path, field = payload.split("#", 1)
                    return SecretRef(provider=provider, path=path, field=field)
                return SecretRef(provider=provider, path=payload)

        return None

    @staticmethod
    def _extract_field(secret_value: str, field: str | None) -> str:
        if not field:
            return secret_value

        try:
            parsed = json.loads(secret_value)
        except Exception:
            return secret_value

        if isinstance(parsed, dict) and field in parsed:
            value = parsed[field]
            return value if isinstance(value, str) else json.dumps(value)

        return secret_value

    def resolve_value(self, value: Any) -> Any:
        if not isinstance(value, str):
            return value

        ref = self._parse_ref(value)
        if ref is None:
            return value

        secret: str | None
        if ref.provider == "env":
            secret = os.getenv(ref.path)
        elif ref.provider == "aws":
            secret = self.aws.get_secret(ref.path)
        elif ref.provider == "gcp":
            secret = self.gcp.get_secret(ref.path)
        elif ref.provider == "azure":
            secret = self.azure.get_secret(ref.path)
        else:
            secret = None

        if secret is None:
            return value

        return self._extract_field(secret, ref.field)

    def resolve_tree(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {k: self.resolve_tree(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self.resolve_tree(v) for v in value]
        return self.resolve_value(value)
