from types import SimpleNamespace

import pytest

from src.bootstrap.asset_readiness import AUTHORIZATION_OWNERS
from src.bootstrap.readiness import collect_workers, worker_inventory
from src.config import GeneralSettings
from src.services.access.managed_asset_reconciliation import ManagedAssetReconciliationHealth
from tests.bootstrap.test_readiness_inventory import base


@pytest.mark.parametrize("missing", ["managed_asset_reconciliation_service", *AUTHORIZATION_OWNERS])
def test_current_main_asset_owners_cannot_disappear_from_readiness(missing):
    state, cfg = base()
    delattr(state, missing)
    required, _ = collect_workers(worker_inventory(state, cfg))
    name = (
        "managed_asset_links"
        if missing.startswith("managed_asset")
        else "creator_asset_authorization"
    )
    assert not required[name].ready
    assert required[name].state == "unavailable"


def test_creator_authorization_freshness_is_not_cached_with_dependency_results():
    state, cfg = base()
    inventory = worker_inventory(state, cfg)
    assert collect_workers(inventory)[0]["creator_asset_authorization"].ready
    state.creator_prompt_access_service.authorization_ready = lambda: False
    assert collect_workers(inventory)[0]["creator_asset_authorization"].state == "stale"
    state.creator_prompt_access_service.authorization_ready = lambda: True
    assert collect_workers(inventory)[0]["creator_asset_authorization"].ready


def test_asset_counts_are_preserved_without_exposing_exception_messages():
    state, cfg = base()
    state.managed_asset_reconciliation_service.health_snapshot = lambda: (
        ManagedAssetReconciliationHealth(
            state="failed",
            ready=False,
            missing_links=2,
            last_repaired=3,
            detail="private database endpoint and credential",
        )
    )
    check = collect_workers(worker_inventory(state, cfg))[0]["managed_asset_links"]
    assert not check.ready
    payload = check.details.payload()
    assert payload["missing_links"] == "2"
    assert payload["last_repaired"] == "3"
    assert payload["detail"] == "managed-asset link check failed"
    assert "private" not in str(payload)


@pytest.mark.parametrize("installed,ready", [(False, False), (True, False), (True, True)])
def test_enabled_realtime_remains_a_required_readiness_check(installed, ready):
    state, cfg = base()
    cfg.general_settings.realtime = GeneralSettings(realtime={"enabled": True}).realtime
    if installed:
        state.realtime_runtime = SimpleNamespace(ready=ready)
    check = collect_workers(worker_inventory(state, cfg))[0]["realtime"]
    assert check.ready is ready


def test_disabled_realtime_does_not_withdraw_a_ready_http_process():
    state, cfg = base()
    required, _ = collect_workers(worker_inventory(state, cfg))
    assert "realtime" not in required
    assert all(check.ready for check in required.values())
