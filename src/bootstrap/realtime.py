from __future__ import annotations

import asyncio

from starlette.datastructures import State

from src.billing.spend_ingestion import SpendIngestionService
from src.config import AppConfig
from src.db.realtime_billing import RealtimeBillingRepository
from src.db.realtime_recovery import RealtimeBillingRecovery
from src.realtime.admission import RealtimeAdmissionService
from src.realtime.capacity import RealtimeCapacity
from src.realtime.config import RealtimeSettings
from src.realtime.routing import RealtimeRouting
from src.realtime.runtime import RealtimeRuntime
from src.services.limit_counter import LimitCounter
from src.runtime_settings import resolve_general_setting


async def init_realtime_runtime(state: State, cfg: AppConfig) -> RealtimeRuntime | None:
    """Compose the production owner from existing services; no extra pool or worker."""
    settings = resolve_general_setting(
        cfg.general_settings, state.settings, "realtime", RealtimeSettings()
    )
    state.realtime_settings = settings
    state.realtime_runtime = None
    spend: SpendIngestionService = state.spend_tracking_service
    if spend.durable_ingestion_enabled and spend.db is not None:
        # Keep recovery running when a rollout disables new WebSocket sessions.
        async with asyncio.timeout(2):
            rows = await spend.db.query_raw(
                "SELECT to_regclass('deltallm_realtime_billing_intents')::text AS journal"
            )
        if rows and rows[0].get("journal") is not None:
            spend.realtime_recovery = RealtimeBillingRecovery(
                spend.db,
                max_pending_events=spend.config.max_pending_events,
                max_attempts=spend.config.max_attempts,
            )
    if not settings.enabled:
        return None
    if (
        state.redis is None
        or not spend.durable_ingestion_enabled
        or not spend.worker_health.ready
        or spend.realtime_recovery is None
    ):
        raise RuntimeError(
            "Realtime requires Redis, the migrated durable spend outbox, and a healthy spend worker"
        )
    routing = RealtimeRouting(
        state.routing_runtime_generation_store,
        state.callable_target_grant_service,
        state.tier_policy_service,
        default_transcription_model=settings.default_transcription_model,
        policy_mode=resolve_general_setting(
            cfg.general_settings, state.settings, "callable_target_scope_policy_mode", "enforce"
        ),
        tier_policy_mode=str(state.tier_policy_service.mode),
        tier_missing_service_mode="fail_closed",
    )
    # This facade shares Redis keys/scripts with HTTP, but never degrades locally.
    capacity = RealtimeCapacity(
        LimitCounter(state.redis, degraded_mode="fail_closed"),
        routing,
        settings,
        fair_share_enabled=resolve_general_setting(
            cfg.general_settings, state.settings, "tier_capacity_fair_share_enabled", False
        ),
    )
    admission = RealtimeAdmissionService(
        routing=routing,
        billing=RealtimeBillingRepository(spend.db),
        capacity=capacity,
        keys=state.key_service,
        settings=settings,
        accounting_ready=lambda: spend.worker_health.ready,
    )
    runtime = RealtimeRuntime(admission=admission, limits=settings.transport_limits())
    state.realtime_runtime = runtime
    return runtime
