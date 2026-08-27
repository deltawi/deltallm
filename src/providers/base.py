from __future__ import annotations

import json
import math
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

import httpx

from src.models.errors import (
    FailureClassification,
    GatewayCapacityError,
    InvalidRequestError,
    NO_HEALTHY_DEPLOYMENTS_CODE,
    ProxyError,
    RateLimitError,
    ServiceUnavailableError,
    TimeoutError,
    parse_retry_after_header,
)
from src.models.requests import ChatCompletionRequest
from src.models.responses import ChatCompletionResponse
from src.providers.error_body import (
    MAX_PROVIDER_ERROR_BODY_BYTES,
    PROVIDER_ERROR_BODY_TRUNCATED_EXTENSION as PROVIDER_ERROR_BODY_TRUNCATED_EXTENSION,
    bound_provider_error_response_body as bound_provider_error_response_body,
    provider_error_body_is_unavailable,
)

_MAX_CLASSIFICATION_MESSAGE_CHARS = 2048
_PROVIDER_ERROR_READ_CHUNK_BYTES = 8192
INVALID_PROVIDER_RESPONSE_MESSAGE = "Provider returned an invalid response"

ProviderSuccessValidator = Callable[[Mapping[str, Any]], bool]


@dataclass(frozen=True, slots=True)
class ProviderErrorDetails:
    """Bounded provider metadata used for classification before sanitization."""

    status_code: int | None
    identifiers: frozenset[str]
    message: str | None = field(default=None, repr=False)


def _mapping(value: object) -> Mapping[str, object] | None:
    return value if isinstance(value, Mapping) else None


def _normalized_identifier(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.rsplit("#", 1)[-1].strip().lower()
    if not normalized:
        return None
    return normalized.replace("-", "_").replace(" ", "_")


def _bounded_message(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    message = value.strip()
    return message[:_MAX_CLASSIFICATION_MESSAGE_CHARS] if message else None


def provider_error_details_from_payload(
    payload: object,
    *,
    status_code: int | None,
) -> ProviderErrorDetails:
    """Extract bounded classification fields from a provider-owned JSON value."""

    payload = _mapping(payload)
    if payload is None:
        return ProviderErrorDetails(status_code=status_code, identifiers=frozenset())

    nested_error = _mapping(payload.get("error"))
    error_container = nested_error or payload
    inner_error = _mapping(error_container.get("innererror"))
    underscored_inner_error = _mapping(error_container.get("inner_error"))
    containers = tuple(
        container
        for container in (payload, nested_error, inner_error, underscored_inner_error)
        if container is not None
    )
    identifiers = {
        normalized
        for container in containers
        for key in ("code", "type", "status", "reason", "__type")
        if (normalized := _normalized_identifier(container.get(key))) is not None
    }

    details = (nested_error or payload).get("details")
    if isinstance(details, list):
        for item in details[:16]:
            detail = _mapping(item)
            if detail is None:
                continue
            reason = _normalized_identifier(detail.get("reason"))
            if reason is not None:
                identifiers.add(reason)

    message = next(
        (
            bounded
            for container in containers
            for key in ("message", "Message")
            if (bounded := _bounded_message(container.get(key))) is not None
        ),
        None,
    )
    return ProviderErrorDetails(
        status_code=status_code,
        identifiers=frozenset(identifiers),
        message=message,
    )


def provider_error_details(provider_error: Exception) -> ProviderErrorDetails:
    """Read known envelope fields without retaining or returning the upstream body."""

    response = getattr(provider_error, "response", None)
    status_code = getattr(response, "status_code", None)
    if provider_error_body_is_unavailable(response):
        return ProviderErrorDetails(status_code=status_code, identifiers=frozenset())
    try:
        payload = response.json() if response is not None else None
    except (AttributeError, httpx.ResponseNotRead, RecursionError, TypeError, ValueError):
        payload = None
    return provider_error_details_from_payload(payload, status_code=status_code)


async def read_streaming_provider_error_details(
    response: httpx.Response,
    *,
    max_body_bytes: int = MAX_PROVIDER_ERROR_BODY_BYTES,
) -> ProviderErrorDetails:
    """Consume a failed streaming response up to a fixed decoded-body limit."""

    status_code = response.status_code
    if max_body_bytes <= 0:
        return ProviderErrorDetails(status_code=status_code, identifiers=frozenset())
    body = bytearray()
    try:
        async for chunk in response.aiter_bytes(
            chunk_size=min(_PROVIDER_ERROR_READ_CHUNK_BYTES, max_body_bytes + 1)
        ):
            if len(body) + len(chunk) > max_body_bytes:
                return ProviderErrorDetails(status_code=status_code, identifiers=frozenset())
            body.extend(chunk)
    except httpx.HTTPError:
        return ProviderErrorDetails(status_code=status_code, identifiers=frozenset())

    try:
        payload = json.loads(body) if body else None
    except (RecursionError, TypeError, ValueError):
        payload = None
    if provider_error_body_is_unavailable(response):
        payload = None
    return provider_error_details_from_payload(payload, status_code=status_code)


def classify_provider_failure(
    details: ProviderErrorDetails,
    *,
    context_identifiers: frozenset[str] = frozenset(),
    content_identifiers: frozenset[str] = frozenset(),
    context_message_markers: tuple[str, ...] = (),
    content_message_markers: tuple[str, ...] = (),
) -> FailureClassification | None:
    """Apply adapter-owned allowlists to structured provider error details."""

    if details.identifiers & context_identifiers:
        return FailureClassification.CONTEXT_WINDOW
    if details.identifiers & content_identifiers:
        return FailureClassification.CONTENT_POLICY

    message = (details.message or "").lower()
    if message and any(marker in message for marker in context_message_markers):
        return FailureClassification.CONTEXT_WINDOW
    if message and any(marker in message for marker in content_message_markers):
        return FailureClassification.CONTENT_POLICY
    return None


def reject_openai_compatible_failure_response(payload: object) -> None:
    """Reject documented success envelopes that represent content filtering."""

    response = _mapping(payload)
    if response is None:
        return
    choices = response.get("choices")
    if not isinstance(choices, list):
        return
    for choice_value in choices:
        choice = _mapping(choice_value)
        if choice is None:
            continue
        finish_reason = choice.get("finish_reason")
        if isinstance(finish_reason, str) and finish_reason.strip().lower() == "content_filter":
            raise InvalidRequestError(
                message="Provider rejected request",
                affects_deployment_health=False,
                failure_classification=FailureClassification.CONTENT_POLICY,
            )


def invalid_provider_response_error() -> ServiceUnavailableError:
    """Return the one sanitized error for malformed nominal-success payloads."""

    return ServiceUnavailableError(
        message=INVALID_PROVIDER_RESPONSE_MESSAGE,
        affects_deployment_health=True,
        failure_classification=FailureClassification.GENERIC,
    )


def parse_provider_json_response(response: httpx.Response) -> dict[str, Any]:
    """Parse a provider success response without exposing provider-owned content."""

    try:
        payload = response.json()
    except (RecursionError, TypeError, ValueError) as exc:
        raise invalid_provider_response_error() from exc
    if not isinstance(payload, Mapping):
        raise invalid_provider_response_error()
    return dict(payload)


def validate_provider_success_payload(
    payload: Mapping[str, Any],
    validator: ProviderSuccessValidator,
) -> None:
    """Apply a bounded schema guard without exposing provider-owned values."""

    try:
        valid = validator(payload)
    except ProxyError:
        raise
    except Exception as exc:
        raise invalid_provider_response_error() from exc
    if not valid:
        raise invalid_provider_response_error()


def is_valid_provider_token_count(value: object) -> bool:
    """Return whether a provider token count is an exact non-negative integer."""

    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def is_valid_provider_number(value: object) -> bool:
    """Return whether a provider numeric value is finite and not boolean."""

    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def map_standard_provider_error(
    provider_error: Exception,
    *,
    failure_classification: FailureClassification | None = None,
) -> ProxyError:
    if isinstance(provider_error, httpx.PoolTimeout):
        return GatewayCapacityError()

    if isinstance(provider_error, httpx.TimeoutException):
        return TimeoutError(
            affects_deployment_health=True,
            failure_classification=FailureClassification.TIMEOUT,
        )

    if isinstance(provider_error, httpx.HTTPStatusError):
        return map_standard_provider_status_error(
            provider_error.response.status_code,
            retry_after_header=provider_error.response.headers.get("retry-after"),
            failure_classification=failure_classification,
        )

    return ServiceUnavailableError(
        message="Provider unavailable",
        affects_deployment_health=True,
        failure_classification=failure_classification or FailureClassification.GENERIC,
    )


def map_standard_provider_status_error(
    status_code: int,
    *,
    retry_after_header: str | None = None,
    failure_classification: FailureClassification | None = None,
) -> ProxyError:
    """Build one stable gateway error from sanitized provider status metadata."""

    if status_code == 429:
        return RateLimitError(
            message="Provider rate limited request",
            retry_after=parse_retry_after_header(retry_after_header),
            affects_deployment_health=True,
            failure_classification=FailureClassification.RATE_LIMIT,
        )
    if status_code == 408:
        return TimeoutError(
            affects_deployment_health=True,
            failure_classification=FailureClassification.TIMEOUT,
        )
    if status_code >= 500:
        return ServiceUnavailableError(
            message="Provider unavailable",
            affects_deployment_health=True,
            failure_classification=(failure_classification or FailureClassification.GENERIC),
        )
    resolved_classification = failure_classification or FailureClassification.GENERIC
    if status_code in {401, 403, 404} and failure_classification not in {
        FailureClassification.CONTEXT_WINDOW,
        FailureClassification.CONTENT_POLICY,
    }:
        return ServiceUnavailableError(
            message="Provider unavailable",
            affects_deployment_health=True,
            failure_classification=resolved_classification,
        )
    return InvalidRequestError(
        message="Provider rejected request",
        affects_deployment_health=False,
        failure_classification=resolved_classification,
    )


def sanitize_provider_proxy_error(provider_error: ProxyError) -> ProxyError:
    """Remove provider-owned text while preserving trusted routing semantics."""

    health_impact = provider_error.affects_deployment_health
    classification = provider_error.failure_classification
    if isinstance(provider_error, GatewayCapacityError):
        return GatewayCapacityError(failure_classification=classification)
    if isinstance(provider_error, RateLimitError):
        retry_after = getattr(provider_error, "retry_after", None)
        return RateLimitError(
            message="Provider rate limited request",
            retry_after=parse_retry_after_header(
                str(retry_after) if retry_after is not None else None
            ),
            affects_deployment_health=health_impact,
            failure_classification=classification,
        )
    if isinstance(provider_error, TimeoutError):
        return TimeoutError(
            affects_deployment_health=health_impact,
            failure_classification=classification,
        )
    if isinstance(provider_error, ServiceUnavailableError):
        if provider_error.code == NO_HEALTHY_DEPLOYMENTS_CODE:
            return ServiceUnavailableError(
                message="No healthy deployments available",
                code=NO_HEALTHY_DEPLOYMENTS_CODE,
                affects_deployment_health=health_impact,
                failure_classification=classification,
            )
        return ServiceUnavailableError(
            message="Provider unavailable",
            affects_deployment_health=health_impact,
            failure_classification=classification,
        )
    if isinstance(provider_error, InvalidRequestError):
        return InvalidRequestError(
            message="Provider rejected request",
            affects_deployment_health=health_impact,
            failure_classification=classification,
        )
    retry_after = getattr(provider_error, "retry_after", None)
    return map_standard_provider_status_error(
        provider_error.status_code,
        retry_after_header=str(retry_after) if retry_after is not None else None,
        failure_classification=classification,
    )


class ProviderAdapter(ABC):
    provider_name: str
    # When True, the executor passes translate_stream the raw response bytes
    # (response.aiter_bytes()) instead of decoded text lines (response.aiter_lines()).
    # Needed for providers whose stream framing isn't line-delimited UTF-8 SSE,
    # e.g. Bedrock's binary vnd.amazon.eventstream format.
    stream_uses_bytes: bool = False

    @abstractmethod
    async def translate_request(
        self,
        canonical_request: ChatCompletionRequest,
        provider_config: dict[str, Any],
    ) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    async def translate_response(
        self, provider_response: Any, model_name: str
    ) -> ChatCompletionResponse:
        raise NotImplementedError

    async def translate_success_response(
        self,
        response: httpx.Response,
        model_name: str,
    ) -> ChatCompletionResponse:
        """Parse and translate a nominal-success response at the provider boundary."""

        payload = parse_provider_json_response(response)
        try:
            return await self.translate_response(payload, model_name)
        except ProxyError:
            raise
        except Exception as exc:
            raise invalid_provider_response_error() from exc

    @abstractmethod
    async def translate_stream(
        self,
        provider_stream: AsyncIterator[Any],
        *,
        model_name: str | None = None,
    ) -> AsyncIterator[str]:
        raise NotImplementedError

    @abstractmethod
    def map_error(
        self,
        provider_error: Exception,
        *,
        details: ProviderErrorDetails | None = None,
    ) -> ProxyError:
        raise NotImplementedError

    @abstractmethod
    async def health_check(self, provider_config: dict[str, Any]) -> bool:
        raise NotImplementedError
