from src.bootstrap.dependency_capacity import DependencyAllocationSnapshot
from src.config import Settings
from src.config_runtime.loader import build_app_config
from tests.test_accounting_native_config import native
from cryptography.hazmat.primitives.asymmetric import rsa
from tests.auth.test_external_assertions import settings_for
import pytest


def test_effective_snapshot_preserves_file_and_environment_precedence():
    settings = Settings(
        database_url="postgresql://fixture:fixture@fixture/db",
        db_pool_size=7,
        telemetry_db_pool_size=3,
        audit_ingestion_mode="outbox",
        redis_critical_max_connections=10,
        redis_cache_max_connections=2,
        db_foreground_pool_size=4,
    )
    file_config = {"general_settings": {"redis_critical_max_connections": 5}}
    initial = build_app_config(file_config)
    snapshot = DependencyAllocationSnapshot.build(initial, settings)
    assert snapshot.control_connections == 7
    assert snapshot.telemetry_connections == 3
    assert snapshot.telemetry_worker_connections == 5
    assert snapshot.database.db_foreground_pool_size == 4
    assert snapshot.redis.critical_max_connections == 5
    assert snapshot.redis.cache_max_connections == 2
    # Adding effective values, unrelated config or an optional cache endpoint
    # does not change the declared capacity. Legacy pool environment pins win.
    effective = build_app_config(
        file_config,
        {
            "general_settings": {
                "db_pool_size": 100,
                "telemetry_db_pool_size": 100,
                "db_foreground_pool_size": 4,
                "redis_cache_max_connections": 2,
                "audit_ingestion_mode": "outbox",
                "log_level": "DEBUG",
                "redis_bulk_url": "redis://fixture:fixture@cache/0",
            }
        },
    )
    snapshot.validate_effective(effective, settings)


def test_switching_enabled_telemetry_owner_does_not_add_pools():
    settings = Settings(database_url="postgresql://fixture:fixture@fixture/db")
    initial = build_app_config({"general_settings": {"audit_ingestion_mode": "outbox"}})
    effective = build_app_config({"general_settings": {"spend_ingestion_mode": "outbox"}})
    DependencyAllocationSnapshot.build(initial, settings).validate_effective(effective, settings)


def test_accounting_api_does_not_allocate_a_worker_pool_without_worker_ownership():
    settings = Settings(database_url="postgresql://fixture:fixture@fixture/db")
    api = build_app_config(
        {
            "general_settings": {
                "accounting_protocol_enabled": True,
                "accounting_projection_worker_enabled": False,
                "spend_ingestion_worker_enabled": False,
                "audit_ingestion_worker_enabled": False,
            }
        }
    )
    worker = build_app_config(
        {
            "general_settings": {
                "accounting_protocol_enabled": True,
                "accounting_projection_worker_enabled": True,
                "spend_ingestion_mode": "outbox",
                "audit_ingestion_mode": "outbox",
            }
        }
    )

    api_snapshot = DependencyAllocationSnapshot.build(api, settings)
    worker_snapshot = DependencyAllocationSnapshot.build(worker, settings)

    assert api_snapshot.telemetry_connections == 5
    assert api_snapshot.telemetry_worker_connections == 0
    assert worker_snapshot.telemetry_connections == 5
    assert worker_snapshot.telemetry_worker_connections == 5


def test_native_api_does_not_allocate_an_unused_assigned_accounting_pool():
    settings = Settings(database_url="postgresql://fixture:fixture@fixture/db")
    initial = build_app_config({"general_settings": native().model_dump(exclude_unset=True)})
    snapshot = DependencyAllocationSnapshot.build(initial, settings)
    assert snapshot.accounting.accounting_execution_mode == "local_journal"
    assert snapshot.telemetry_connections == snapshot.telemetry_worker_connections == 0
    # The native pool belongs only to the isolated request/projection role.
    changed = build_app_config(
        {
            "general_settings": native(accounting_rpc_max_connections=32).model_dump(
                exclude_unset=True
            )
        }
    )
    with pytest.raises(RuntimeError, match="allocation settings"):
        snapshot.validate_effective(changed, settings)


def test_external_auth_pool_is_counted_and_requires_a_restart_to_change():
    external = settings_for(rsa.generate_private_key(public_exponent=65537, key_size=2048))
    settings = Settings(
        database_url="postgresql://fixture:fixture@fixture/db", external_auth=external
    )
    initial = build_app_config({})
    snapshot = DependencyAllocationSnapshot.build(initial, settings)
    assert snapshot.external_auth_connections == 4
    snapshot.validate_effective(initial, settings)

    disabled = build_app_config({"general_settings": {"external_auth": {"enabled": False}}})
    assert DependencyAllocationSnapshot.build(disabled, settings).external_auth_connections == 0
    with pytest.raises(RuntimeError, match="allocation settings"):
        snapshot.validate_effective(disabled, settings)
