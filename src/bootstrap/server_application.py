"""Select a fixed process graph before importing the full inference application."""

import os
import socket
from uuid import uuid4

from fastapi import FastAPI

from src.bootstrap.accounting_config import (
    read_accounting_settings,
    validate_legacy_accounting_writers,
)
from src.bootstrap.accounting_role_config import validate_accounting_role
from src.config import resolve_database_settings
from src.db.runtime.allocation_config import resolve_allocation_settings
from src.deployment_capacity_settings import resolve_capacity_settings
from src.process_lifecycle import ProcessLifecycle
from src.startup_config import StartupConfig


def create_server_application(*, startup: StartupConfig, lifecycle: ProcessLifecycle) -> FastAPI:
    general, settings = startup.app_config.general_settings, startup.settings
    config = read_accounting_settings(general, settings)
    role = resolve_capacity_settings(general, settings).deployment_capacity_role
    validate_accounting_role(config, role)
    if config.accounting_execution_mode == "local_journal" and role in {
        "accountingRequest",
        "accountingWorker",
    }:
        from src.bootstrap.accounting_worker_app import create_accounting_worker_app
        from src.bootstrap.capacity_contract import DeploymentCapacityContract

        database = resolve_database_settings(startup.app_config, settings)
        if database is None:
            raise RuntimeError("Native accounting role requires an explicit database URL")
        allocation = resolve_allocation_settings(general, settings)
        contract = DeploymentCapacityContract.load(startup.app_config, settings)
        if contract is not None:
            contract.validate_minimal(
                startup.app_config, settings, database=config.accounting_hot_path_db_pool_size
            )
        secret = config.accounting_rpc_signing_secret
        return create_accounting_worker_app(
            config=config,
            database=database,
            lifecycle=lifecycle,
            role="request" if role == "accountingRequest" else "projection",
            owner_id=accounting_owner_id(),
            signing_secret=None if secret is None else secret.get_secret_value(),
            verify_migrations=startup.lifecycle.migration_mode == "external",
            pool_size=config.accounting_hot_path_db_pool_size,
            acquisition_seconds=allocation.db_acquisition_timeout_seconds,
            lock_seconds=min(
                allocation.db_lock_timeout_seconds, config.accounting_statement_timeout_ms / 1000
            ),
            startup_seconds=min(30, startup.lifecycle.migration_verify_timeout_seconds + 5),
        )
    validate_legacy_accounting_writers(config, general=general, settings=settings)

    from src.main import create_app

    return create_app(startup=startup, lifecycle=lifecycle)


def accounting_owner_id() -> str:
    # A restarted process must not inherit a prior local grant owner's identity.
    return f"{socket.gethostname()[:128]}:{os.getpid()}:{uuid4()}"
