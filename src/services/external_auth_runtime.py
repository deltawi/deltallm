from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from time import monotonic, time
from typing import TypeVar

from prisma import Prisma
from prisma.errors import PrismaError
from redis.exceptions import RedisError

from src.auth.external_client import ExternalClientResolver
from src.auth.external_config import ExternalAuthSettings
from src.auth.external_contracts import ExternalPurpose, VerifiedExternalAssertion
from src.auth.external_crypto import ExternalCryptoExecutor
from src.auth.external_errors import (
    ExternalAuthError,
    ExternalAuthUnavailable,
    InvalidExternalAssertion,
)
from src.audit.actions import AuditAction
from src.concurrency import BoundedCapacityGate, CapacityGateFull, CapacityGateTimedOut
from src.db.external_auth_cleanup import ExternalAuthCleanupRepository
from src.db.external_auth_integrations import ExternalIntegrationRepository
from src.db.external_auth_transactions import ExternalAuthTransactions
from src.metrics.external_auth import (
    external_auth_cleanup,
    external_auth_cleanup_backlog,
    external_auth_cleanup_oldest,
    external_auth_denials,
    external_auth_latency,
    external_auth_requests,
)
from src.services.external_auth_administration import ExternalAuthAdministration
from src.services.external_auth_admission import ExternalAuthAdmission
from src.services.external_auth_audit import ExternalAuthAudit, ExternalAuditEvent
from src.services.external_auth_exchange import ExternalAuthExchange
from src.services.external_auth_linking import ExternalAuthLinking
from src.services.external_auth_revocation import ExternalAuthRevocation
from src.services.external_inference_keys import ExternalInferenceKeyService
from src.services.external_auth_sessions import ExternalSessionService
from src.services.platform_identity_service import PlatformIdentityService

T = TypeVar("T")


class ExternalAuthRuntime:
    """Own all external-auth work and enforce one end-to-end request deadline."""

    def __init__(
        self,
        *,
        db: Prisma,
        settings: ExternalAuthSettings,
        crypto: ExternalCryptoExecutor,
        admission: ExternalAuthAdmission,
        audit: ExternalAuthAudit,
        identities: PlatformIdentityService,
    ) -> None:
        self.db = db
        self.settings = settings
        self.client_resolver = ExternalClientResolver(settings)
        self.crypto = crypto
        self.admission = admission
        self.audit = audit
        self.identities = identities
        self.transactions = ExternalAuthTransactions(db)
        self.exchange = ExternalAuthExchange(self.transactions, audit, identities, settings)
        self.revocation = ExternalAuthRevocation(self.transactions, audit, settings)
        self.linking = ExternalAuthLinking(self.transactions, audit, identities)
        self.administration = ExternalAuthAdministration(self.transactions, audit, settings)
        self.sessions = ExternalSessionService(self.transactions, audit)
        self.inference_keys = ExternalInferenceKeyService(
            self.transactions, identities.sessions.salt
        )
        self.ingress = BoundedCapacityGate(concurrency=4, max_waiters=0)
        self.cleanup_task: asyncio.Task[None] | None = None
        self.cleanup_healthy = False
        self.closed = False
        self.cache_worker_ready: Callable[[], bool] = lambda: True

    @property
    def ready(self) -> bool:
        return (
            not self.closed
            and self.cache_worker_ready()
            and self.crypto.ready
            and self.cleanup_healthy
            and self.cleanup_task is not None
            and not self.cleanup_task.done()
            and self.audit.service.worker_health.ready
        )

    async def start(self) -> None:
        async with self.transactions.transaction() as db:
            if not await ExternalAuthCleanupRepository(db).protocol_ready():
                raise ValueError("External authentication schema/protocol is incompatible")
            await ExternalIntegrationRepository(db).register_configured(
                [item.integration_id for item in self.settings.integrations]
            )
        await self.crypto.start()
        await self._cleanup_once()
        self.cleanup_task = asyncio.create_task(self._cleanup_loop(), name="external-auth-cleanup")
        self.identities.sessions.external = self.sessions

    @asynccontextmanager
    async def operation(self, name: str) -> AsyncIterator[None]:
        if name not in {"exchange", "revoke", "link", "runtime_bind", "admin", "inference_key"}:
            raise ValueError("Unknown external auth operation")
        started = monotonic()
        outcome = "unavailable"
        try:
            if not self.ready:
                raise ExternalAuthUnavailable()
            async with asyncio.timeout(1):
                yield
            outcome = "success"
        except ExternalAuthError as exc:
            known = {
                "invalid_external_assertion",
                "external_access_denied",
                "external_reauthentication_required",
                "assertion_replayed",
                "account_link_required",
                "identity_binding_conflict",
                "external_auth_rate_limited",
                "external_auth_unavailable",
            }
            external_auth_denials.labels(exc.code if exc.code in known else "invalid_request").inc()
            outcome = "denied" if exc.status_code < 500 else "unavailable"
            raise
        except (PrismaError, TimeoutError, CapacityGateFull, CapacityGateTimedOut) as exc:
            raise ExternalAuthUnavailable() from exc
        finally:
            external_auth_requests.labels(name, outcome).inc()
            external_auth_latency.labels(name).observe(monotonic() - started)

    async def assertion_operation(
        self,
        token: str,
        correlation_id: str,
        *,
        purposes: tuple[ExternalPurpose, ...],
        operation: Callable[[VerifiedExternalAssertion], Awaitable[T]],
    ) -> T:
        try:
            assertion = await self.crypto.verify(token, purposes=purposes, now=int(time()))
        except InvalidExternalAssertion:
            await self.admission.admit_denial_audit()
            async with self.transactions.transaction() as db:
                await self.audit.write(
                    db,
                    ExternalAuditEvent(
                        AuditAction.EXTERNAL_AUTH_DENIAL,
                        "unverified",
                        correlation_id,
                        "denied",
                        reason="invalid_external_assertion",
                    ),
                )
            raise
        await self.admission.admit(assertion)
        await self.exchange.consume(assertion, correlation_id=correlation_id)
        try:
            return await operation(assertion)
        except ExternalAuthError as exc:
            if exc.status_code < 500:
                async with self.transactions.transaction() as db:
                    await self.audit.write(
                        db,
                        ExternalAuditEvent(
                            AuditAction.EXTERNAL_AUTH_DENIAL,
                            assertion.integration_id,
                            correlation_id,
                            "denied",
                            binding_id=assertion.claims.binding_id,
                            purpose=assertion.claims.purpose,
                            reason="external_access_denied",
                        ),
                    )
            raise

    @staticmethod
    def correlation_id() -> str:
        from uuid import uuid4

        return uuid4().hex

    async def check_ready(self) -> bool:
        if not self.ready:
            return False
        try:
            async with asyncio.timeout(1):
                await self.admission.counter.redis.ping()
                async with self.transactions.transaction("validation") as db:
                    compatible = await ExternalAuthCleanupRepository(db).protocol_ready()
            return compatible
        except (
            PrismaError,
            TimeoutError,
            CapacityGateFull,
            CapacityGateTimedOut,
            OSError,
            RedisError,
            ExternalAuthUnavailable,
        ):
            return False

    async def _cleanup_once(self) -> None:
        async with self.transactions.transaction("maintenance") as db:
            repository = ExternalAuthCleanupRepository(db)
            if await repository.claim(self.settings.cleanup_interval_seconds):
                counts = await repository.clean(self.settings.cleanup_batch_size)
                for kind, count in counts.items():
                    external_auth_cleanup.labels(kind).inc(count)
                for kind, (count, oldest) in (
                    await repository.retention_pressure(self.settings.cleanup_batch_size)
                ).items():
                    external_auth_cleanup_backlog.labels(kind).set(count)
                    external_auth_cleanup_oldest.labels(kind).set(oldest)
        self.cleanup_healthy = True

    async def _cleanup_loop(self) -> None:
        while not self.closed:
            await asyncio.sleep(self.settings.cleanup_interval_seconds)
            try:
                await self._cleanup_once()
            except (
                PrismaError,
                CapacityGateFull,
                CapacityGateTimedOut,
                TimeoutError,
                ExternalAuthUnavailable,
            ):
                self.cleanup_healthy = False
                external_auth_cleanup.labels("failure").inc()

    async def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.identities.sessions.external = None
        if self.cleanup_task is not None:
            self.cleanup_task.cancel()
            await asyncio.gather(self.cleanup_task, return_exceptions=True)
        await self.crypto.close()
        await self.db.disconnect()
