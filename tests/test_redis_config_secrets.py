from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.config import AppConfig, GeneralSettings

pytestmark = [pytest.mark.app, pytest.mark.asyncio]

BULK_URL = "redis://fixture-user:private-fixture-password@private-fixture-host:6379/0"
MASTER = "RedisConfigFixtureMasterKey2026ValidLength"


def configure(test_app):
    test_app.state.settings.master_key = MASTER
    test_app.state.app_config = AppConfig(
        general_settings=GeneralSettings(master_key=MASTER, redis_bulk_url=BULK_URL)
    )
    update = AsyncMock()
    test_app.state.dynamic_config_manager = SimpleNamespace(update_config=update)
    return update


async def test_settings_response_masks_new_redis_endpoint(client, test_app):
    configure(test_app)
    response = await client.get("/ui/api/settings", headers={"X-Master-Key": MASTER})
    assert response.status_code == 200
    assert response.json()["general_settings"]["redis_bulk_url"] == "**********"
    assert BULK_URL not in response.text
    assert "private-fixture" not in response.text


@pytest.mark.parametrize("value", [None, "", "**********"])
async def test_settings_echo_preserves_existing_redis_secret(client, test_app, monkeypatch, value):
    update = configure(test_app)
    audit = AsyncMock()
    monkeypatch.setattr("src.api.admin.endpoints.config.emit_admin_mutation_audit", audit)
    response = await client.put(
        "/ui/api/settings",
        headers={"X-Master-Key": MASTER},
        json={"general_settings": {"redis_bulk_url": value, "log_level": "INFO"}},
    )
    assert response.status_code == 200
    update.assert_awaited_once_with(
        {"general_settings": {"log_level": "INFO"}}, updated_by="admin_api"
    )
    assert test_app.state.app_config.general_settings.redis_bulk_url.get_secret_value() == BULK_URL
    assert "private-fixture" not in response.text
    assert "private-fixture" not in repr(audit.await_args.kwargs)


async def test_live_secret_write_is_rejected_before_persistence_and_audit(
    client, test_app, monkeypatch
):
    update = configure(test_app)
    audit = AsyncMock()
    monkeypatch.setattr("src.api.admin.endpoints.config.emit_admin_mutation_audit", audit)
    response = await client.put(
        "/ui/api/settings",
        headers={"X-Master-Key": MASTER},
        json={"general_settings": {"redis_bulk_url": BULK_URL}},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "restart_required"
    assert "private-fixture" not in response.text
    update.assert_not_awaited()
    audit.assert_not_awaited()
