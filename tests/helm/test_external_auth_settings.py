from __future__ import annotations

import json
from pathlib import Path
import pytest
import yaml

from src.auth.external_config import ExternalAuthSettings
from tests.helm.test_batch_worker_split import HELM_CHART_DIR, _render, _render_error
from tests.helm.test_tier_policy_settings import _config_maps, _general_settings

pytestmark = pytest.mark.helm


def test_external_auth_schema_covers_runtime_fields_and_defaults_are_disabled():
    schema = json.loads((HELM_CHART_DIR / "values.schema.json").read_text())
    external = schema["properties"]["config"]["properties"]["general_settings"]["properties"][
        "external_auth"
    ]
    assert set(external["properties"]) == set(ExternalAuthSettings.model_fields)
    assert external["additionalProperties"] is False
    for profile in ["values.yaml", "values-eval.yaml", "values-production.yaml"]:
        configs = _config_maps(_render("-f", str(HELM_CHART_DIR / profile)))
        for config in configs:
            if "config.yaml" in config.get("data", {}):
                assert _general_settings(config)["external_auth"]["enabled"] is False


def enabled_values(tmp_path: Path) -> str:
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    pem = (
        rsa.generate_private_key(public_exponent=65537, key_size=2048)
        .public_key()
        .public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    settings = ExternalAuthSettings().model_dump(mode="json")
    settings.update(
        enabled=True,
        deployment_protocol="external_customer_v1",
        allowed_origins=["https://console.example.com"],
        integrations=[
            {
                "integration_id": "console",
                "issuer": "https://console.example.com",
                "audience": "gateway",
                "identity_issuer": "https://clerk.example.com",
                "keys": [{"kid": "one", "public_key": pem}],
            }
        ],
    )
    values = {
        "externalAuthCapacity": {
            "maximumApiPods": 5,
            "otherReservedConnections": 210,
            "postgresConnectionBudget": 230,
        },
        "config": {
            "general_settings": {
                "external_auth": settings,
                "api_key_auth_cache_ttl_seconds": 60,
                "audit_enabled": True,
                "audit_ingestion_mode": "outbox",
                "audit_ingestion_worker_enabled": True,
                "cache_invalidation_worker_enabled": True,
            }
        },
    }
    path = tmp_path / "external.yaml"
    path.write_text(yaml.safe_dump(values))
    return str(path)


def test_certified_external_auth_profile_renders(tmp_path):
    _render("-f", enabled_values(tmp_path))


@pytest.mark.parametrize(
    "setting,value,reason",
    [
        ("externalAuthCapacity.postgresConnectionBudget", "229", "postgresConnectionBudget"),
        ("externalAuthCapacity.otherReservedConnections", "209", "otherReservedConnections"),
        ("externalAuthCapacity.maximumApiPods", "1", "maximumApiPods"),
        ("config.general_settings.api_key_auth_cache_ttl_seconds", "61", "cache_ttl"),
        ("config.general_settings.audit_ingestion_worker_enabled", "false", "workers"),
        ("config.general_settings.audit_enabled", "false", "workers"),
        (
            "config.general_settings.external_auth.deployment_protocol",
            "wrong",
            "deployment_protocol",
        ),
    ],
)
def test_uncertified_external_auth_profile_fails(tmp_path, setting, value, reason):
    error = _render_error("-f", enabled_values(tmp_path), "--set", f"{setting}={value}")
    assert reason in error


def test_separate_worker_pool_and_surge_are_included_in_the_budget(tmp_path):
    error = _render_error(
        "-f",
        enabled_values(tmp_path),
        "--set",
        "batchWorker.enabled=true",
        "--set",
        "batchWorker.allowUnsafeLocalStorage=true",
        "--set",
        "api.config.general_settings.cache_invalidation_worker_enabled=true",
    )
    assert "otherReservedConnections" in error
    _render(
        "-f",
        enabled_values(tmp_path),
        "--set",
        "batchWorker.enabled=true",
        "--set",
        "batchWorker.allowUnsafeLocalStorage=true",
        "--set",
        "api.config.general_settings.cache_invalidation_worker_enabled=true",
        "--set",
        "externalAuthCapacity.otherReservedConnections=324",
        "--set",
        "externalAuthCapacity.postgresConnectionBudget=344",
    )
