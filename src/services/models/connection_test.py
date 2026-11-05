"""Check a model's provider connection before it is saved.

The check never makes a billable call. It reuses the deployment health probe, which
reads the provider's model list or an equivalent free endpoint, then looks for the
entered provider model in that list when the provider publishes one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

import httpx

from src.providers.discovery_runtime import ProviderDiscoveryRuntime
from src.providers.healthcheck import HealthProbeResult, probe_provider_health
from src.providers.model_discovery import discover_provider_models
from src.providers.resolution import resolve_provider, resolve_upstream_model
from src.upstream_http import outbound_proxy_scope

ConnectionTestStatus = Literal["passed", "warning", "failed", "unavailable"]
ModelCheck = Literal["found", "not_found", "not_checked"]

# Providers whose probe validates settings without a network call.
_OFFLINE_PROBE_PROVIDERS = frozenset({"bedrock"})

# Azure lists base models, while a deployment calls its own deployment name.
_UNLISTED_MODEL_PROVIDERS = frozenset({"azure", "azure_openai"})

# Connection fields a test may take from the form, a named credential, or the saved model.
CONNECTION_FIELDS = (
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


@dataclass(frozen=True)
class ConnectionTestResult:
    status: ConnectionTestStatus
    message: str
    model_check: ModelCheck
    status_code: int | None
    checked_at: int

    def to_payload(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "message": self.message,
            "model_check": self.model_check,
            "status_code": self.status_code,
            "checked_at": self.checked_at,
        }


def _probe_failure(result: HealthProbeResult) -> ConnectionTestResult:
    error = result.error or "Provider connection failed"
    if not result.affects_deployment_health:
        # No free probe, gateway capacity, or discovery runtime missing: not the settings.
        return ConnectionTestResult(
            status="unavailable",
            message=f"{error}. The connection was not tested.",
            model_check="not_checked",
            status_code=result.status_code,
            checked_at=result.checked_at,
        )
    hint = ""
    if result.status_code in {401, 403}:
        hint = " Check the API key."
    elif result.status_code == 404:
        hint = " Check the API base URL."
    return ConnectionTestResult(
        status="failed",
        message=f"{error}.{hint}",
        model_check="not_checked",
        status_code=result.status_code,
        checked_at=result.checked_at,
    )


async def _listed_model_ids(
    http_client: httpx.AsyncClient,
    params: dict[str, Any],
    *,
    provider: str,
    default_openai_base_url: str,
    general_settings: Any | None,
    discovery_runtime: ProviderDiscoveryRuntime | None,
) -> set[str] | None:
    """Return the provider's live model ids, or None when it publishes no usable list."""

    try:
        discovered = await discover_provider_models(
            http_client,
            provider=provider,
            # Every listed model counts: the type filter is the form's job, not this check's.
            mode=None,
            api_key=params.get("api_key"),
            api_base=params.get("api_base"),
            api_version=params.get("api_version"),
            auth_header_name=params.get("auth_header_name"),
            auth_header_format=params.get("auth_header_format"),
            default_openai_base_url=default_openai_base_url,
            general_settings=general_settings,
            discovery_runtime=discovery_runtime,
        )
    except Exception:
        return None
    live = {
        str(item.get("id") or "").strip().lower()
        for item in discovered.get("data") or []
        if item.get("source") == "provider_api"
    }
    live.discard("")
    if discovered.get("warnings") or not live:
        return None
    return live


async def check_model_connection(
    http_client: httpx.AsyncClient,
    params: dict[str, Any],
    *,
    default_openai_base_url: str,
    general_settings: Any | None = None,
    discovery_runtime: ProviderDiscoveryRuntime | None = None,
) -> ConnectionTestResult:
    provider = resolve_provider(params)
    proxy = params.get("outbound_proxy")
    probe = await probe_provider_health(
        http_client,
        params,
        default_openai_base_url=default_openai_base_url,
        general_settings=general_settings,
        health_check_timeout_seconds=30,
        discovery_runtime=discovery_runtime,
    )
    if not probe.healthy:
        return _probe_failure(probe)
    if provider in _OFFLINE_PROBE_PROVIDERS:
        return ConnectionTestResult(
            status="unavailable",
            message=(
                "The credentials are filled in, but this provider has no free check, "
                "so the connection was not tested."
            ),
            model_check="not_checked",
            status_code=None,
            checked_at=probe.checked_at,
        )

    upstream_model = resolve_upstream_model(params)
    listed: set[str] | None = None
    check_model = bool(upstream_model) and provider not in _UNLISTED_MODEL_PROVIDERS
    if check_model:
        with outbound_proxy_scope(proxy if isinstance(proxy, str) else None):
            listed = await _listed_model_ids(
                http_client,
                params,
                provider=provider,
                default_openai_base_url=default_openai_base_url,
                general_settings=general_settings,
                discovery_runtime=discovery_runtime,
            )
    if listed is None:
        return ConnectionTestResult(
            status="passed",
            message=(
                "Connected to the provider. Its model list could not be read, "
                "so the provider model was not checked."
                if check_model
                else "Connected to the provider."
            ),
            model_check="not_checked",
            status_code=probe.status_code,
            checked_at=probe.checked_at,
        )
    if upstream_model.strip().lower() in listed:
        return ConnectionTestResult(
            status="passed",
            message=f"Connected to the provider. {upstream_model} is in its model list.",
            model_check="found",
            status_code=probe.status_code,
            checked_at=probe.checked_at,
        )
    return ConnectionTestResult(
        status="warning",
        message=(
            f"Connected to the provider, but {upstream_model} is not in its model list. "
            "Check the provider model name."
        ),
        model_check="not_found",
        status_code=probe.status_code,
        checked_at=probe.checked_at,
    )
