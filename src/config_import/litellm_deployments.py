"""Convert LiteLLM `model_list` entries into DeltaLLM deployments."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError

from src.config import ModelDeployment
from src.config_import.litellm_providers import (
    PROVIDER_TARGETS,
    UNSUPPORTED_PROVIDERS,
    ProviderTarget,
    normalize_api_base,
    split_litellm_model,
)
from src.config_import.report import ImportReport, Severity, flag_literal_secrets
from src.providers.resolution import validate_provider_mode_compatibility

# LiteLLM `litellm_params` key -> DeltaLLM `deltallm_params` key.
_PARAM_KEYS: dict[str, str] = {
    "api_key": "api_key",
    "api_base": "api_base",
    "base_url": "api_base",
    "timeout": "timeout",
    "stream_timeout": "stream_timeout",
    "rpm": "rpm",
    "tpm": "tpm",
    "weight": "weight",
    "max_tokens": "max_tokens",
    "aws_access_key_id": "aws_access_key_id",
    "aws_secret_access_key": "aws_secret_access_key",
    "aws_session_token": "aws_session_token",
    "aws_region_name": "region",
    "region_name": "region",
}
_INTEGER_PARAMS = frozenset({"timeout", "stream_timeout", "rpm", "tpm", "weight", "max_tokens"})

# Request parameters that LiteLLM sends on every call. DeltaLLM sends them as defaults.
_DEFAULT_REQUEST_PARAMS = frozenset(
    {
        "temperature",
        "top_p",
        "max_completion_tokens",
        "frequency_penalty",
        "presence_penalty",
        "seed",
        "stop",
        "reasoning_effort",
    }
)

# Keys that LiteLLM accepts in either litellm_params or model_info and DeltaLLM keeps in model_info.
_MODEL_INFO_KEYS: dict[str, str] = {
    "mode": "mode",
    "input_cost_per_token": "input_cost_per_token",
    "output_cost_per_token": "output_cost_per_token",
    "cache_read_input_token_cost": "input_cost_per_token_cache_hit",
    "input_cost_per_token_cache_hit": "input_cost_per_token_cache_hit",
    "input_cost_per_second": "input_cost_per_second",
    "output_cost_per_second": "output_cost_per_second",
    "input_cost_per_character": "input_cost_per_character",
    "output_cost_per_character": "output_cost_per_character",
    "input_cost_per_image": "input_cost_per_image",
    "output_cost_per_image": "output_cost_per_image",
    "input_cost_per_audio_token": "input_cost_per_audio_token",
    "output_cost_per_audio_token": "output_cost_per_audio_token",
    "input_cost_per_query": "cost_per_request",
    "max_tokens": "max_tokens",
    "max_input_tokens": "max_input_tokens",
    "max_output_tokens": "max_output_tokens",
    "output_vector_size": "output_vector_size",
    "access_groups": "access_groups",
    "tags": "tags",
}
# In litellm_params, max_tokens is a request default, not the context capacity.
_PARAM_INFO_KEYS = frozenset(_MODEL_INFO_KEYS) - {"max_tokens"}
_MODE_MAP: dict[str, str | None] = {
    "chat": "chat",
    "completion": "chat",
    "responses": "chat",
    "embedding": "embedding",
    "image_generation": "image_generation",
    "audio_speech": "audio_speech",
    "audio_transcription": "audio_transcription",
    "realtime": "realtime",
    "rerank": "rerank",
}
_MODE_HINTS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("embedding", "embed-"), "embedding"),
    (("rerank",), "rerank"),
    (("whisper", "transcribe"), "audio_transcription"),
    (("tts-", "-tts"), "audio_speech"),
    (("dall-e", "gpt-image", "imagen"), "image_generation"),
)
_IGNORED_MODEL_INFO = frozenset({"id", "db_model", "base_model", "updated_at", "created_at"})
_ANTHROPIC_ONLY_PARAMS = frozenset({"api_version"})
KEYLESS_STAND_IN = "not-needed"


def convert_model_list(raw: object, report: ImportReport) -> list[dict[str, Any]]:
    if raw is None:
        report.add(Severity.ACTION, "model_list", "is empty. Add deployments after import.")
        return []
    if not isinstance(raw, list):
        report.add(Severity.SKIPPED, "model_list", "is not a list and was not imported.")
        return []
    converted: list[dict[str, Any]] = []
    for index, entry in enumerate(raw):
        path = f"model_list[{index}]"
        deployment = _convert_entry(entry, path, report)
        if deployment is None:
            report.skipped_deployments += 1
            continue
        converted.append(deployment)
        report.imported_deployments += 1
    return converted


def _convert_entry(entry: object, path: str, report: ImportReport) -> dict[str, Any] | None:
    if not isinstance(entry, Mapping):
        report.add(Severity.SKIPPED, path, "is not a mapping.")
        return None
    model_name = str(entry.get("model_name") or "").strip()
    params = entry.get("litellm_params") or entry.get("deltallm_params")
    if not model_name or not isinstance(params, Mapping) or not params.get("model"):
        report.add(Severity.SKIPPED, path, "needs model_name and litellm_params.model.")
        return None
    path = f"{path} ({model_name})"
    if "*" in model_name or "*" in str(params.get("model")):
        report.add(
            Severity.SKIPPED,
            path,
            "wildcard models are not supported. Add each model you need as a deployment.",
        )
        return None
    raw_info = entry.get("model_info")
    model_info: Mapping[str, Any] = raw_info if isinstance(raw_info, Mapping) else {}
    target = _resolve_target(params, path, report)
    if target is None:
        return None
    prefix, upstream_model, provider_target = target
    deltallm_params = _convert_params(params, provider_target, prefix, path, report)
    if deltallm_params is None:
        return None
    deltallm_params["model"] = upstream_model
    deployment: dict[str, Any] = {"model_name": model_name, "deltallm_params": deltallm_params}
    info = _convert_model_info(params, model_info, path, report)
    if info is None:
        return None
    if info:
        deployment["model_info"] = info
    deployment_id = model_info.get("id")
    if isinstance(deployment_id, str) and deployment_id.strip():
        deployment["deployment_id"] = deployment_id.strip()
    return _validated(deployment, path, report)


def _resolve_target(
    params: Mapping[str, Any], path: str, report: ImportReport
) -> tuple[str, str, ProviderTarget] | None:
    custom = params.get("custom_llm_provider")
    prefix, upstream_model = split_litellm_model(
        str(params["model"]), str(custom) if custom else None
    )
    if prefix is None:
        report.add(
            Severity.SKIPPED,
            path,
            "the provider cannot be found from the model name. Add a provider prefix "
            "such as `openai/` and run the import again.",
        )
        return None
    if prefix in UNSUPPORTED_PROVIDERS:
        report.add(Severity.SKIPPED, path, f"`{prefix}/`: {UNSUPPORTED_PROVIDERS[prefix]}")
        return None
    target = PROVIDER_TARGETS.get(prefix)
    if target is None:
        report.add(Severity.SKIPPED, path, f"provider `{prefix}` is not known to the importer.")
        return None
    if prefix == "bedrock":
        upstream_model = _bedrock_model(upstream_model, path, report)
    if target.note:
        report.add(Severity.INFO, path, target.note)
    return prefix, upstream_model, target


def _bedrock_model(model: str, path: str, report: ImportReport) -> str:
    for route in ("converse/", "invoke/", "converse_like/"):
        if model.startswith(route):
            if route != "converse/":
                report.add(
                    Severity.CHANGED,
                    path,
                    "DeltaLLM calls Bedrock through the Converse API, not InvokeModel.",
                )
            return model[len(route) :]
    return model


def _convert_params(
    params: Mapping[str, Any],
    target: ProviderTarget,
    prefix: str,
    path: str,
    report: ImportReport,
) -> dict[str, Any] | None:
    converted: dict[str, Any] = {"model": None}  # Filled by the caller; keeps it first.
    if target.provider is not None:
        converted["provider"] = target.provider
    flag_literal_secrets(report, f"{path}.litellm_params", params.items())
    for key, value in params.items():
        if key in {"model", "custom_llm_provider"} or value is None:
            continue
        if key in _PARAM_KEYS:
            converted[_PARAM_KEYS[key]] = _coerce_param(key, value, path, report)
        elif key in _ANTHROPIC_ONLY_PARAMS and target.provider == "anthropic":
            converted[key] = value
        elif key == "api_version" and target.provider == "azure_openai":
            report.add(
                Severity.CHANGED,
                f"{path}.api_version",
                "dropped. DeltaLLM uses the Azure OpenAI v1 API, which needs no api-version.",
            )
        elif key in _DEFAULT_REQUEST_PARAMS or key in _PARAM_INFO_KEYS or key == "order":
            continue  # Moved into model_info by _convert_model_info.
        else:
            report.add(Severity.SKIPPED, f"{path}.litellm_params.{key}", "has no equivalent.")
    original_base = converted.get("api_base")
    if original_base is None and target.provider == "azure_openai":
        converted["api_base"] = original_base = "os.environ/AZURE_API_BASE"
        report.add(
            Severity.ACTION,
            f"{path}.api_base",
            "LiteLLM read AZURE_API_BASE. Set it to https://<resource>.openai.azure.com/openai/v1.",
        )
    api_base, changed = normalize_api_base(target, original_base)
    if api_base is None and target.base_rule == "required_append_v1":
        report.add(Severity.SKIPPED, path, f"`{prefix}/` needs api_base (the server URL).")
        return None
    if api_base is not None:
        converted["api_base"] = api_base
    if changed and original_base is not None:
        report.add(Severity.CHANGED, f"{path}.api_base", "path changed to the OpenAI v1 form.")
    _apply_credentials(converted, target, path, report)
    return converted


def _apply_credentials(
    converted: dict[str, Any], target: ProviderTarget, path: str, report: ImportReport
) -> None:
    if target.provider == "bedrock":
        if not converted.get("aws_access_key_id"):
            report.add(
                Severity.ACTION,
                path,
                "has no AWS keys. DeltaLLM reads AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY and "
                "AWS_REGION from the environment. AWS profiles and role names are not used.",
            )
        return
    if "api_key" in converted:
        return
    if target.keyless:
        converted["api_key"] = KEYLESS_STAND_IN
        report.add(
            Severity.INFO,
            path,
            f"no api_key: `{KEYLESS_STAND_IN}` is set so the deployment never uses another "
            "provider's key. Replace it if the server needs a key.",
        )
    elif target.default_key_env:
        converted["api_key"] = f"os.environ/{target.default_key_env}"
        report.add(
            Severity.CHANGED,
            f"{path}.api_key",
            f"LiteLLM read {target.default_key_env} by default. It is now explicit.",
        )
    else:
        report.add(Severity.ACTION, path, "has no api_key. Add one before you send traffic.")


def _coerce_param(key: str, value: Any, path: str, report: ImportReport) -> Any:
    if key not in _INTEGER_PARAMS or isinstance(value, bool) or not isinstance(value, float):
        return value
    rounded = math.ceil(value)
    if rounded != value:
        report.add(Severity.CHANGED, f"{path}.{key}", "rounded up to a whole number.")
    return rounded


def _convert_model_info(
    params: Mapping[str, Any],
    model_info: Mapping[str, Any],
    path: str,
    report: ImportReport,
) -> dict[str, Any] | None:
    info: dict[str, Any] = {}
    defaults = {k: v for k, v in params.items() if k in _DEFAULT_REQUEST_PARAMS}
    if defaults:
        info["default_params"] = defaults
        report.add(
            Severity.CHANGED,
            f"{path}.litellm_params",
            "request parameters moved to model_info.default_params. A value in the "
            "client request has priority.",
        )
    for key, value in params.items():
        if key in _PARAM_INFO_KEYS and value is not None:
            info[_MODEL_INFO_KEYS[key]] = value
    for key, value in model_info.items():
        if key in _MODEL_INFO_KEYS and value is not None:
            info[_MODEL_INFO_KEYS[key]] = value
    order = params.get("order", model_info.get("order"))
    if isinstance(order, int) and not isinstance(order, bool):
        info["priority"] = max(order, 0)
        report.add(
            Severity.INFO,
            path,
            "`order` became model_info.priority. It applies only with "
            "routing_strategy priority-based-routing.",
        )
    for key in map(str, model_info):
        if key in _MODEL_INFO_KEYS or key in _IGNORED_MODEL_INFO or key == "order":
            continue
        if key == "cache_creation_input_token_cost":
            report.add(
                Severity.CHANGED,
                f"{path}.model_info.{key}",
                "dropped. DeltaLLM bills cache writes at the input token price.",
            )
        elif not key.startswith("supports_"):
            report.add(Severity.SKIPPED, f"{path}.model_info.{key}", "has no equivalent.")
    if "mode" not in info:
        inferred = _infer_mode(str(params.get("model") or ""))
        if inferred is not None:
            info["mode"] = inferred
            report.add(Severity.CHANGED, path, f"mode `{inferred}` was set from the model name.")
    if "mode" in info:
        mode = _MODE_MAP.get(str(info["mode"]))
        if mode is None:
            report.add(Severity.SKIPPED, path, f"model mode `{info['mode']}` is not supported.")
            return None
        info["mode"] = mode
    return info


def _infer_mode(model: str) -> str | None:
    """LiteLLM finds a model's mode in its cost map. DeltaLLM needs model_info.mode."""

    name = model.lower().rsplit("/", 1)[-1]
    for markers, mode in _MODE_HINTS:
        if any(marker in name for marker in markers):
            return mode
    return None


def _validated(
    deployment: dict[str, Any], path: str, report: ImportReport
) -> dict[str, Any] | None:
    try:
        ModelDeployment.model_validate(deployment)
        validate_provider_mode_compatibility(deployment)
    except (ValidationError, ValueError) as exc:
        report.add(Severity.SKIPPED, path, f"failed DeltaLLM validation: {_safe_error(exc)}")
        return None
    return deployment


def _safe_error(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        first = exc.errors(include_url=False, include_context=False, include_input=False)[0]
        location = ".".join(str(part) for part in first.get("loc") or ())
        return f"{location}: {first.get('msg')}"
    return str(exc)
