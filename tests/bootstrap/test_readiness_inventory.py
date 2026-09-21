import asyncio
from types import SimpleNamespace

import pytest

from src.bootstrap.readiness import collect_workers, worker_inventory
from src.config import AppConfig, GeneralSettings, Settings
from src.telemetry.lifecycle import WorkerHealth, WorkerState


def base(**settings):
    general = GeneralSettings(
        **(
            {
                "audit_enabled": False,
                "organization_deletion_worker_enabled": False,
                "cache_invalidation_worker_enabled": False,
            }
            | settings
        )
    )
    state = SimpleNamespace(
        settings=Settings(),
        governance_invalidation_service=SimpleNamespace(
            worker_health=WorkerHealth(WorkerState.READY)
        ),
        organization_lifecycle_task=SimpleNamespace(done=lambda: False),
        organization_lifecycle_authorizer=SimpleNamespace(is_ready=lambda: True),
        dynamic_config_manager=SimpleNamespace(worker_health=WorkerHealth(WorkerState.READY)),
        model_hot_reload_manager=SimpleNamespace(
            get_applied_routing_state=lambda: SimpleNamespace(requires_reconciliation=False)
        ),
    )
    return state, AppConfig(general_settings=general)


@pytest.mark.parametrize(
    "config,component",
    [
        ({"audit_enabled": True}, "audit_ingestion_worker"),
        ({"audit_enabled": True, "audit_ingestion_mode": "outbox"}, "audit_ingestion_worker"),
        ({"spend_ingestion_mode": "outbox"}, "spend_ingestion_worker"),
        ({"email_enabled": True}, "email_outbox_worker"),
        ({"cache_invalidation_worker_enabled": True}, "cache_invalidation_worker"),
        ({"organization_deletion_worker_enabled": True}, "organization_deletion_worker"),
        (
            {"embeddings_batch_enabled": True, "embeddings_batch_worker_enabled": True},
            "batch_executor",
        ),
    ],
)
def test_configured_worker_cannot_disappear_from_readiness(config, component):
    state, cfg = base(**config)
    required, _ = collect_workers(worker_inventory(state, cfg))
    assert component in required
    assert not required[component].ready
    assert required[component].state == "unavailable"


def test_disabled_optional_workers_are_distinct_from_missing_required_ones():
    state, cfg = base()
    required, optional = collect_workers(worker_inventory(state, cfg))
    assert all(check.ready for check in required.values())
    assert optional["audit_ingestion_worker"].state == "disabled"
    assert optional["email_outbox_worker"].state == "disabled"


def test_worker_must_acknowledge_startup_and_still_be_alive():
    state, cfg = base(cache_invalidation_worker_enabled=True)
    started = asyncio.Event()
    done = [False]
    state.cache_invalidation_worker = SimpleNamespace(started=started)
    state.cache_invalidation_task = SimpleNamespace(done=lambda: done[0])
    inventory = worker_inventory(state, cfg)
    assert collect_workers(inventory)[0]["cache_invalidation_worker"].state == "starting"
    started.set()
    assert collect_workers(inventory)[0]["cache_invalidation_worker"].ready
    done[0] = True
    assert not collect_workers(inventory)[0]["cache_invalidation_worker"].ready
    state.cache_invalidation_worker = None
    done[0] = False
    assert not collect_workers(inventory)[0]["cache_invalidation_worker"].ready


def test_environment_tier_enforcement_requires_its_refresh_worker():
    state, cfg = base()
    state.settings = Settings(
        tier_policy_mode="enforce", tier_policy_missing_service_mode="fail_closed"
    )
    required, _ = collect_workers(worker_inventory(state, cfg))
    assert not required["tier_policy_refresh"].ready
    cfg.general_settings.tier_policy_mode = "disabled"
    required, optional = collect_workers(worker_inventory(state, cfg))
    assert "tier_policy_refresh" not in required
    assert optional["tier_policy_refresh"].state == "disabled"
