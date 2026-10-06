"""Bind the finite dependency/worker inventory once, after role configuration."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from fastapi import FastAPI
from starlette.datastructures import State

from src.bootstrap.asset_readiness import (
    asset_link_check,
    creator_authorization_check,
    realtime_check,
)
from src.config import AppConfig, GeneralSettings
from src.realtime.config import RealtimeSettings
from src.process_lifecycle import ProcessLifecycle
from src.readiness import Checks, HealthCheck, Probe, ReadinessRuntime
from src.telemetry.lifecycle import WorkerState
from src.redis_runtime import startup_setting


@dataclass(frozen=True)
class WorkerCheck:
    name: str
    required: bool
    read: Callable[[], HealthCheck]


class StartedWorker(Protocol):
    started: asyncio.Event


class FreshWorker(StartedWorker, Protocol):
    def is_ready(self) -> bool: ...


def dependency_probes(state: State) -> dict[str, Probe]:
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
        databases["telemetry_database"] = "telemetry_prisma_manager"
    if getattr(state, "accounting_protocol_enabled", False):
        if getattr(state, "accounting_execution_mode", "assigned") == "assigned":
            databases["telemetry_database"] = "telemetry_prisma_manager"

        async def accounting() -> object:
            service = getattr(state, "accounting_protocol_service", None)
            return False if service is None else await service.readiness_probe()

        probes["accounting_database"] = accounting
    if getattr(state, "telemetry_worker_database_required", False):
        databases["telemetry_worker_database"] = "telemetry_worker_prisma_manager"
    if getattr(state, "spend_operation_intents_enabled", False):
        databases["telemetry_settlement_database"] = "telemetry_settlement_prisma_manager"
    for name, manager_name in databases.items():

        async def database(attribute: str = manager_name) -> object:
            manager = getattr(state, attribute, None)
            return False if manager is None else await manager.readiness_probe()

        probes[name] = database
    return probes


def service_check(
    state: State, attribute: str, *, health_attribute: str = "worker_health", disabled: bool = False
) -> HealthCheck:
    health = getattr(getattr(state, attribute, None), health_attribute, None)
    if health is None:
        return HealthCheck(disabled, "disabled" if disabled else "unavailable")
    value = str(health.state)
    if value not in {member.value for member in WorkerState}:
        return HealthCheck(False, "failed")
    return HealthCheck(bool(health.ready) and (disabled or value != "disabled"), value)


def task_check(
    task: asyncio.Future[object] | None, *, worker: StartedWorker | None = None
) -> HealthCheck:
    if task is None or worker is None:
        return HealthCheck(False, "unavailable")
    if task.done():
        return HealthCheck(False, "failed")
    if not worker.started.is_set():
        return HealthCheck(False, "starting")
    return HealthCheck(True, "ready")


def fresh_task_check(
    task: asyncio.Future[object] | None, *, worker: FreshWorker | None = None
) -> HealthCheck:
    result = task_check(task, worker=worker)
    if result.ready and worker is not None and not worker.is_ready():
        return HealthCheck(False, "stale")
    return result


def collect_workers(inventory: tuple[WorkerCheck, ...]) -> tuple[Checks, Checks]:
    required, optional = {}, {}
    for check in inventory:
        try:
            result = check.read()
        except Exception:
            result = HealthCheck(False, "unavailable")
        (required if check.required else optional)[check.name] = result
    return required, optional


def _service_inventory(state: State, general: GeneralSettings) -> tuple[WorkerCheck, ...]:
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
        (
            "accounting_protocol",
            "accounting_protocol_service",
            bool(getattr(state, "accounting_protocol_enabled", False)),
        ),
        (
            "accounting_projection_worker",
            "accounting_projection_worker",
            bool(
                startup_setting(
                    general,
                    state.settings,
                    "accounting_projection_worker_enabled",
                    False,
                )
            ),
        ),
        (
            "accounting_native_runtime",
            "accounting_remote_owner",
            startup_setting(general, state.settings, "accounting_execution_mode", "assigned")
            == "local_journal",
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
    return tuple(inventory)


def _policy_inventory(state: State, general: GeneralSettings) -> tuple[WorkerCheck, ...]:
    tier_mode = startup_setting(general, state.settings, "tier_policy_mode", "disabled")
    tier_fail_closed = (
        startup_setting(general, state.settings, "tier_policy_missing_service_mode", "fail_open")
        == "fail_closed"
    )
    inventory: list[WorkerCheck] = []
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
            WorkerCheck("managed_asset_links", True, lambda: asset_link_check(state)),
            WorkerCheck(
                "creator_asset_authorization", True, lambda: creator_authorization_check(state)
            ),
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
    return tuple(inventory)


def worker_inventory(state: State, cfg: AppConfig) -> tuple[WorkerCheck, ...]:
    general = cfg.general_settings
    inventory = [*_service_inventory(state, general), *_policy_inventory(state, general)]
    realtime = startup_setting(general, state.settings, "realtime", RealtimeSettings())
    if realtime.enabled:
        inventory.append(WorkerCheck("realtime", True, lambda: realtime_check(state)))
    task_specs = (
        (
            "organization_lifecycle_refresher",
            "organization_lifecycle_task",
            "organization_lifecycle_authorizer",
            True,
            fresh_task_check,
        ),
        (
            "organization_deletion_worker",
            "organization_deletion_task",
            "organization_deletion_worker",
            general.organization_deletion_worker_enabled,
            fresh_task_check,
        ),
        (
            "cache_invalidation_worker",
            "cache_invalidation_task",
            "cache_invalidation_worker",
            general.cache_invalidation_worker_enabled,
            task_check,
        ),
    )
    for name, task, worker, enabled, check in task_specs:
        if enabled:
            inventory.append(
                WorkerCheck(
                    name,
                    True,
                    lambda task=task, worker=worker, check=check: check(
                        getattr(state, task, None), worker=getattr(state, worker, None)
                    ),
                )
            )
    if general.embeddings_batch_enabled:
        inventory.extend(_batch_checks(state, general))
    return tuple(inventory)


def _routing_check(state: State) -> HealthCheck:
    manager = getattr(state, "model_hot_reload_manager", None)
    if manager is None:
        return HealthCheck(False, "unavailable")
    ready = not manager.get_applied_routing_state().requires_reconciliation
    return HealthCheck(ready, "ready" if ready else "stale")


def _batch_checks(state: State, general: GeneralSettings) -> list[WorkerCheck]:
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


def initialize_readiness(
    app: FastAPI, cfg: AppConfig, lifecycle: ProcessLifecycle
) -> ReadinessRuntime:
    inventory = worker_inventory(app.state, cfg)
    runtime = ReadinessRuntime(
        lifecycle=lifecycle,
        probes=dependency_probes(app.state),
        workers=lambda: collect_workers(inventory),
        max_readers=app.state.ingress_runtime.limits.health_max_active,
    )
    app.state.readiness_runtime = runtime
    return runtime
