from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4
import logging

from src.billing.realtime_charge import RealtimeAttribution, RealtimeChargeContext
from src.billing.realtime_usage import RealtimeDurationUsage, realtime_usage_receipt
from src.metrics.realtime import record_receipt
from src.metrics import increment_router_health_update_failure
from src.providers.openai_realtime import successful_realtime_terminal
from src.router.candidates import AttemptPermit
from src.db.realtime_billing import RealtimeBillingRepository
from src.realtime.capacity import RealtimeCapacity, RealtimeLeases
from src.realtime.config import RealtimeSettings
from src.realtime.contracts import AdmittedRealtime, RealtimeRequest, TextSocket
from src.realtime.controls import prepare_session, validate_controls
from src.realtime.errors import RealtimeError
from src.realtime.routing import RealtimeRoute, RealtimeRouting
from src.services.key_service import KeyService
from src.services.runtime_scopes import resolve_runtime_scope_context

logger = logging.getLogger(__name__)


class RealtimeAdmissionService:
    def __init__(
        self,
        *,
        routing: RealtimeRouting,
        billing: RealtimeBillingRepository,
        capacity: RealtimeCapacity,
        keys: KeyService,
        settings: RealtimeSettings,
        accounting_ready: Callable[[], bool],
    ) -> None:
        self.routing, self.billing, self.capacity = routing, billing, capacity
        self.keys, self.settings = keys, settings
        self.accounting_ready = accounting_ready

    @property
    def ready(self) -> bool:
        return self.accounting_ready()

    def require_ready(self) -> None:
        if not self.ready:
            raise RealtimeError(
                "accounting_unavailable", "Realtime accounting is unavailable", close_code=1013
            )

    @asynccontextmanager
    async def admit(self, request: RealtimeRequest) -> AsyncIterator[AdmittedRealtime]:
        self.require_ready()
        if resolve_runtime_scope_context(request.auth).auth_source != "api_key":
            raise RealtimeError("unsupported_auth", "Realtime requires a stored API key")
        self.check_guardrails(request)
        route = await self.routing.resolve(request)
        auth = request.auth
        context = RealtimeChargeContext(
            RealtimeAttribution(
                request.session_id,
                auth.api_key,
                auth.user_id,
                auth.team_id,
                auth.organization_id,
                auth.owner_account_id,
                route.target.public_model,
                route.deployment.deployment_id,
                route.target.upstream_model,
            ),
            route.customer_prices,
            route.provider_prices,
            datetime.now(UTC),
        )
        await self.billing.check_owner(context)
        leases = await self.capacity.acquire(auth, route)
        permit = RealtimeSessionPermit(self, request, route, context, leases)
        try:
            yield AdmittedRealtime(route.target, permit)
        finally:
            try:
                try:
                    await permit.close()
                finally:
                    await self.billing.close(request.session_id)
            finally:
                await leases.close()

    def check_guardrails(self, request: RealtimeRequest) -> None:
        config = self.routing.generations.require_snapshot().app_config
        if config.deltallm_settings.guardrails or request.auth.guardrails:
            raise RealtimeError(
                "guardrail_profile_unsupported",
                "Configured guardrails do not support Realtime audio",
            )


class RealtimeSessionPermit:
    def __init__(
        self,
        owner: RealtimeAdmissionService,
        request: RealtimeRequest,
        route: RealtimeRoute,
        context: RealtimeChargeContext,
        leases: RealtimeLeases,
    ) -> None:
        self.owner, self.request, self.route = owner, request, route
        self.context, self.leases = context, leases
        self.current: str | None = None
        self.committed = False
        self.turns = 0
        self.receipts: dict[str, str] = {}
        self.provider_permit: AttemptPermit | None = None
        self.expires_at = context.started_at + timedelta(
            seconds=owner.settings.session_seconds + 30
        )

    async def prepare_upstream(self, upstream: TextSocket) -> TextSocket:
        return await prepare_session(
            upstream,
            target=self.route.target,
            limits=self.owner.settings.transport_limits(),
            max_output_tokens=self.owner.settings.max_output_tokens,
        )

    def authorize_client_event(self, event: Mapping[str, object]) -> None:
        self.owner.require_ready()
        validate_controls(
            event,
            profile=self.request.profile,
            max_output_tokens=self.owner.settings.max_output_tokens,
        )
        if self.request.profile == "transcription":
            if self.committed and event.get("type") in {
                "input_audio_buffer.append",
                "input_audio_buffer.commit",
            }:
                raise RealtimeError(
                    "turn_in_progress", "Wait for transcription completion before the next turn"
                )
            if self.current is not None and event.get("type") == "input_audio_buffer.clear":
                raise RealtimeError(
                    "turn_in_progress", "Commit the active transcription before clearing the buffer"
                )

    async def before_client_event(self, event: Mapping[str, object]) -> None:
        kind = event.get("type")
        transcription = self.request.profile == "transcription"
        starts = (
            (kind == "input_audio_buffer.append" and self.current is None)
            if transcription
            else kind == "response.create"
        )
        if starts:
            if self.current is not None or self.turns >= self.owner.settings.max_turns:
                raise RealtimeError("turn_limit_exceeded", "Realtime turn limit reached")
            await self.check_health()
            provider = await self.leases.turn(self.request.auth)
            try:
                operation_id = str(uuid4())
                await self.owner.billing.dispatch(
                    operation_id, self.context, expires_at=self.expires_at
                )
                self.current = operation_id
                self.turns += 1
                self.provider_permit = provider
            except BaseException:
                await self.route.generation.router.state.release_attempt(provider)
                raise
        if transcription and kind == "input_audio_buffer.commit":
            if self.current is None:
                raise RealtimeError(
                    "empty_audio_buffer", "Append audio before committing transcription"
                )
            self.committed = True

    async def accept_usage(self, event: Mapping[str, object]) -> None:
        receipt = realtime_usage_receipt(self.request.session_id, event)
        expected = "transcription" if self.request.profile == "transcription" else "response"
        if receipt.operation != expected:
            raise RealtimeError(
                "unexpected_usage",
                "Upstream usage does not match the admitted profile",
                close_code=1011,
            )
        operation = self.receipts.get(receipt.receipt_id) or self.current
        if operation is None:
            raise RealtimeError(
                "unexpected_usage", "Upstream usage has no admitted turn", close_code=1011
            )
        if receipt.usage is not None and isinstance(receipt.usage, RealtimeDurationUsage) != (
            self.route.usage_type == "duration"
        ):
            receipt = replace(receipt, usage=None, pending_reason="usage_profile_changed")
        await self.owner.billing.accept(operation, self.context, receipt)
        record_receipt(pending=receipt.pending_reason is not None)
        self.receipts[receipt.receipt_id] = operation
        if operation == self.current:
            await self._complete_turn(event)
            self.current, self.committed = None, False
        if receipt.pending_reason:
            raise RealtimeError(
                "usage_pending", "Realtime usage requires reconciliation", close_code=1011
            )

    async def _complete_turn(self, event: Mapping[str, object]) -> None:
        permit = self.provider_permit
        if permit is None or not permit.recovery or not successful_realtime_terminal(event):
            await self.close()
            return
        try:
            await self.route.generation.cooldown_manager.complete_recovery_attempt(permit)
        except Exception:
            # The receipt is already durable. Retain the permit for idempotent
            # cleanup and never replay provider work after this failure.
            increment_router_health_update_failure()
            logger.warning("Realtime recovery completion failed")
            raise
        self.provider_permit = None

    async def check_health(self) -> None:
        self.owner.require_ready()
        auth = await self.owner.keys.get_auth_by_token_hash(self.request.auth.api_key)
        self.request = replace(self.request, auth=auth)
        self.owner.check_guardrails(self.request)
        generation = self.owner.routing.authorize(self.request, self.route.target.public_model)
        if generation is not self.route.generation:
            model = self.route.target.public_model
            old_group = self.route.generation.router.resolve_model_group(model)
            new_group = generation.router.resolve_model_group(model)
            current = generation.deployment_registry.physical_deployments.get(
                self.route.deployment.deployment_id
            )
            if (
                current is None
                or new_group != old_group
                or generation.routing_fingerprints.get(new_group)
                != self.route.generation.routing_fingerprints.get(old_group)
                or current.health_ref != self.route.deployment.health_ref
                or current.deltallm_params != self.route.deployment.deltallm_params
                or current.model_info.get("mode") != "realtime"
                or current.model_info.get("realtime_profile", "realtime") != self.request.profile
                or current.model_info.get("realtime_usage_type", "tokens") != self.route.usage_type
                or any(
                    getattr(current, field) != getattr(self.route.deployment, field)
                    for field in (
                        "rpm_limit",
                        "tpm_limit",
                        "audio_seconds_pm_limit",
                        "char_pm_limit",
                    )
                )
            ):
                raise RealtimeError(
                    "deployment_changed", "Reconnect to use the current Realtime deployment"
                )
        self.owner.capacity.require_supported(auth, self.route)
        await self.owner.billing.check_owner(self.context)
        if self.owner.capacity.parallel_signature(auth, self.route) != self.leases.signature:
            raise RealtimeError(
                "capacity_policy_changed", "Reconnect to use the current Realtime capacity policy"
            )
        await self.leases.refresh()

    async def close(self) -> None:
        if self.provider_permit is not None:
            await self.route.generation.router.state.release_attempt(self.provider_permit)
            self.provider_permit = None
