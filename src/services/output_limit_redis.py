from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Protocol

from src.metrics.output_tpm import record_output_tpm
from src.services.redis_lua import RedisLuaScript
from src.models.errors import RateLimitError, RoutingFailureAction, ServiceUnavailableError
from src.services.output_limit_lua import OUTPUT_ACCOUNTING_LUA
from src.services.output_limit_types import OutputAccountingEvent, OutputPolicy, OutputSnapshot
from src.services.rate_limit_contracts import RateLimitCheck, RateLimitResult

OUTPUT_COORDINATION_TIMEOUT_SECONDS = 1.0


class OutputRedisClient(Protocol):
    async def eval(self, script: str, numkeys: int, *args: object) -> object: ...


class OutputUsageUnknownError(ServiceUnavailableError):
    def __init__(self, policy: OutputPolicy, index: int, reset: int, now: int) -> None:
        scope = policy.scopes[index]
        super().__init__(
            message="Output token usage is unknown until the next minute",
            param=scope.scope,
            code="output_tpm_usage_unknown",
            affects_deployment_health=False,
            routing_failure_action=RoutingFailureAction.FAIL_FAST,
        )
        self.retry_after = max(1, reset - now)
        self.limit = scope.limit
        self.reset_at = reset


def output_unavailable() -> ServiceUnavailableError:
    record_output_tpm("unavailable")
    return ServiceUnavailableError(
        message="Output token rate coordination is unavailable",
        code="output_tpm_unavailable",
        affects_deployment_health=False,
        routing_failure_action=RoutingFailureAction.FAIL_FAST,
    )


def output_args(policy: OutputPolicy) -> tuple[str, ...]:
    return (*(str(s.limit) for s in policy.scopes), str(len(policy.scopes)))


def check_output_failure(raw: Sequence[object], policy: OutputPolicy) -> None:
    if not raw or int(raw[0]) != 0:
        return
    kind = raw[1].decode() if isinstance(raw[1], bytes) else str(raw[1])
    if kind == "output_unavailable":
        raise output_unavailable()
    if kind not in {"output", "output_unknown"}:
        return
    index, current, reset = int(raw[2]) - 1, int(raw[3]), int(raw[6])
    scope = policy.scopes[index]
    if kind == "output_unknown":
        record_output_tpm("unavailable")
        raise OutputUsageUnknownError(policy, index, reset, int(raw[7]))
    record_output_tpm("denied")
    error = RateLimitError(
        message=f"Output token rate limit exceeded for scope '{scope.scope}'",
        param=scope.scope,
        code=f"{scope.scope}_exceeded",
        retry_after=max(1, reset - int(raw[7])),
        affects_deployment_health=False,
        routing_failure_action=RoutingFailureAction.FAIL_FAST,
    )
    error.rate_limit_checks = [
        RateLimitCheck(s.scope, s.entity_id, s.limit, dimension="output_tokens")
        for s in policy.scopes
    ]
    error.rate_limit_current = current
    error.output_reset_at = reset
    raise error


def attach_output_result(
    result: RateLimitResult,
    raw: Sequence[object],
    policy: OutputPolicy,
) -> None:
    marker = next((i for i, value in enumerate(raw) if value in ("output_v2", b"output_v2")), -1)
    if marker < 0 or len(raw) != marker + 3 + len(policy.scopes):
        raise output_unavailable()
    window, reset = int(raw[marker + 1]), int(raw[marker + 2])
    values = tuple(int(v) for v in raw[marker + 3 :])
    record_output_tpm("admitted")
    result.output_snapshot = OutputSnapshot(policy, window, reset, values, (False,) * len(values))
    result.checks.extend(
        RateLimitCheck(s.scope, s.entity_id, s.limit, dimension="output_tokens")
        for s in policy.scopes
    )
    result.current_values.extend(values)
    result.window_resets.extend([reset] * len(values))


async def account_output(
    redis_client: OutputRedisClient, event: OutputAccountingEvent, *, environment: str
) -> OutputSnapshot:
    try:
        raw = await evaluate_output_script(
            OUTPUT_ACCOUNTING_LUA,
            redis_client,
            event.keys(environment=environment),
            (event.fingerprint, str(event.actual if event.actual is not None else -1)),
        )
        n = len(event.policy.scopes)
        if not isinstance(raw, (list, tuple)) or len(raw) != 4 + 2 * n or int(raw[0]) != 1:
            raise output_unavailable()
        if int(raw[3]):
            record_output_tpm("saturated")
        return OutputSnapshot(
            event.policy,
            int(raw[1]),
            int(raw[2]),
            tuple(int(v) for v in raw[4 : 4 + n]),
            tuple(bool(int(v)) for v in raw[4 + n :]),
        )
    except ServiceUnavailableError:
        raise
    except Exception as exc:
        raise output_unavailable() from exc


async def evaluate_output_script(
    script: RedisLuaScript,
    redis_client: OutputRedisClient,
    keys: tuple[str, ...],
    arguments: tuple[object, ...],
) -> object:
    async with asyncio.timeout(OUTPUT_COORDINATION_TIMEOUT_SECONDS):
        return await script.eval(redis_client, len(keys), *keys, *arguments)
