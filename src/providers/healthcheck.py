from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from src.providers.resolution import is_openai_compatible_provider, resolve_provider
from src.upstream_auth import build_openai_compatible_auth_headers


@dataclass
class HealthProbeResult:
    healthy: bool
    error: str | None = None
    status_code: int | None = None
    checked_at: int = field(default_factory=lambda: int(time.time()))


def _status_error(prefix: str, status_code: int) -> str:
    return f"{prefix} returned {status_code}"


async def probe_provider_health(
    http_client: httpx.AsyncClient,
    params: dict[str, Any],
    *,
    default_openai_base_url: str,
) -> HealthProbeResult:
    provider = resolve_provider(params)

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
                timeout=10.0,
            )
            if response.status_code >= 400:
                return HealthProbeResult(
                    healthy=False,
                    error=_status_error("Anthropic health check", response.status_code),
                    status_code=response.status_code,
                )
            return HealthProbeResult(healthy=True, status_code=response.status_code)
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
                timeout=10.0,
            )
            if response.status_code >= 400:
                return HealthProbeResult(
                    healthy=False,
                    error=_status_error("Azure OpenAI health check", response.status_code),
                    status_code=response.status_code,
                )
            return HealthProbeResult(healthy=True, status_code=response.status_code)
        except httpx.TimeoutException:
            return HealthProbeResult(healthy=False, error="Azure OpenAI health check timed out")
        except httpx.HTTPError as exc:
            return HealthProbeResult(healthy=False, error=f"Azure OpenAI health check failed: {exc}")

    if provider == "gemini":
        api_key = str(params.get("api_key") or "").strip()
        if not api_key:
            return HealthProbeResult(healthy=False, error="Provider API key is missing")
        api_base = str(params.get("api_base") or "https://generativelanguage.googleapis.com/v1beta").rstrip("/")
        try:
            response = await http_client.get(f"{api_base}/models?key={api_key}", timeout=10.0)
            if response.status_code >= 400:
                return HealthProbeResult(
                    healthy=False,
                    error=_status_error("Gemini health check", response.status_code),
                    status_code=response.status_code,
                )
            return HealthProbeResult(healthy=True, status_code=response.status_code)
        except httpx.TimeoutException:
            return HealthProbeResult(healthy=False, error="Gemini health check timed out")
        except httpx.HTTPError as exc:
            return HealthProbeResult(healthy=False, error=f"Gemini health check failed: {exc}")

    if provider == "bedrock":
        if not str(params.get("aws_access_key_id") or "").strip():
            return HealthProbeResult(healthy=False, error="AWS access key is missing")
        if not str(params.get("aws_secret_access_key") or "").strip():
            return HealthProbeResult(healthy=False, error="AWS secret access key is missing")
        if not str(params.get("region") or "").strip():
            return HealthProbeResult(healthy=False, error="AWS region is missing")
        return HealthProbeResult(healthy=True)

    if provider in {"unknown", ""}:
        return HealthProbeResult(healthy=False, error="Provider could not be resolved for this deployment")

    if not is_openai_compatible_provider(provider):
        return HealthProbeResult(healthy=False, error=f"Health checks are not implemented for provider '{provider}'")

    api_key = str(params.get("api_key") or "").strip()
    if not api_key:
        return HealthProbeResult(healthy=False, error="Provider API key is missing")

    api_base = str(params.get("api_base") or (default_openai_base_url if provider == "openai" else "")).rstrip("/")
    if not api_base:
        return HealthProbeResult(healthy=False, error="API base URL is missing")

    try:
        response = await http_client.get(
            f"{api_base}/models",
            headers=build_openai_compatible_auth_headers(
                provider=provider,
                api_key=api_key,
                auth_header_name=str(params.get("auth_header_name") or "").strip() or None,
                auth_header_format=str(params.get("auth_header_format") or "").strip() or None,
            ),
            timeout=10.0,
        )
        if response.status_code >= 400:
            return HealthProbeResult(
                healthy=False,
                error=_status_error(f"{provider} health check", response.status_code),
                status_code=response.status_code,
            )
        return HealthProbeResult(healthy=True, status_code=response.status_code)
    except httpx.TimeoutException:
        return HealthProbeResult(healthy=False, error=f"{provider} health check timed out")
    except httpx.HTTPError as exc:
        return HealthProbeResult(healthy=False, error=f"{provider} health check failed: {exc}")
