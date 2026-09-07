"""Untrusted, bounded Redis projection; PostgreSQL owns durable policy semantics."""

from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.route_group_config import ModelMode, RoutingStrategyName
from src.router.context_policy import parse_context_routing_policy
from src.router.policy_validation import ALLOWED_RETRYABLE_ERROR_CLASSES

ROUTE_GROUP_RUNTIME_CACHE_SCHEMA_VERSION = 2
ROUTE_GROUP_RUNTIME_CACHE_MAX_BYTES = 4 * 1024 * 1024


def _validate_numeric(value: object, *, minimum: float, integer: bool) -> object:
    if value is None:
        return value
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError("invalid numeric cache setting")
    try:
        number = float(value)
    except (ValueError, OverflowError) as exc:
        raise ValueError("invalid numeric cache setting") from exc
    if not math.isfinite(number) or number < minimum:
        raise ValueError("invalid numeric cache setting")
    if integer:
        # Numeric strings are valid historical JSON; retain them without coercion.
        if isinstance(value, float):
            raise ValueError("integer cache setting required")
        try:
            int(value)
        except ValueError as exc:
            raise ValueError("integer cache setting required") from exc
    return value


class _CacheTimeouts(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True)

    global_ms: int | str | None = None
    global_seconds: float | str | None = None

    @field_validator("global_ms", mode="before")
    @classmethod
    def valid_milliseconds(cls, value: object) -> object:
        return _validate_numeric(value, minimum=1, integer=True)

    @field_validator("global_seconds", mode="before")
    @classmethod
    def valid_seconds(cls, value: object) -> object:
        return _validate_numeric(value, minimum=0.001, integer=False)


class _CacheRetry(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True)

    max_attempts: int | str | None = None
    retryable_error_classes: list[str] | None = None

    @field_validator("max_attempts", mode="before")
    @classmethod
    def valid_attempts(cls, value: object) -> object:
        return _validate_numeric(value, minimum=0, integer=True)

    @field_validator("retryable_error_classes")
    @classmethod
    def valid_error_classes(cls, value: list[str] | None) -> list[str] | None:
        if value is not None and any(
            item.strip() not in ALLOWED_RETRYABLE_ERROR_CLASSES for item in value
        ):
            raise ValueError("unknown retry error class")
        return value


class _CacheMember(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    deployment_id: str = Field(min_length=1)
    enabled: bool = True
    weight: int | None = Field(default=None, ge=1)
    priority: int | None = Field(default=None, ge=0)


class _CacheGroup(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    key: str = Field(min_length=1)
    mode: ModelMode | None = None
    enabled: bool = True
    strategy: RoutingStrategyName | None = None
    policy_version: int | None = Field(default=None, ge=1)
    policy_semantics_version: int | None = Field(default=None, ge=1)
    timeouts: _CacheTimeouts | None = None
    retry: _CacheRetry | None = None
    context: dict[str, object] | None = None
    default_prompt: dict[str, str] | None = None
    access_groups: list[str] | None = None
    members: list[_CacheMember]

    @field_validator("context")
    @classmethod
    def valid_context(cls, value: dict[str, object] | None) -> dict[str, object] | None:
        # Use the runtime's canonical parser and retain opaque historical extensions.
        # A malformed owned field must reject the whole snapshot, never erase context.
        if value is not None and parse_context_routing_policy(value) is None:
            raise ValueError("invalid context routing cache setting")
        return value


class RouteGroupRuntimeCacheEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal[2]
    selector_activation_state: Literal["inactive"]
    revision: int = Field(ge=0)
    groups: list[_CacheGroup]
    database_initialized: bool | None
