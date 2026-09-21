"""Bind the finite dependency/worker inventory once, after role configuration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from src.config import AppConfig
from src.process_lifecycle import ProcessLifecycle
from src.readiness import Checks, HealthCheck, Probe, ReadinessRuntime
from src.telemetry.lifecycle import WorkerState
from src.redis_runtime import startup_setting


@dataclass(frozen=True)
class WorkerCheck:
    name: str
    required: bool
    read: Callable[[], HealthCheck]


def dependency_probes(state: Any) -> dict[str, Probe]:
    async def redis() -> object:
        client = getattr(state, "redis", None)
        return False if client is None else await client.ping()

    probes: dict[str, Probe] = {"redis": redis}
    databases = {
        "database": "prisma_manager",
        "foreground_database": "foreground_prisma_manager",
    }
    if "outbox" in {
        getattr(state, "spend_ingestion_mode", "legacy"),
        getattr(state, "audit_ingestion_mode", "legacy"),
    }:
        databases.update(
            telemetry_database="telemetry_prisma_manager",
            telemetry_worker_database="telemetry_worker_prisma_manager",
        )
    if getattr(state, "spend_operation_intents_enabled", False):
        databases["telemetry_settlement_database"] = "telemetry_settlement_prisma_manager"
    for name, manager_name in databases.items():

        async def database(attribute: str = manager_name) -> object:
            client = getattr(getattr(state, attribute, None), "client", None)
            return False if client is None else await client.query_raw("SELECT 1")

        probes[name] = database
    return probes


def service_check(
    state: Any, attribute: str, *, health_attribute: str = "worker_health", disabled: bool = False
) -> HealthCheck:
    health = getattr(getattr(state, attribute, None), health_attribute, None)
    if health is None:
        return HealthCheck(disabled, "disabled" if disabled else "unavailable")
    value = str(health.state)
    if value not in {member.value for member in WorkerState}:
        return HealthCheck(False, "failed")
    return HealthCheck(bool(health.ready) and (disabled or value != "disabled"), value)


def task_check(task: object, *, worker: object = None) -> HealthCheck:
    if task is None or worker is None:
        return HealthCheck(False, "unavailable")
    if task.done():
        return HealthCheck(False, "failed")
    if worker is not None:
        started = getattr(worker, "started", None)
        if started is not None and not started.is_set():
            return HealthCheck(False, "starting")
        is_ready = getattr(worker, "is_ready", None)
        if is_ready is not None and not is_ready():
            return HealthCheck(False, "stale")
    return HealthCheck(True, "ready")


def collect_workers(inventory: tuple[WorkerCheck, ...]) -> tuple[Checks, Checks]:
    required, optional = {}, {}
    for check in inventory:
        try:
            result = check.read()
        except Exception:
            result = HealthCheck(False, "unavailable")
        (required if check.required else optional)[check.name] = result
    return required, optional


def worker_inventory(state: Any, cfg: AppConfig) -> tuple[WorkerCheck, ...]:
    general = cfg.general_settings
    tier_mode = startup_setting(general, state.settings, "tier_policy_mode", "disabled")
    tier_fail_closed = (
        startup_setting(general, state.settings, "tier_policy_missing_service_mode", "fail_open")
        == "fail_closed"
    )
    services = (
        (
            "spend_ingestion_worker",
            "spend_tracking_service",
            bool(
                startup_setting(general, state.settings, "spend_ingestion_mode", "legacy")
                == "outbox"
                and startup_setting(general, state.settings, "spend_ingestion_worker_enabled", True)
            ),
        ),
        (
            "audit_ingestion_worker",
            "audit_service",
            bool(
                general.audit_enabled
                and (
                    startup_setting(general, state.settings, "audit_ingestion_mode", "legacy")
                    != "outbox"
                    or startup_setting(
                        general, state.settings, "audit_ingestion_worker_enabled", True
                    )
                )
            ),
        ),
        (
            "email_outbox_worker",
            "email_outbox_worker",
            bool(general.email_enabled and general.email_worker_enabled),
        ),
    )
    inventory = [
        WorkerCheck(
            name,
            required,
            lambda attr=attr, required=required: service_check(state, attr, disabled=not required),
        )
        for name, attr, required in services
    ]
    inventory.extend(
        (
            WorkerCheck(
                "audit_policy_listener",
                False,
                lambda: service_check(
                    state, "audit_service", health_attribute="policy_listener_health", disabled=True
                ),
            ),
            WorkerCheck(
                "budget_notification_worker",
                False,
                lambda: service_check(state, "budget_notification_worker", disabled=True),
            ),
            WorkerCheck("routing_runtime", True, lambda: _routing_check(state)),
            WorkerCheck(
                "governance_refresh",
                True,
                lambda: service_check(state, "governance_invalidation_service"),
            ),
            WorkerCheck(
                "tier_policy_refresh",
                tier_mode == "enforce" and tier_fail_closed,
                lambda: service_check(
                    state, "tier_policy_service", disabled=tier_mode == "disabled"
                ),
            ),
            WorkerCheck(
                "config_refresh", True, lambda: service_check(state, "dynamic_config_manager")
            ),
        )
    )
    task_specs = (
        (
            "organization_lifecycle_refresher",
            "organization_lifecycle_task",
            "organization_lifecycle_authorizer",
            True,
        ),
        (
            "organization_deletion_worker",
            "organization_deletion_task",
            "organization_deletion_worker",
            general.organization_deletion_worker_enabled,
        ),
        (
            "cache_invalidation_worker",
            "cache_invalidation_task",
            "cache_invalidation_worker",
            general.cache_invalidation_worker_enabled,
        ),
    )
    for name, task, worker, enabled in task_specs:
        if enabled:
            inventory.append(
                WorkerCheck(
                    name,
                    True,
                    lambda task=task, worker=worker: task_check(
                        getattr(state, task, None), worker=getattr(state, worker, None)
                    ),
                )
            )
    if general.embeddings_batch_enabled:
        inventory.extend(_batch_checks(state, general))
    return tuple(inventory)


def _routing_check(state: Any) -> HealthCheck:
    manager = getattr(state, "model_hot_reload_manager", None)
    if manager is None:
        return HealthCheck(False, "unavailable")
    ready = not manager.get_applied_routing_state().requires_reconciliation
    return HealthCheck(ready, "ready" if ready else "stale")


def _batch_checks(state: Any, general: Any) -> list[WorkerCheck]:
    specs = (
        ("batch_executor", "worker", "worker_task", general.embeddings_batch_worker_enabled),
        (
            "batch_completion_outbox",
            "completion_outbox_worker",
            "completion_outbox_task",
            general.embeddings_batch_completion_outbox_worker_enabled,
        ),
        (
            "batch_webhook_worker",
            "webhook_outbox_worker",
            "webhook_outbox_task",
            general.batch_webhook_worker_enabled and bool(general.batch_webhook_encryption_key),
        ),
        (
            "batch_stale_lease_sweeper",
            "stale_lease_sweeper_worker",
            "stale_lease_sweeper_task",
            general.embeddings_batch_stale_lease_sweeper_enabled,
        ),
        (
            "batch_create_session_cleanup",
            "create_session_cleanup_worker",
            "create_session_cleanup_task",
            general.embeddings_batch_create_session_cleanup_enabled,
        ),
    )
    checks = []
    for name, worker, task, enabled in specs:
        if enabled:
            checks.append(
                WorkerCheck(
                    name,
                    True,
                    lambda task=task, worker=worker: task_check(
                        getattr(getattr(state, "batch_runtime", None), task, None),
                        worker=getattr(getattr(state, "batch_runtime", None), worker, None),
                    ),
                )
            )
    return checks


def initialize_readiness(app: Any, cfg: AppConfig, lifecycle: ProcessLifecycle) -> ReadinessRuntime:
    inventory = worker_inventory(app.state, cfg)
    runtime = ReadinessRuntime(
        lifecycle=lifecycle,
        probes=dependency_probes(app.state),
        workers=lambda: collect_workers(inventory),
        max_readers=app.state.ingress_runtime.limits.health_max_active,
    )
    app.state.readiness_runtime = runtime
    return runtime
