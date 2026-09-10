from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from src.models.errors import GatewayCapacityError, InvalidRequestError
from src.providers.resolution import is_openai_compatible_provider, resolve_provider
from src.providers.chat_profiles import CHAT_PROVIDER_PROFILES, ChatProviderProfile
from src.providers.chat_discovery import fetch_chat_models
from src.providers.discovery_runtime import DiscoveryUnavailable, ProviderDiscoveryRuntime
from src.upstream_auth import build_openai_compatible_auth_headers
from src.upstream_http import build_health_check_request_timeout


@dataclass
class HealthProbeResult:
    healthy: bool
    error: str | None = None
    status_code: int | None = None
    checked_at: int = field(default_factory=lambda: int(time.time()))
    affects_deployment_health: bool = True


def _status_error(prefix: str, status_code: int) -> str:
    return f"{prefix} returned {status_code}"


async def _probe_chat_profile(
    discovery_runtime: ProviderDiscoveryRuntime | None,
    profile: ChatProviderProfile,
    params: dict[str, object],
    request_timeout: httpx.Timeout,
) -> HealthProbeResult:
    if profile.discovery == "catalog":
        return HealthProbeResult(
            healthy=False,
            error="This provider has no supported non-billable health probe",
            affects_deployment_health=False,
        )
    api_key = str(params.get("api_key") or "").strip()
    if not api_key:
        return HealthProbeResult(healthy=False, error="Provider API key is missing")
    try:
        if discovery_runtime is None:
            raise DiscoveryUnavailable("Provider discovery runtime is unavailable")
        await fetch_chat_models(
            discovery_runtime,
            profile=profile,
            api_base=str(params.get("api_base") or profile.api_base),
            api_key=api_key,
            timeout=request_timeout,
        )
        return HealthProbeResult(healthy=True, status_code=200)
    except (NotImplementedError, DiscoveryUnavailable) as exc:
        return HealthProbeResult(healthy=False, error=str(exc), affects_deployment_health=False)
    except httpx.PoolTimeout:
        return HealthProbeResult(
            healthy=False, error="Gateway capacity exceeded", affects_deployment_health=False
        )
    except httpx.HTTPStatusError as exc:
        return HealthProbeResult(
            healthy=False,
            error=_status_error("Provider health check", exc.response.status_code),
            status_code=exc.response.status_code,
        )
    except (httpx.TimeoutException, TimeoutError):
        return HealthProbeResult(healthy=False, error="Provider health check timed out")
    except (httpx.HTTPError, ValueError):
        return HealthProbeResult(healthy=False, error="Provider health check failed")


async def probe_provider_health(
    http_client: httpx.AsyncClient,
    params: dict[str, Any],
    *,
    default_openai_base_url: str,
    general_settings: Any | None = None,
    health_check_timeout_seconds: float | int | None = None,
    discovery_runtime: ProviderDiscoveryRuntime | None = None,
) -> HealthProbeResult:
    provider = resolve_provider(params)
    if provider in {"unknown", ""}:
        provider = "openai"
    request_timeout = build_health_check_request_timeout(
        general_settings,
        read_timeout_seconds=10.0,
        health_check_timeout_seconds=health_check_timeout_seconds,
    )
    profile = CHAT_PROVIDER_PROFILES.get(provider)
    if profile is not None:
        return await _probe_chat_profile(discovery_runtime, profile, params, request_timeout)

    if provider == "anthropic":
        api_key = str(params.get("api_key") or "").strip()
        if not api_key:
            return HealthProbeResult(healthy=False, error="Provider API key is missing")
        api_base = str(params.get("api_base") or "https://api.anthropic.com/v1").rstrip("/")
        version = str(params.get("api_version") or "2023-06-01").strip()
        try:
            response = await http_client.get(
                f"{api_base}/models",
                headers={"x-api-key": api_key, "anthropic-version": version},
                timeout=request_timeout,
            )
            if response.status_code >= 400:
                return HealthProbeResult(
                    healthy=False,
                    error=_status_error("Anthropic health check", response.status_code),
                    status_code=response.status_code,
                )
            return HealthProbeResult(healthy=True, status_code=response.status_code)
        except httpx.PoolTimeout:
            error = GatewayCapacityError()
            return HealthProbeResult(
                healthy=False, error=error.message, affects_deployment_health=False
            )
        except httpx.TimeoutException:
            return HealthProbeResult(healthy=False, error="Anthropic health check timed out")
        except httpx.HTTPError as exc:
            return HealthProbeResult(healthy=False, error=f"Anthropic health check failed: {exc}")

    if provider in {"azure", "azure_openai"}:
        api_key = str(params.get("api_key") or "").strip()
        api_base = str(params.get("api_base") or "").rstrip("/")
        if not api_key:
            return HealthProbeResult(healthy=False, error="Provider API key is missing")
        if not api_base:
            return HealthProbeResult(healthy=False, error="API base URL is missing")
        try:
            response = await http_client.get(
                f"{api_base}/models",
                headers={"api-key": api_key},
                timeout=request_timeout,
            )
            if response.status_code >= 400:
                return HealthProbeResult(
                    healthy=False,
                    error=_status_error("Azure OpenAI health check", response.status_code),
                    status_code=response.status_code,
                )
            return HealthProbeResult(healthy=True, status_code=response.status_code)
        except httpx.PoolTimeout:
            error = GatewayCapacityError()
            return HealthProbeResult(
                healthy=False, error=error.message, affects_deployment_health=False
            )
        except httpx.TimeoutException:
            return HealthProbeResult(healthy=False, error="Azure OpenAI health check timed out")
        except httpx.HTTPError as exc:
            return HealthProbeResult(
                healthy=False, error=f"Azure OpenAI health check failed: {exc}"
            )

    if provider == "gemini":
        api_key = str(params.get("api_key") or "").strip()
        if not api_key:
            return HealthProbeResult(healthy=False, error="Provider API key is missing")
        api_base = str(
            params.get("api_base") or "https://generativelanguage.googleapis.com/v1beta"
        ).rstrip("/")
        try:
            response = await http_client.get(
                f"{api_base}/models?key={api_key}", timeout=request_timeout
            )
            if response.status_code >= 400:
                return HealthProbeResult(
                    healthy=False,
                    error=_status_error("Gemini health check", response.status_code),
                    status_code=response.status_code,
                )
            return HealthProbeResult(healthy=True, status_code=response.status_code)
        except httpx.PoolTimeout:
            error = GatewayCapacityError()
            return HealthProbeResult(
                healthy=False, error=error.message, affects_deployment_health=False
            )
        except httpx.TimeoutException:
            return HealthProbeResult(healthy=False, error="Gemini health check timed out")
        except httpx.HTTPError as exc:
            return HealthProbeResult(healthy=False, error=f"Gemini health check failed: {exc}")

    if provider == "elevenlabs":
        api_key = str(params.get("api_key") or "").strip()
        if not api_key:
            return HealthProbeResult(healthy=False, error="Provider API key is missing")
        api_base = str(params.get("api_base") or "https://api.elevenlabs.io/v1").rstrip("/")
        try:
            response = await http_client.get(
                f"{api_base}/models",
                headers={"xi-api-key": api_key},
                timeout=request_timeout,
            )
            if response.status_code >= 400:
                return HealthProbeResult(
                    healthy=False,
                    error=_status_error("ElevenLabs health check", response.status_code),
                    status_code=response.status_code,
                )
            return HealthProbeResult(healthy=True, status_code=response.status_code)
        except httpx.PoolTimeout:
            error = GatewayCapacityError()
            return HealthProbeResult(
                healthy=False, error=error.message, affects_deployment_health=False
            )
        except httpx.TimeoutException:
            return HealthProbeResult(healthy=False, error="ElevenLabs health check timed out")
        except httpx.HTTPError as exc:
            return HealthProbeResult(healthy=False, error=f"ElevenLabs health check failed: {exc}")

    if provider == "bedrock":
        if not str(params.get("aws_access_key_id") or "").strip():
            return HealthProbeResult(healthy=False, error="AWS access key is missing")
        if not str(params.get("aws_secret_access_key") or "").strip():
            return HealthProbeResult(healthy=False, error="AWS secret access key is missing")
        if not str(params.get("region") or "").strip():
            return HealthProbeResult(healthy=False, error="AWS region is missing")
        return HealthProbeResult(healthy=True)

    if not is_openai_compatible_provider(provider):
        return HealthProbeResult(
            healthy=False, error=f"Health checks are not implemented for provider '{provider}'"
        )

    api_key = str(params.get("api_key") or "").strip()
    if not api_key:
        return HealthProbeResult(healthy=False, error="Provider API key is missing")

    api_base = str(
        params.get("api_base") or (default_openai_base_url if provider == "openai" else "")
    ).rstrip("/")
    if not api_base:
        return HealthProbeResult(healthy=False, error="API base URL is missing")

    try:
        headers = build_openai_compatible_auth_headers(
            provider=provider,
            api_key=api_key,
            auth_header_name=str(params.get("auth_header_name") or "").strip() or None,
            auth_header_format=str(params.get("auth_header_format") or "").strip() or None,
        )
    except (InvalidRequestError, ValueError) as exc:
        return HealthProbeResult(
            healthy=False, error=str(exc) or "Provider auth configuration is invalid"
        )

    try:
        response = await http_client.get(
            f"{api_base}/models",
            headers=headers,
            timeout=request_timeout,
        )
        if response.status_code >= 400:
            return HealthProbeResult(
                healthy=False,
                error=_status_error(f"{provider} health check", response.status_code),
                status_code=response.status_code,
            )
        return HealthProbeResult(healthy=True, status_code=response.status_code)
    except httpx.PoolTimeout:
        error = GatewayCapacityError()
        return HealthProbeResult(
            healthy=False, error=error.message, affects_deployment_health=False
        )
    except httpx.TimeoutException:
        return HealthProbeResult(healthy=False, error=f"{provider} health check timed out")
    except httpx.HTTPError as exc:
        return HealthProbeResult(healthy=False, error=f"{provider} health check failed: {exc}")


async def is_provider_healthy(
    http_client: httpx.AsyncClient,
    params: dict[str, Any],
    *,
    default_openai_base_url: str,
    default_provider: str | None = None,
    general_settings: Any | None = None,
) -> bool:
    probe_params = dict(params)
    has_provider = bool(str(probe_params.get("provider") or "").strip())
    has_model = bool(str(probe_params.get("model") or "").strip())
    if default_provider and not has_provider and not has_model:
        probe_params["provider"] = default_provider
    try:
        result = await probe_provider_health(
            http_client,
            probe_params,
            default_openai_base_url=default_openai_base_url,
            general_settings=general_settings,
        )
    except Exception:
        return False
    return result.healthy
