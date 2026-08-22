from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
from typing import Any

from src.router.redis_keys import RouterHealthProbeScope


FAILURE_WINDOW_SECONDS = 300
# Health is operational evidence rather than durable configuration. Retaining it
# for 30 days preserves long cooldown/recovery history while bounding abandoned
# deployment hashes. Longer cooldowns extend the retention below.
HEALTH_STATE_RETENTION_SECONDS = 30 * 24 * 60 * 60
RECOVERY_REQUIRED_FIELD = "recovery_required"
COOLDOWN_KIND_FIELD = "cooldown_kind"
LEGACY_HEALTH_GENERATION = "legacy"


@dataclass(frozen=True, slots=True)
class DeploymentHealthRef:
    """Identity for mutable provider-health evidence.

    Admission, usage, and latency remain deployment scoped. Provider health is
    generation scoped so an attempt that started against a retired provider
    configuration cannot update the replacement deployment's health state.
    """

    deployment_id: str
    generation: str = LEGACY_HEALTH_GENERATION

    def __post_init__(self) -> None:
        if not self.deployment_id:
            raise ValueError("deployment health reference requires a deployment ID")
        if not self.generation:
            raise ValueError("deployment health reference requires a generation")


HealthRefInput = DeploymentHealthRef | str


def coerce_health_ref(value: HealthRefInput) -> DeploymentHealthRef:
    if isinstance(value, DeploymentHealthRef):
        return value
    return DeploymentHealthRef(deployment_id=str(value))


def build_deployment_health_ref(
    *,
    deployment_id: str,
    model_name: str,
    deltallm_params: dict[str, Any],
    incarnation: str | None,
    named_credential_id: str | None = None,
) -> DeploymentHealthRef:
    """Build an opaque, cross-replica-stable provider-health generation."""

    identity = {
        "version": 1,
        "model_name": model_name,
        "named_credential_id": named_credential_id,
        "incarnation": incarnation or deployment_id,
        "deltallm_params": deltallm_params,
    }
    encoded = json.dumps(
        identity,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return DeploymentHealthRef(
        deployment_id=deployment_id,
        generation=hashlib.sha256(encoded).hexdigest()[:24],
    )


def health_state_ttl_seconds(cooldown_seconds: int = 0) -> int:
    return max(
        HEALTH_STATE_RETENTION_SECONDS,
        max(0, int(cooldown_seconds)) + FAILURE_WINDOW_SECONDS,
    )


class DeploymentHealthState(str, Enum):
    HEALTHY = "healthy"
    COOLDOWN = "cooldown"
    RECOVERABLE = "recoverable"
    UNHEALTHY = "unhealthy"


@dataclass(frozen=True, slots=True)
class HealthTransitionResult:
    applied: bool
    state: DeploymentHealthState
    failure_count: int = 0
    entered_cooldown: bool = False
    recovered: bool = False


@dataclass(frozen=True, slots=True)
class HealthProbeClaim:
    health_ref: DeploymentHealthRef
    owner_token: str
    scope: RouterHealthProbeScope = "background"
    recovery: bool = False

    @property
    def recovery_token(self) -> str | None:
        return self.owner_token if self.recovery else None


HEALTH_FAILURE_SCRIPT = """
-- router_health_failure_v1
local recovery_token = ARGV[6]
local current_recovery_token = redis.call('GET', KEYS[4])
local unhealthy = redis.call('HGET', KEYS[2], 'healthy') == 'false'
  or redis.call('HGET', KEYS[2], 'recovery_required') == 'true'
if recovery_token == '' then
  if unhealthy or redis.call('EXISTS', KEYS[3]) == 1 or current_recovery_token then
    local rejected_state = redis.call('EXISTS', KEYS[3]) == 1 and 'cooldown' or 'recoverable'
    return {0, 0, 0, rejected_state}
  end
elseif current_recovery_token ~= recovery_token then
  return {0, 0, 0, 'recoverable'}
end

local failure_count = redis.call('INCR', KEYS[1])
redis.call('EXPIRE', KEYS[1], tonumber(ARGV[3]))
redis.call(
  'HSET',
  KEYS[2],
  'consecutive_failures', tostring(failure_count),
  'last_error', ARGV[1],
  'last_error_at', ARGV[5]
)

local entered_cooldown = 0
local state = 'healthy'
local recovery_required = redis.call('HGET', KEYS[2], 'recovery_required') == 'true'
if recovery_required or failure_count > tonumber(ARGV[2]) then
  state = 'cooldown'
  if redis.call('EXISTS', KEYS[3]) == 0 then
    redis.call('SETEX', KEYS[3], tonumber(ARGV[4]), ARGV[1])
    entered_cooldown = 1
    redis.call('HSET', KEYS[2], 'cooldown_kind', 'automatic')
  end
  redis.call(
    'HSET',
    KEYS[2],
    'healthy', 'false',
    'recovery_required', 'true'
  )
end

if recovery_token ~= '' and redis.call('GET', KEYS[4]) == recovery_token then
  redis.call('DEL', KEYS[4])
end
redis.call('EXPIRE', KEYS[2], tonumber(ARGV[7]))
return {1, failure_count, entered_cooldown, state}
"""


HEALTH_SUCCESS_SCRIPT = """
-- router_health_success_v1
local recovery_token = ARGV[2]
if redis.call('EXISTS', KEYS[3]) == 1 and redis.call('HGET', KEYS[2], 'cooldown_kind') == 'manual' then
  return {0, 0, 0, 'cooldown'}
end
local current_recovery_token = redis.call('GET', KEYS[4])
local unhealthy = redis.call('HGET', KEYS[2], 'healthy') == 'false'
  or redis.call('HGET', KEYS[2], 'recovery_required') == 'true'
if recovery_token == '' then
  if unhealthy or redis.call('EXISTS', KEYS[3]) == 1 or current_recovery_token then
    local rejected_state = redis.call('EXISTS', KEYS[3]) == 1 and 'cooldown' or 'recoverable'
    return {0, 0, 0, rejected_state}
  end
elseif current_recovery_token ~= recovery_token then
  return {0, 0, 0, 'recoverable'}
end

local recovered = 0
if redis.call('EXISTS', KEYS[3]) == 1 or redis.call('HGET', KEYS[2], 'healthy') == 'false' then
  recovered = 1
end
redis.call('DEL', KEYS[1])
redis.call('DEL', KEYS[3])
if recovery_token ~= '' and redis.call('GET', KEYS[4]) == recovery_token then
  redis.call('DEL', KEYS[4])
end
redis.call(
  'HSET',
  KEYS[2],
  'healthy', 'true',
  'recovery_required', 'false',
  'consecutive_failures', '0',
  'last_success_at', ARGV[1]
)
redis.call('HDEL', KEYS[2], 'last_error', 'last_error_at')
redis.call('HDEL', KEYS[2], 'cooldown_kind')
redis.call('EXPIRE', KEYS[2], tonumber(ARGV[3]))
return {1, 0, recovered, 'healthy'}
"""


MANUAL_COOLDOWN_SCRIPT = """
-- router_health_manual_cooldown_v1
redis.call('SETEX', KEYS[1], tonumber(ARGV[1]), ARGV[2])
redis.call(
  'HSET',
  KEYS[2],
  'healthy', 'false',
  'recovery_required', 'true',
  'cooldown_kind', 'manual',
  'last_error', ARGV[2],
  'last_error_at', ARGV[3]
)
redis.call('DEL', KEYS[3])
redis.call('EXPIRE', KEYS[2], tonumber(ARGV[4]))
return 1
"""


HEALTH_PROBE_CLAIM_SCRIPT = """
-- router_health_probe_claim_v1
local owner_token = ARGV[1]
local ttl_ms = tonumber(ARGV[2])
local claimed = redis.call('SET', KEYS[1], owner_token, 'NX', 'PX', ttl_ms)
if not claimed then
  return {0, 0}
end

local recovery = 0
local recoverable = redis.call('EXISTS', KEYS[2]) == 0
  and redis.call('HGET', KEYS[3], 'healthy') == 'false'
  and redis.call('HGET', KEYS[3], 'recovery_required') == 'true'
if recoverable then
  local recovery_claimed = redis.call('SET', KEYS[4], owner_token, 'NX', 'PX', ttl_ms)
  if not recovery_claimed then
    if redis.call('GET', KEYS[1]) == owner_token then
      redis.call('DEL', KEYS[1])
    end
    return {0, 0}
  end
  recovery = 1
end
return {1, recovery}
"""


HEALTH_RECOVERY_RELEASE_SCRIPT = """
-- router_health_recovery_release_v1
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
"""


HEALTH_PROBE_RELEASE_SCRIPT = """
-- router_health_probe_release_v1
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
"""
