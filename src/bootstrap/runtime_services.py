from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.bootstrap.status import BootstrapStatus
from src.billing import AlertConfig, AlertService, BudgetEnforcementService, SpendLedgerService, SpendTrackingService
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
from src.services.callable_target_grants import CallableTargetGrantService
from src.services.governance_invalidation import GovernanceInvalidationService
from src.services.key_notifications import KeyNotificationService
from src.services.notification_recipients import NotificationRecipientResolver
from src.services.prompt_registry import PromptRegistryService


@dataclass
class RuntimeServicesRuntime:
    callback_manager: CallbackManager
    governance_invalidation_service: GovernanceInvalidationService
    statuses: tuple[BootstrapStatus, ...] = ()


async def init_runtime_services(app: Any, cfg: Any) -> RuntimeServicesRuntime:
    app.state.callable_target_grant_service = CallableTargetGrantService(
        repository=getattr(app.state, "callable_target_binding_repository", None),
        policy_repository=getattr(app.state, "callable_target_scope_policy_repository", None),
        access_group_repository=getattr(app.state, "callable_target_access_group_repository", None),
        callable_target_catalog_getter=lambda: getattr(app.state, "callable_target_catalog", None),
    )
    await app.state.callable_target_grant_service.reload()
    app.state.prompt_registry_service = PromptRegistryService(
        repository=app.state.prompt_registry_repository,
        route_group_repository=app.state.route_group_repository,
        redis_client=app.state.redis,
    )
    app.state.mcp_registry_service = MCPRegistryService(
        repository=app.state.mcp_repository,
        redis_client=app.state.redis,
    )
    app.state.mcp_governance_service = MCPGovernanceService(
        repository=app.state.mcp_repository,
        policy_repository=getattr(app.state, "mcp_scope_policy_repository", None),
    )
    await app.state.mcp_governance_service.reload()
    app.state.mcp_transport_client = StreamableHTTPMCPClient(app.state.http_client)
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
        mcp_registry_service=app.state.mcp_registry_service,
        mcp_governance_service=app.state.mcp_governance_service,
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

    general_settings = getattr(cfg, "general_settings", None)
    app.state.notification_recipient_resolver = NotificationRecipientResolver(app.state.prisma_manager.client)
    app.state.key_notification_service = KeyNotificationService(
        outbox_service=getattr(app.state, "email_outbox_service", None),
        recipient_resolver=app.state.notification_recipient_resolver,
        audit_service=getattr(app.state, "audit_service", None),
        config_getter=lambda: getattr(app.state, "app_config", cfg),
    )
    app.state.alert_service = AlertService(
        redis_client=app.state.redis,
        outbox_service=getattr(app.state, "email_outbox_service", None),
        recipient_resolver=app.state.notification_recipient_resolver,
        audit_service=getattr(app.state, "audit_service", None),
        config_getter=lambda: getattr(app.state, "app_config", cfg),
        config=AlertConfig(
            budget_alert_ttl=int(getattr(general_settings, "budget_alert_ttl_seconds", 3600) or 3600),
        ),
    )
    app.state.spend_ledger_service = SpendLedgerService(app.state.prisma_manager.client)
    app.state.spend_tracking_service = SpendTrackingService(
        db_client=app.state.prisma_manager.client,
        ledger=app.state.spend_ledger_service,
    )
    app.state.budget_service = BudgetEnforcementService(
        db_client=app.state.prisma_manager.client,
        alert_service=app.state.alert_service,
    )

    return RuntimeServicesRuntime(
        callback_manager=callback_manager,
        governance_invalidation_service=app.state.governance_invalidation_service,
        statuses=(
            BootstrapStatus("callable_target_grants", "ready"),
            BootstrapStatus("prompt_registry", "ready"),
            BootstrapStatus("mcp_runtime", "ready"),
            BootstrapStatus("guardrails", "ready"),
            BootstrapStatus("callbacks", "ready"),
            BootstrapStatus("billing", "ready"),
        ),
    )


async def shutdown_runtime_services(runtime: RuntimeServicesRuntime) -> None:
    await runtime.governance_invalidation_service.close()
    await runtime.callback_manager.shutdown()
