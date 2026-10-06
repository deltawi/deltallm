from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
import json

from src.models.errors import RoutingFailureAction, ServiceUnavailableError
from src.models.output_limits import MAX_OUTPUT_TOKENS
from src.models.responses import UserAPIKeyAuth
from src.redis_namespace import build_redis_key


@dataclass(frozen=True, slots=True)
class OutputScope:
    scope: str
    entity_id: str
    limit: int


def output_scopes(auth: UserAPIKeyAuth) -> tuple[OutputScope, ...]:
    values = (
        ("org_output_tpm", auth.organization_id, auth.org_output_tpm_limit),
        ("team_output_tpm", auth.team_id, auth.team_output_tpm_limit),
        ("user_output_tpm", auth.user_id, auth.user_output_tpm_limit),
        ("key_output_tpm", auth.api_key, auth.key_output_tpm_limit),
    )
    if any(limit is not None and not identity for _, identity, limit in values):
        raise ServiceUnavailableError(
            message="Output token policy identity is unavailable",
            code="output_tpm_unavailable",
            affects_deployment_health=False,
            routing_failure_action=RoutingFailureAction.FAIL_FAST,
        )
    return tuple(
        OutputScope(scope, identity, limit)
        for scope, identity, limit in values
        if limit is not None
    )


@dataclass(frozen=True, slots=True)
class OutputPolicy:
    scopes: tuple[OutputScope, ...]

    def __post_init__(self) -> None:
        if not 1 <= len(self.scopes) <= 4:
            raise ValueError("Invalid output scope count")
        if len({s.scope for s in self.scopes}) != len(self.scopes):
            raise ValueError("Duplicate output scope")
        if any(
            s.scope
            not in {"org_output_tpm", "team_output_tpm", "user_output_tpm", "key_output_tpm"}
            or not s.entity_id
            or type(s.limit) is not int
            or not 1 <= s.limit <= MAX_OUTPUT_TOKENS
            for s in self.scopes
        ):
            raise ValueError("Invalid output scope identity or limit")

    def keys(self, *, environment: str) -> tuple[str, ...]:
        return tuple(
            _key(environment, ("bucket", s.scope, sha256(s.entity_id.encode()).hexdigest()))
            for s in self.scopes
        )


def _key(environment: str, identifiers: tuple[str, ...]) -> str:
    key = build_redis_key(
        application="deltallm",
        environment=environment,
        schema_version=2,
        capability="output-tpm",
        identifiers=identifiers,
    )
    if len(key.encode()) > 512:
        raise ValueError("Output key exceeds its bound")
    return key


@dataclass(frozen=True, slots=True)
class OutputSnapshot:
    policy: OutputPolicy
    window_id: int
    reset_at: int
    current_values: tuple[int, ...]
    unknown: tuple[bool, ...]


@dataclass(frozen=True, slots=True)
class OutputAccountingEvent:
    policy: OutputPolicy
    event_id: str
    actual: int | None

    def __post_init__(self) -> None:
        if not self.event_id or len(self.event_id) > 256:
            raise ValueError("Invalid output event identity")
        if self.actual is not None and (
            type(self.actual) is not int or not 0 <= self.actual <= MAX_OUTPUT_TOKENS
        ):
            raise ValueError("Invalid output count")

    @property
    def fingerprint(self) -> str:
        value = [self.actual, [(s.scope, s.entity_id, s.limit) for s in self.policy.scopes]]
        return sha256(json.dumps(value, separators=(",", ":")).encode()).hexdigest()

    def keys(self, *, environment: str) -> tuple[str, ...]:
        return self.policy.keys(environment=environment) + (
            _key(environment, ("receipt", sha256(self.event_id.encode()).hexdigest())),
        )


def complete_output_count(usage: object) -> int | None:
    if not isinstance(usage, Mapping):
        return None
    count = usage.get("completion_tokens")
    return count if type(count) is int and 0 <= count <= MAX_OUTPUT_TOKENS else None
