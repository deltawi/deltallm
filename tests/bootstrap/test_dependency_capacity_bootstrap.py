from src.bootstrap.dependency_capacity import DependencyAllocationSnapshot
from src.config import Settings
from src.config_runtime.loader import build_app_config


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
