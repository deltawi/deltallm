"""Caller-facing model metadata in the LiteLLM ``/model/info`` shapes.

Everything here is built from allowlists so provider credentials, endpoints and
operator-only settings never reach an API-key caller.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from src.providers.resolution import resolve_provider

# deltallm_params keys that are safe to show. api_key, api_base, AWS credentials,
# auth headers and region stay server-side.
_PUBLIC_PARAM_KEYS = ("model", "rpm", "tpm", "max_tokens", "timeout", "stream_timeout")

_PRICE_KEYS = (
    "input_cost_per_token",
    "output_cost_per_token",
    "output_cost_per_token_cache_hit",
    "batch_input_cost_per_token",
    "batch_output_cost_per_token",
    "batch_price_multiplier",
    "input_cost_per_character",
    "output_cost_per_character",
    "input_cost_per_second",
    "output_cost_per_second",
    "input_cost_per_image",
    "output_cost_per_image",
    "input_cost_per_audio_token",
    "output_cost_per_audio_token",
    "input_cost_per_audio_token_cache_hit",
    "cost_per_request",
)

_LIMIT_KEYS = ("max_tokens", "max_input_tokens", "max_output_tokens")

# Operator-declared chat capability -> LiteLLM flag name.
CAPABILITY_FLAGS = {
    "tools": "supports_function_calling",
    "image": "supports_vision",
    "audio": "supports_audio_input",
    "file": "supports_pdf_input",
    "json_schema": "supports_response_schema",
    "streaming": "supports_native_streaming",
}


def deployment_info(public_name: str, deployment: Any, *, mode: str | None) -> dict[str, Any]:
    """One ``/model/info`` row for a deployment reachable under ``public_name``."""
    params: Mapping[str, Any] = deployment.deltallm_params or {}
    model_info: Mapping[str, Any] = deployment.model_info or {}
    provider = resolve_provider(params)

    public_params: dict[str, Any] = {
        key: params[key] for key in _PUBLIC_PARAM_KEYS if params.get(key) is not None
    }
    public_params["custom_llm_provider"] = provider

    info: dict[str, Any] = {
        "id": deployment.deployment_id,
        "mode": _mode(model_info) or mode,
        "litellm_provider": provider,
    }
    for key in _LIMIT_KEYS:
        info[key] = _positive_int(model_info.get(key))
    if model_info.get("output_vector_size") is not None:
        info["output_vector_size"] = model_info["output_vector_size"]
    info["input_cost_per_token"] = model_info.get("input_cost_per_token")
    info["output_cost_per_token"] = model_info.get("output_cost_per_token")
    info["cache_read_input_token_cost"] = model_info.get("input_cost_per_token_cache_hit")
    for key in _PRICE_KEYS:
        if key not in info and model_info.get(key) is not None:
            info[key] = model_info[key]
    info["rpm"] = deployment.rpm_limit
    info["tpm"] = deployment.tpm_limit

    capabilities = _capabilities(model_info)
    info["chat_capabilities"] = capabilities
    info.update(_capability_flags(capabilities))
    return {"model_name": public_name, "litellm_params": public_params, "model_info": info}


def model_group_info(
    public_name: str,
    deployments: Sequence[Any],
    *,
    mode: str | None,
    target_type: str,
) -> dict[str, Any]:
    """One ``/model_group/info`` row; aggregates so any routed deployment fits.

    LiteLLM reports the largest context window and price across a group. A request
    can land on any deployment, so this reports the smallest context window, the
    highest price, and a capability only when every deployment declares it.
    """
    rows = [
        deployment_info(public_name, deployment, mode=mode)["model_info"]
        for deployment in deployments
    ]
    providers = sorted({row["litellm_provider"] for row in rows})
    flags = {
        flag: _all_true(row[flag] for row in rows) if rows else None
        for flag in CAPABILITY_FLAGS.values()
    }
    return {
        "model_group": public_name,
        "target_type": target_type,
        "providers": providers,
        "mode": mode or _single(row["mode"] for row in rows),
        "max_input_tokens": _min_known(row["max_input_tokens"] for row in rows),
        "max_output_tokens": _min_known(row["max_output_tokens"] for row in rows),
        "input_cost_per_token": _max_known(row["input_cost_per_token"] for row in rows),
        "output_cost_per_token": _max_known(row["output_cost_per_token"] for row in rows),
        "cache_read_input_token_cost": _max_known(
            row["cache_read_input_token_cost"] for row in rows
        ),
        "rpm": _sum_if_all(row["rpm"] for row in rows),
        "tpm": _sum_if_all(row["tpm"] for row in rows),
        "supports_function_calling": flags["supports_function_calling"],
        "supports_parallel_function_calling": None,
        "supports_vision": flags["supports_vision"],
        "supports_audio_input": flags["supports_audio_input"],
        "supports_pdf_input": flags["supports_pdf_input"],
        "supports_response_schema": flags["supports_response_schema"],
        "supports_native_streaming": flags["supports_native_streaming"],
        "supports_reasoning": None,
        "supports_web_search": None,
        "supported_openai_params": None,
        "is_public_model_group": False,
    }


def _mode(model_info: Mapping[str, Any]) -> str | None:
    value = str(model_info.get("mode") or "").strip().lower()
    return value or None


def _positive_int(value: object) -> int | None:
    # Non-positive limits are legacy "unknown" sentinels.
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        return None
    return int(value)


def _capabilities(model_info: Mapping[str, Any]) -> dict[str, bool] | None:
    declared = model_info.get("chat_capabilities")
    if hasattr(declared, "model_dump"):
        declared = declared.model_dump()
    if not isinstance(declared, Mapping):
        return None
    return {key: bool(value) for key, value in declared.items()}


def _capability_flags(capabilities: Mapping[str, bool] | None) -> dict[str, bool | None]:
    # Undeclared capabilities are unknown, not unsupported.
    if capabilities is None:
        return {flag: None for flag in CAPABILITY_FLAGS.values()}
    return {flag: bool(capabilities.get(key)) for key, flag in CAPABILITY_FLAGS.items()}


def _all_true(values: Iterable[bool | None]) -> bool | None:
    items = list(values)
    if any(value is False for value in items):
        return False
    if any(value is None for value in items):
        return None
    return True


def _known(values: Iterable[Any]) -> list[float]:
    return [
        value for value in values if isinstance(value, (int, float)) and not isinstance(value, bool)
    ]


def _min_known(values: Iterable[Any]) -> Any:
    known = _known(values)
    return min(known) if known else None


def _max_known(values: Iterable[Any]) -> Any:
    known = _known(values)
    return max(known) if known else None


def _sum_if_all(values: Iterable[Any]) -> int | None:
    items = list(values)
    if not items or any(value is None for value in items):
        return None
    return sum(items)


def _single(values: Iterable[str | None]) -> str | None:
    distinct = {value for value in values if value}
    return next(iter(distinct)) if len(distinct) == 1 else None
