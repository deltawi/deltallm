from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from src.redis_namespace import build_redis_key


_LEGACY_HEALTH_GENERATION = "legacy"


RouterHealthProbeScope = Literal["background", "manual"]


@dataclass(frozen=True, slots=True)
class RouterRedisKeyspace:
    """Single namespaced key builder for shared router state."""

    environment: str = "dev"
    application: str = "deltallm"
    schema_version: int = 1

    def active_requests(self, deployment_id: str) -> str:
        return self._key("router-active-requests", deployment_id)

    def attempt_owners(self, deployment_id: str) -> str:
        return self._key("router-attempt-owners", deployment_id)

    def cooldown(self, deployment_id: str, generation: str = _LEGACY_HEALTH_GENERATION) -> str:
        return self._key("router-cooldown", deployment_id, generation)

    def health(self, deployment_id: str, generation: str = _LEGACY_HEALTH_GENERATION) -> str:
        return self._key("router-health", deployment_id, generation)

    def health_failures(
        self, deployment_id: str, generation: str = _LEGACY_HEALTH_GENERATION
    ) -> str:
        return self._key("router-health-failures", deployment_id, generation)

    def latency(self, deployment_id: str) -> str:
        return self._key("router-latency", deployment_id)

    def usage(self, deployment_id: str, counter: str, minute: str) -> str:
        return self._key("router-usage", deployment_id, counter, minute)

    def health_recovery(
        self, deployment_id: str, generation: str = _LEGACY_HEALTH_GENERATION
    ) -> str:
        return self._key("router-health-recovery", deployment_id, generation)

    def health_probe(
        self,
        deployment_id: str,
        scope: RouterHealthProbeScope,
        generation: str = _LEGACY_HEALTH_GENERATION,
    ) -> str:
        return self._key("router-health-probe", scope, deployment_id, generation)

    def _key(self, capability: str, *identifiers: str) -> str:
        return build_redis_key(
            application=self.application,
            environment=self.environment,
            schema_version=self.schema_version,
            capability=capability,
            identifiers=identifiers,
        )
