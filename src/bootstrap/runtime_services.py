from __future__ import annotations

from dataclasses import dataclass
import logging
import os
import socket
from typing import Any

from src.bootstrap.status import BootstrapStatus
from src.billing import (
    AlertConfig,
    AlertService,
    BudgetEnforcementService,
    SpendLedgerService,
    SpendIngestionConfig,
    SpendIngestionService,
    SpendTrackingService,
)
from src.callbacks import CallbackManager
from src.guardrails.middleware import GuardrailMiddleware
from src.guardrails.registry import GuardrailRegistry
from src.mcp import (
    MCPApprovalService,
    MCPGatewayService,
    MCPGovernanceService,
    MCPHealthProbe,
    MCPRegistryService,
    MCPToolPolicyEnforcer,
    MCPToolResultCache,
    StreamableHTTPMCPClient,
)
from src.notifications.channels import EmailChannel, SlackChannel
from src.notifications.dispatcher import NotificationDispatcher
from src.notifications.types import NotificationChannel
from src.notifications.webhook import close_shared_client
from src.services.callable_target_grants import CallableTargetGrantService
from src.services.routing_authorization import RoutingAuthorizationReconciler
from src.router.runtime_generation import (
    RoutingRuntimeGenerationStore,
    with_authorization_snapshot,
)
from src.services.governance_invalidation import GovernanceInvalidationService
from src.services.key_notifications import KeyNotificationService
from src.services.notification_recipients import NotificationRecipientResolver
from src.services.prompt_registry import PromptRegistryService
from src.services.tier_policy_service import TierPolicyService

logger = logging.getLogger(__name__)


@dataclass
class RuntimeServicesRuntime:
    callback_manager: CallbackManager
    governance_invalidation_service: GovernanceInvalidationService
    tier_policy_service: Any | None = None
    spend_ingestion_service: SpendIngestionService | None = None
    prompt_registry_service: PromptRegistryService | None = None
    statuses: tuple[BootstrapStatus, ...] = ()


_MISSING = object()


def _runtime_setting(
    general_settings: Any,
    settings: Any,
    field_name: str,
    default: Any,
) -> Any:
    value = _explicit_general_setting(general_settings, field_name)
    if value is not _MISSING:
        return value
    return getattr(settings, field_name, default)


def _explicit_general_setting(general_settings: Any, field_name: str) -> Any:
    if general_settings is None:
        return _MISSING
    fields_set = getattr(general_settings, "model_fields_set", None)
    if fields_set is not None and field_name not in fields_set:
        return _MISSING
    return getattr(general_settings, field_name, _MISSING)


async def init_runtime_services(app: Any, cfg: Any) -> RuntimeServicesRuntime:
    app.state.callable_target_grant_service = CallableTargetGrantService(
        repository=getattr(app.state, "callable_target_binding_repository", None),
        policy_repository=getattr(app.state, "callable_target_scope_policy_repository", None),
        access_group_repository=getattr(app.state, "callable_target_access_group_repository", None),
        callable_target_catalog_getter=lambda: getattr(app.state, "callable_target_catalog", None),
    )
    await app.state.callable_target_grant_service.reload()
    generation_store = getattr(app.state, "routing_runtime_generation_store", None)
    if isinstance(generation_store, RoutingRuntimeGenerationStore):
        generation_store.replace(
            with_authorization_snapshot(
                generation_store.require_snapshot(),
                app.state.callable_target_grant_service.snapshot(),
            )
        )
    app.state.routing_authorization_reconciler = RoutingAuthorizationReconciler(
        db=getattr(getattr(app.state, "prisma_manager", None), "client", None),
        callable_target_bindings=getattr(
            app.state,
            "callable_target_binding_repository",
            None,
        ),
        route_groups=getattr(app.state, "route_group_repository", None),
        callable_target_grants=app.state.callable_target_grant_service,
    )
    model_hot_reload_manager = getattr(app.state, "model_hot_reload_manager", None)
    if model_hot_reload_manager is not None:
        model_hot_reload_manager.set_routing_authorization_reconciler(
            app.state.routing_authorization_reconciler
        )
    general_settings = getattr(cfg, "general_settings", None)
    settings = getattr(app.state, "settings", None)
    tier_policy_mode = str(
        _runtime_setting(general_settings, settings, "tier_policy_mode", "disabled") or "disabled"
    )
    tier_policy_missing_service_mode = str(
        _runtime_setting(
            general_settings,
            settings,
            "tier_policy_missing_service_mode",
            "fail_open",
        )
        or "fail_open"
    )
    app.state.tier_policy_service = TierPolicyService(
        repository=getattr(app.state, "tier_repository", None),
        mode=tier_policy_mode,
        missing_service_mode=tier_policy_missing_service_mode,
        refresh_interval_seconds=_runtime_setting(
            general_settings,
            settings,
            "tier_policy_refresh_interval_seconds",
            300.0,
        ),
        refresh_jitter_seconds=_runtime_setting(
            general_settings,
            settings,
            "tier_policy_refresh_jitter_seconds",
            1.0,
        ),
        transition_grace_seconds=_runtime_setting(
            general_settings,
            settings,
            "tier_policy_transition_grace_seconds",
            0.05,
        ),
        refresh_retry_delay_seconds=_runtime_setting(
            general_settings,
            settings,
            "tier_policy_refresh_retry_delay_seconds",
            5.0,
        ),
    )
    resolved_tier_policy_mode = str(
        getattr(app.state.tier_policy_service, "mode", tier_policy_mode) or "disabled"
    )
    resolved_tier_policy_missing_service_mode = str(
        getattr(
            app.state.tier_policy_service,
            "missing_service_mode",
            tier_policy_missing_service_mode,
        )
        or "fail_open"
    )
    tier_policy_status = "disabled"
    if resolved_tier_policy_mode != "disabled":
        try:
            await app.state.tier_policy_service.reload()
        except Exception:
            if resolved_tier_policy_missing_service_mode != "fail_open":
                raise
            tier_policy_status = "degraded"
            logger.exception(
                "tier policy startup reload failed; continuing because fail_open is configured"
            )
        else:
            tier_policy_status = "ready"
    prompt_registry_service = PromptRegistryService(
        repository=app.state.prompt_registry_repository,
        route_group_repository=app.state.route_group_repository,
        redis_client=app.state.redis,
        render_log_sink=getattr(app.state, "audit_service", None),
        l1_ttl_seconds=_runtime_setting(
            general_settings, settings, "prompt_cache_l1_ttl_seconds", 30
        ),
        l2_ttl_seconds=_runtime_setting(
            general_settings, settings, "prompt_cache_l2_ttl_seconds", 300
        ),
        negative_cache_enabled=_runtime_setting(
            general_settings,
            settings,
            "prompt_negative_cache_enabled",
            False,
        ),
        negative_l1_ttl_seconds=_runtime_setting(
            general_settings,
            settings,
            "prompt_negative_l1_ttl_seconds",
            5,
        ),
        negative_l2_ttl_seconds=_runtime_setting(
            general_settings,
            settings,
            "prompt_negative_l2_ttl_seconds",
            30,
        ),
        l1_max_entries=_runtime_setting(
            general_settings,
            settings,
            "prompt_cache_l1_max_entries",
            10_000,
        ),
        singleflight_max_keys=_runtime_setting(
            general_settings,
            settings,
            "prompt_singleflight_max_keys",
            256,
        ),
        singleflight_timeout_seconds=_runtime_setting(
            general_settings,
            settings,
            "prompt_singleflight_timeout_seconds",
            2.0,
        ),
    )
    app.state.prompt_registry_service = prompt_registry_service
    app.state.mcp_registry_service = MCPRegistryService(
        repository=app.state.mcp_repository,
        redis_client=app.state.redis,
    )
    app.state.mcp_governance_service = MCPGovernanceService(
        repository=app.state.mcp_repository,
        policy_repository=getattr(app.state, "mcp_scope_policy_repository", None),
    )
    await app.state.mcp_governance_service.reload()
    app.state.mcp_transport_client = StreamableHTTPMCPClient(
        app.state.http_client,
        general_settings=getattr(app.state, "upstream_http_settings", cfg.general_settings),
    )
    app.state.mcp_health_probe = MCPHealthProbe(
        registry=app.state.mcp_registry_service,
        client=app.state.mcp_transport_client,
    )
    app.state.mcp_gateway_service = MCPGatewayService(
        registry=app.state.mcp_registry_service,
        governance_service=app.state.mcp_governance_service,
        transport_client=app.state.mcp_transport_client,
        policy_enforcer=MCPToolPolicyEnforcer(app.state.limit_counter),
        result_cache=MCPToolResultCache(getattr(app.state, "cache_backend", None)),
        approval_service=MCPApprovalService(app.state.mcp_repository),
    )
    app.state.governance_invalidation_service = GovernanceInvalidationService(
        redis_client=app.state.redis,
        callable_target_grant_service=app.state.callable_target_grant_service,
        tier_policy_service=(
            app.state.tier_policy_service if resolved_tier_policy_mode != "disabled" else None
        ),
        mcp_registry_service=app.state.mcp_registry_service,
        mcp_governance_service=app.state.mcp_governance_service,
        prompt_registry_service=app.state.prompt_registry_service,
        route_group_reload=getattr(
            getattr(app.state, "model_hot_reload_manager", None),
            "reload_route_groups",
            None,
        ),
        route_group_revision_source=getattr(app.state, "route_group_repository", None),
        route_group_applied_revision=getattr(
            getattr(app.state, "model_hot_reload_manager", None),
            "get_applied_route_revision",
            None,
        ),
        routing_applied_state=getattr(
            getattr(app.state, "model_hot_reload_manager", None),
            "get_applied_routing_state",
            None,
        ),
    )
    await app.state.governance_invalidation_service.start()

    guardrail_registry = GuardrailRegistry()
    if cfg.deltallm_settings.guardrails:
        guardrail_registry.load_from_config(cfg.deltallm_settings.guardrails)
    app.state.guardrail_registry = guardrail_registry
    app.state.guardrail_middleware = GuardrailMiddleware(
        registry=guardrail_registry,
        cache_backend=app.state.redis,
    )

    callback_manager = CallbackManager()
    callback_manager.load_from_settings(
        success_callbacks=cfg.deltallm_settings.success_callback,
        failure_callbacks=cfg.deltallm_settings.failure_callback,
        callbacks=cfg.deltallm_settings.callbacks,
        callback_settings=cfg.deltallm_settings.callback_settings,
    )
    app.state.callback_manager = callback_manager
    app.state.turn_off_message_logging = cfg.deltallm_settings.turn_off_message_logging

    app.state.notification_recipient_resolver = NotificationRecipientResolver(
        app.state.prisma_manager.client
    )
    budget_alert_ttl = int(getattr(general_settings, "budget_alert_ttl_seconds", 3600) or 3600)

    channels: list[NotificationChannel] = []
    email_outbox_service = getattr(app.state, "email_outbox_service", None)
    if email_outbox_service is not None:
        channels.append(EmailChannel(outbox_service=email_outbox_service))
    if getattr(general_settings, "slack_alerting_enabled", False) and getattr(
        general_settings, "slack_webhook_url", None
    ):
        channels.append(
            SlackChannel(
                webhook_url=general_settings.slack_webhook_url,
                allowed_alert_types=set(getattr(general_settings, "slack_alert_kinds", []) or []),
            )
        )

    notification_dispatcher = NotificationDispatcher(
        channels=channels,
        redis_client=app.state.redis,
        audit_service=getattr(app.state, "audit_service", None),
        dedupe_ttl_seconds=budget_alert_ttl,
    )
    app.state.notification_dispatcher = notification_dispatcher
    app.state.key_notification_service = KeyNotificationService(
        dispatcher=notification_dispatcher,
        recipient_resolver=app.state.notification_recipient_resolver,
        config_getter=lambda: getattr(app.state, "app_config", cfg),
    )
    app.state.alert_service = AlertService(
        dispatcher=notification_dispatcher,
        recipient_resolver=app.state.notification_recipient_resolver,
        config_getter=lambda: getattr(app.state, "app_config", cfg),
        config=AlertConfig(budget_alert_ttl=budget_alert_ttl),
    )
    spend_ingestion_mode = str(
        getattr(app.state, "spend_ingestion_mode", None)
        or _runtime_setting(general_settings, settings, "spend_ingestion_mode", "legacy")
        or "legacy"
    )
    telemetry_db_client = getattr(
        getattr(app.state, "telemetry_prisma_manager", None),
        "client",
        None,
    )
    if spend_ingestion_mode == "outbox" and telemetry_db_client is None:
        raise RuntimeError("spend outbox mode requires the dedicated telemetry database pool")
    spend_db_client = (
        telemetry_db_client if spend_ingestion_mode == "outbox" else app.state.prisma_manager.client
    )
    app.state.spend_ledger_service = SpendLedgerService(spend_db_client)
    spend_writer = SpendTrackingService(
        db_client=spend_db_client,
        ledger=app.state.spend_ledger_service,
    )
    spend_ingestion_service = SpendIngestionService(
        db_client=spend_db_client,
        writer=spend_writer,
        config=SpendIngestionConfig(
            enabled=spend_ingestion_mode == "outbox",
            batch_size=int(
                _runtime_setting(general_settings, settings, "spend_ingestion_batch_size", 100)
            ),
            flush_interval_seconds=(
                float(
                    _runtime_setting(
                        general_settings, settings, "spend_ingestion_flush_interval_ms", 100
                    )
                )
                / 1000.0
            ),
            lease_seconds=int(
                _runtime_setting(general_settings, settings, "spend_ingestion_lease_seconds", 30)
            ),
            max_attempts=int(
                _runtime_setting(general_settings, settings, "spend_ingestion_max_attempts", 10)
            ),
            worker_enabled=bool(
                _runtime_setting(general_settings, settings, "spend_ingestion_worker_enabled", True)
            ),
            max_pending_events=int(
                _runtime_setting(
                    general_settings, settings, "spend_ingestion_max_pending_events", 100_000
                )
            ),
            overload_policy=str(
                _runtime_setting(
                    general_settings, settings, "spend_ingestion_overload_policy", "sync_fallback"
                )
            ),
            fallback_max_concurrency=int(
                _runtime_setting(
                    general_settings, settings, "spend_ingestion_fallback_max_concurrency", 1
                )
            ),
            fallback_max_waiters=int(
                _runtime_setting(
                    general_settings, settings, "spend_ingestion_fallback_max_waiters", 8
                )
            ),
            fallback_queue_timeout_seconds=(
                float(
                    _runtime_setting(
                        general_settings,
                        settings,
                        "spend_ingestion_fallback_queue_timeout_ms",
                        100,
                    )
                )
                / 1000.0
            ),
            fallback_execution_timeout_seconds=float(
                _runtime_setting(
                    general_settings,
                    settings,
                    "spend_ingestion_fallback_execution_timeout_seconds",
                    2.0,
                )
            ),
            completed_retention_hours=int(
                _runtime_setting(
                    general_settings, settings, "spend_ingestion_completed_retention_hours", 1
                )
            ),
            failed_retention_days=int(
                _runtime_setting(
                    general_settings, settings, "spend_ingestion_failed_retention_days", 30
                )
            ),
            cleanup_interval_seconds=float(
                _runtime_setting(
                    general_settings, settings, "spend_ingestion_cleanup_interval_seconds", 60.0
                )
            ),
            cleanup_batch_size=int(
                _runtime_setting(
                    general_settings, settings, "spend_ingestion_cleanup_batch_size", 1000
                )
            ),
            cleanup_max_batches_per_run=int(
                _runtime_setting(
                    general_settings,
                    settings,
                    "spend_ingestion_cleanup_max_batches_per_run",
                    10,
                )
            ),
            cleanup_time_budget_seconds=float(
                _runtime_setting(
                    general_settings,
                    settings,
                    "spend_ingestion_cleanup_time_budget_seconds",
                    2.0,
                )
            ),
            worker_startup_timeout_seconds=float(
                _runtime_setting(
                    general_settings,
                    settings,
                    "telemetry_worker_startup_timeout_seconds",
                    5.0,
                )
            ),
            shutdown_drain_timeout_seconds=float(
                _runtime_setting(
                    general_settings, settings, "telemetry_shutdown_drain_timeout_seconds", 20.0
                )
            ),
            worker_id=f"{socket.gethostname()}:{os.getpid()}:spend",
        ),
    )
    await spend_ingestion_service.start()
    app.state.spend_tracking_service = spend_ingestion_service
    app.state.budget_service = BudgetEnforcementService(
        db_client=app.state.prisma_manager.client,
        alert_service=app.state.alert_service,
        query_mode=_runtime_setting(
            general_settings,
            settings,
            "budget_enforcement_query_mode",
            "legacy",
        ),
        shadow_sample_rate=_runtime_setting(
            general_settings,
            settings,
            "budget_enforcement_shadow_sample_rate",
            0.01,
        ),
        query_timeout_seconds=_runtime_setting(
            general_settings,
            settings,
            "budget_enforcement_query_timeout_seconds",
            2.0,
        ),
    )

    if resolved_tier_policy_mode != "disabled":
        start_tier_policy_service = getattr(app.state.tier_policy_service, "start", None)
        if callable(start_tier_policy_service):
            await start_tier_policy_service()

    return RuntimeServicesRuntime(
        callback_manager=callback_manager,
        governance_invalidation_service=app.state.governance_invalidation_service,
        tier_policy_service=app.state.tier_policy_service,
        spend_ingestion_service=spend_ingestion_service,
        prompt_registry_service=prompt_registry_service,
        statuses=(
            BootstrapStatus("callable_target_grants", "ready"),
            BootstrapStatus("tier_policy", tier_policy_status),
            BootstrapStatus("prompt_registry", "ready"),
            BootstrapStatus("mcp_runtime", "ready"),
            BootstrapStatus("guardrails", "ready"),
            BootstrapStatus("callbacks", "ready"),
            BootstrapStatus("billing", "ready"),
        ),
    )


async def shutdown_runtime_services(runtime: RuntimeServicesRuntime) -> None:
    if runtime.spend_ingestion_service is not None:
        await runtime.spend_ingestion_service.shutdown()
    prompt_shutdown = getattr(runtime.prompt_registry_service, "shutdown", None)
    if callable(prompt_shutdown):
        await prompt_shutdown()
    tier_policy_service = runtime.tier_policy_service
    if tier_policy_service is not None and callable(getattr(tier_policy_service, "close", None)):
        await tier_policy_service.close()
    await runtime.governance_invalidation_service.close()
    await runtime.callback_manager.shutdown()
    await close_shared_client()
