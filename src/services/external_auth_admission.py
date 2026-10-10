from __future__ import annotations

import asyncio
import hashlib

from src.auth.external_config import ExternalAuthSettings
from src.auth.external_contracts import VerifiedExternalAssertion
from src.auth.external_errors import ExternalAuthError, ExternalAuthUnavailable
from src.models.errors import RateLimitError, ServiceUnavailableError
from src.redis_namespace import build_redis_channel
from src.services.limit_counter import LimitCounter, RateLimitCheck


class ExternalAuthAdmission:
    def __init__(
        self, counter: LimitCounter, settings: ExternalAuthSettings, *, environment: str
    ) -> None:
        if counter.redis is None or counter.degraded_mode != "fail_closed":
            raise ValueError("External auth requires a distributed fail-closed limiter")
        self.counter = counter
        self.settings = settings
        self.namespace = build_redis_channel(
            application="deltallm",
            environment=environment,
            schema_version=1,
            capability="external-auth",
        )

    async def admit(self, assertion: VerifiedExternalAssertion) -> None:
        slot = hashlib.sha256(assertion.integration_id.encode()).hexdigest()
        checks = [
            RateLimitCheck(
                self.namespace + "-subject",
                "{" + slot + "}:" + assertion.digest("rate-subject", assertion.claims.sub),
                self.settings.subject_exchanges_per_minute,
            ),
            RateLimitCheck(
                self.namespace + "-binding",
                "{" + slot + "}:" + assertion.digest("rate-binding", assertion.claims.binding_id),
                self.settings.binding_exchanges_per_minute,
            ),
            RateLimitCheck(
                self.namespace + "-integration",
                "{" + slot + "}",
                self.settings.integration_exchanges_per_minute,
            ),
        ]
        try:
            async with asyncio.timeout(0.1):
                await self.counter.check_rate_limits_atomic(checks)
        except RateLimitError as exc:
            raise ExternalAuthError("external_auth_rate_limited", status_code=429) from exc
        except (ServiceUnavailableError, TimeoutError) as exc:
            raise ExternalAuthUnavailable() from exc

    async def admit_denial_audit(self) -> None:
        """Limit unauthenticated durable denial records to 60 per minute globally."""
        try:
            async with asyncio.timeout(0.1):
                await self.counter.check_rate_limits_atomic(
                    [RateLimitCheck(self.namespace + "-denial", "{unverified}", 60)]
                )
        except RateLimitError as exc:
            raise ExternalAuthError("external_auth_rate_limited", status_code=429) from exc
        except (ServiceUnavailableError, TimeoutError) as exc:
            raise ExternalAuthUnavailable() from exc
