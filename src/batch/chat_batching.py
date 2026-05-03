from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, is_dataclass
import json
import operator
from typing import Any, Literal, Protocol

from pydantic import ValidationError

from src.batch.retry import BatchResponseShapeError
from src.config import ChatBatchingConfig
from src.models.errors import InvalidRequestError, RateLimitError, ServiceUnavailableError, TimeoutError as GatewayTimeoutError
from src.models.requests import ChatCompletionRequest, MCPToolDefinition
from src.providers.resolution import resolve_provider

ChatBatchingMode = Literal["disabled", "concurrent", "sync_microbatch"]
DEFAULT_CHAT_BATCHING_MODE: ChatBatchingMode = "concurrent"


@dataclass(frozen=True, slots=True)
class ChatBatchingSettings:
    mode: ChatBatchingMode = DEFAULT_CHAT_BATCHING_MODE
    max_in_flight: int | None = None
    upstream_max_batch_size: int = 1
    max_total_input_tokens: int | None = None
    require_homogeneous_params: bool = True


@dataclass(frozen=True, slots=True)
class ChatMicrobatchEligibility:
    eligible: bool
    reason: str | None
    group_key: tuple[Any, ...] | None
    input_tokens: int


@dataclass(frozen=True, slots=True)
class NormalizedChatMicrobatchResult:
    index: int
    response_body: dict[str, Any] | None
    usage: dict[str, Any] | None
    error: Exception | None
    api_latency_ms: float | None


class ChatMicrobatchExecutor(Protocol):
    async def execute_chat_microbatch(
        self,
        *,
        requests: list[ChatCompletionRequest],
        deployment: Any,
        request_context: dict[str, Any],
    ) -> Sequence[Any]:
        ...


def resolve_chat_batching_settings(deltallm_params: Mapping[str, Any] | None) -> ChatBatchingSettings:
    raw_config = dict(deltallm_params or {}).get("chat_batching")
    if raw_config is None:
        return ChatBatchingSettings()

    try:
        config = raw_config if isinstance(raw_config, ChatBatchingConfig) else ChatBatchingConfig.model_validate(raw_config)
    except (TypeError, ValidationError, ValueError):
        return ChatBatchingSettings()

    return ChatBatchingSettings(
        mode=config.mode,
        max_in_flight=config.max_in_flight,
        upstream_max_batch_size=max(1, int(config.upstream_max_batch_size or 1)),
        max_total_input_tokens=config.max_total_input_tokens,
        require_homogeneous_params=config.require_homogeneous_params,
    )


def estimate_chat_input_tokens(payload: ChatCompletionRequest) -> int:
    total_chars = 0
    for message in payload.messages:
        total_chars += len(message.role)
        content = message.content
        if isinstance(content, str):
            total_chars += len(content)
        else:
            total_chars += len(json.dumps(content, sort_keys=True, separators=(",", ":"), default=str))
    return max(1, (total_chars + 3) // 4)


def classify_chat_microbatch_request(
    *,
    payload: ChatCompletionRequest,
    deployment: Any,
    model_group: str,
    failover_kwargs: Mapping[str, Any],
) -> ChatMicrobatchEligibility:
    input_tokens = estimate_chat_input_tokens(payload)
    if payload.stream is True:
        return ChatMicrobatchEligibility(False, "streaming", None, input_tokens)
    if any(isinstance(tool, MCPToolDefinition) for tool in payload.tools or []):
        return ChatMicrobatchEligibility(False, "mcp_tools", None, input_tokens)
    if payload.tools:
        return ChatMicrobatchEligibility(False, "tools", None, input_tokens)
    if payload.response_format is not None:
        return ChatMicrobatchEligibility(False, "response_format", None, input_tokens)
    if payload.n not in (None, 1):
        return ChatMicrobatchEligibility(False, "multiple_choices", None, input_tokens)

    params = dict(getattr(deployment, "deltallm_params", {}) or {})
    deployment_id = str(getattr(deployment, "deployment_id", None) or id(deployment))
    request_shape = payload.model_dump(
        mode="json",
        exclude={"messages"},
        exclude_none=True,
    )
    group_key = (
        deployment_id,
        model_group,
        resolve_provider(params),
        str(params.get("api_base") or ""),
        str(params.get("model") or ""),
        _stable_json(request_shape),
        _stable_json(failover_kwargs),
    )
    return ChatMicrobatchEligibility(True, None, group_key, input_tokens)


def normalize_chat_microbatch_results(
    raw_results: Sequence[Any],
    *,
    expected_count: int,
    custom_ids: Sequence[str],
) -> list[NormalizedChatMicrobatchResult]:
    if not isinstance(raw_results, Sequence) or isinstance(raw_results, (str, bytes, bytearray)):
        raise BatchResponseShapeError("chat microbatch response is not a result list")
    if len(raw_results) != expected_count:
        raise BatchResponseShapeError(
            f"chat microbatch response length mismatch expected={expected_count} actual={len(raw_results)}"
        )

    custom_id_to_index = {custom_id: index for index, custom_id in enumerate(custom_ids)}
    normalized_rows: list[NormalizedChatMicrobatchResult | None] = [None] * expected_count
    for row_number, raw_result in enumerate(raw_results):
        result = _result_mapping(raw_result)
        index = _result_index(result, custom_id_to_index)
        if index < 0 or index >= expected_count:
            raise BatchResponseShapeError(f"chat microbatch response item {row_number} index out of range index={index}")
        if normalized_rows[index] is not None:
            raise BatchResponseShapeError(f"chat microbatch response contains duplicate index={index}")

        api_latency_ms = _optional_float(result.get("api_latency_ms") or result.get("latency_ms"))
        error = _normalize_result_error(result.get("error") or result.get("exception"))
        response_body = _result_response_body(result)
        usage = _result_usage(result, response_body)
        if error is None and response_body is None:
            error = BatchResponseShapeError(f"chat microbatch response item {row_number} is missing response body")
        if error is None and not usage:
            error = BatchResponseShapeError(f"chat microbatch response item {row_number} is missing per-item usage")

        normalized_rows[index] = NormalizedChatMicrobatchResult(
            index=index,
            response_body=response_body,
            usage=usage,
            error=error,
            api_latency_ms=api_latency_ms,
        )

    if any(row is None for row in normalized_rows):
        raise BatchResponseShapeError("chat microbatch response is missing one or more expected indexes")
    return [row for row in normalized_rows if row is not None]


def _stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _result_mapping(raw_result: Any) -> Mapping[str, Any]:
    if isinstance(raw_result, Mapping):
        return raw_result
    if is_dataclass(raw_result):
        return asdict(raw_result)
    if hasattr(raw_result, "__dict__"):
        return vars(raw_result)
    raise BatchResponseShapeError("chat microbatch response item is not an object")


def _result_index(result: Mapping[str, Any], custom_id_to_index: Mapping[str, int]) -> int:
    raw_item_index = result.get("item_index")
    raw_index = result.get("index")
    custom_id = result.get("custom_id")
    item_index = _coerce_result_index(raw_item_index) if raw_item_index is not None else None
    index = _coerce_result_index(raw_index) if raw_index is not None else item_index
    if item_index is not None and index is not None and item_index != index:
        raise BatchResponseShapeError("chat microbatch response item has mismatched item_index and index")
    custom_id_index = _custom_id_result_index(custom_id, custom_id_to_index) if custom_id is not None else None

    if index is not None and custom_id_index is not None:
        if index != custom_id_index:
            raise BatchResponseShapeError("chat microbatch response item has mismatched index and custom_id")
        return index
    if index is not None:
        return index
    if custom_id_index is not None:
        return custom_id_index
    raise BatchResponseShapeError("chat microbatch response item is missing index or custom_id")


def _coerce_result_index(raw_index: Any) -> int:
    if isinstance(raw_index, bool):
        raise BatchResponseShapeError("chat microbatch response item has invalid index")
    if isinstance(raw_index, str):
        stripped = raw_index.strip()
        if not stripped:
            raise BatchResponseShapeError("chat microbatch response item has invalid index")
        try:
            return int(stripped)
        except ValueError as exc:
            raise BatchResponseShapeError("chat microbatch response item has invalid index") from exc
    try:
        return operator.index(raw_index)
    except TypeError as exc:
        raise BatchResponseShapeError("chat microbatch response item has invalid index") from exc


def _custom_id_result_index(custom_id: Any, custom_id_to_index: Mapping[str, int]) -> int:
    custom_id_value = str(custom_id)
    if custom_id_value not in custom_id_to_index:
        raise BatchResponseShapeError("chat microbatch response item has unknown custom_id")
    return custom_id_to_index[custom_id_value]


def _result_response_body(result: Mapping[str, Any]) -> dict[str, Any] | None:
    raw_response = result.get("response_body", result.get("response", result.get("body")))
    if raw_response is None:
        return None
    if not isinstance(raw_response, Mapping):
        return None
    return dict(raw_response)


def _result_usage(result: Mapping[str, Any], response_body: Mapping[str, Any] | None) -> dict[str, Any] | None:
    raw_usage = result.get("usage")
    if raw_usage is None and response_body is not None:
        raw_usage = response_body.get("usage")
    if not isinstance(raw_usage, Mapping):
        return None
    return dict(raw_usage)


def _normalize_result_error(raw_error: Any) -> Exception | None:
    if raw_error is None:
        return None
    if isinstance(raw_error, Exception):
        return raw_error

    if not isinstance(raw_error, Mapping):
        return InvalidRequestError(message=str(raw_error))

    message = _error_message(raw_error)
    nested_error = _nested_error_mapping(raw_error)
    status_code = _first_optional_int(
        raw_error.get("status_code"),
        raw_error.get("status"),
        raw_error.get("http_status"),
        raw_error.get("code"),
        nested_error.get("status_code") if nested_error is not None else None,
        nested_error.get("status") if nested_error is not None else None,
        nested_error.get("http_status") if nested_error is not None else None,
        nested_error.get("code") if nested_error is not None else None,
    )
    error_kind = _error_kind(raw_error)
    retry_after = _first_optional_int(
        raw_error.get("retry_after"),
        raw_error.get("retry_after_seconds"),
        nested_error.get("retry_after") if nested_error is not None else None,
        nested_error.get("retry_after_seconds") if nested_error is not None else None,
    )

    if status_code == 429 or "rate_limit" in error_kind or "rate limit" in error_kind:
        return RateLimitError(message=message, retry_after=retry_after)
    if status_code == 408 or "timeout" in error_kind or "timed_out" in error_kind:
        return GatewayTimeoutError(message=message)
    if (status_code is not None and status_code >= 500) or any(
        token in error_kind for token in ("service_unavailable", "server_error", "overload", "upstream_5xx")
    ):
        return ServiceUnavailableError(message=message, affects_deployment_health=True)
    return InvalidRequestError(message=message)


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _first_optional_int(*values: Any) -> int | None:
    for value in values:
        parsed = _optional_int(value)
        if parsed is not None:
            return parsed
    return None


def _error_message(raw_error: Mapping[str, Any]) -> str:
    nested = raw_error.get("error")
    if isinstance(nested, Mapping):
        message = nested.get("message") or nested.get("detail")
    else:
        message = raw_error.get("message") or raw_error.get("detail") or nested
    return str(message or raw_error)


def _nested_error_mapping(raw_error: Mapping[str, Any]) -> Mapping[str, Any] | None:
    nested = raw_error.get("error")
    return nested if isinstance(nested, Mapping) else None


def _error_kind(raw_error: Mapping[str, Any]) -> str:
    nested_values: list[Any] = []
    nested = _nested_error_mapping(raw_error)
    if nested is not None:
        nested_values = [nested.get("type"), nested.get("code")]
    values = [
        raw_error.get("type"),
        raw_error.get("error_type"),
        raw_error.get("code"),
        raw_error.get("status"),
        *nested_values,
    ]
    return " ".join(str(value).lower() for value in values if value is not None)
