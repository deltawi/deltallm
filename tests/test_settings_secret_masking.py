from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from scripts.docs.generate_config_reference import _handling
from src.config import (
    SECRET_SETTING_MASK,
    WRITE_ONLY_GENERAL_SETTINGS,
    AppConfig,
    GeneralSettings,
)

pytestmark = pytest.mark.app

MASTER = "SettingsMaskFixtureMasterKey2026ValidLength"
NEXT_MASTER = "SettingsMaskFixtureNextMasterKey2026Valid"
PLAIN_SECRETS = {
    "master_key": MASTER,
    "salt_key": "private-fixture-salt",
    "database_url": "postgresql://app:private-fixture-db@db:5432/app",
    "redis_password": "private-fixture-redis-password",
    "redis_url": "redis://:private-fixture-redis@redis:6379/0",
    "sso_client_secret": "private-fixture-sso",
    "platform_bootstrap_admin_password": "private-fixture-admin",
    "smtp_password": "private-fixture-smtp",
    "resend_api_key": "private-fixture-resend",
    "resend_webhook_signing_secret": "private-fixture-resend-signing",
    "sendgrid_api_key": "private-fixture-sendgrid",
    "embeddings_batch_s3_access_key_id": "private-fixture-s3-id",
    "embeddings_batch_s3_secret_access_key": "private-fixture-s3-secret",
}
# Secret-typed or startup-only fields the endpoint already masks without the list.
MASKED_BY_TYPE = {"redis_bulk_url", "upstream_proxies"}


def configure(test_app, **general):
    test_app.state.settings.master_key = MASTER
    test_app.state.app_config = AppConfig(general_settings=GeneralSettings(**general))
    update = AsyncMock()
    test_app.state.dynamic_config_manager = SimpleNamespace(update_config=update)
    return update


def test_every_documented_secret_is_write_only():
    for name, field in GeneralSettings.model_fields.items():
        if _handling(name, field.annotation) == "Secret":
            assert name in WRITE_ONLY_GENERAL_SETTINGS | MASKED_BY_TYPE, name


async def test_settings_read_masks_set_secrets_and_nulls_unset(client, test_app):
    configure(test_app, **PLAIN_SECRETS)
    response = await client.get("/ui/api/settings", headers={"X-Master-Key": MASTER})
    assert response.status_code == 200
    general = response.json()["general_settings"]
    for name in PLAIN_SECRETS:
        assert general[name] == SECRET_SETTING_MASK, name
    assert general["slack_webhook_url"] is None
    assert general["mcp_oauth_encryption_key"] is None
    assert general["log_level"] == "INFO"
    assert "private-fixture" not in response.text
    assert MASTER not in response.text


async def test_settings_echo_keeps_every_secret(client, test_app, monkeypatch):
    update = configure(test_app, **PLAIN_SECRETS)
    audit = AsyncMock()
    monkeypatch.setattr("src.api.admin.endpoints.config.emit_admin_mutation_audit", audit)
    read = await client.get("/ui/api/settings", headers={"X-Master-Key": MASTER})
    echoed = {
        name: value
        for name, value in read.json()["general_settings"].items()
        if name in WRITE_ONLY_GENERAL_SETTINGS
    }
    echoed["salt_key"] = ""
    echoed["log_level"] = "WARNING"
    response = await client.put(
        "/ui/api/settings",
        headers={"X-Master-Key": MASTER},
        json={"general_settings": echoed},
    )
    assert response.status_code == 200
    update.assert_awaited_once_with(
        {"general_settings": {"log_level": "WARNING"}}, updated_by="admin_api"
    )
    assert test_app.state.settings.master_key == MASTER
    assert "updated_secret_fields" not in (audit.await_args.kwargs["metadata"] or {})


async def test_settings_write_rotates_secret_without_auditing_it(client, test_app, monkeypatch):
    update = configure(test_app, master_key=MASTER)
    audit = AsyncMock()
    monkeypatch.setattr("src.api.admin.endpoints.config.emit_admin_mutation_audit", audit)
    response = await client.put(
        "/ui/api/settings",
        headers={"X-Master-Key": MASTER},
        json={"general_settings": {"master_key": NEXT_MASTER, "sendgrid_api_key": "private-new"}},
    )
    assert response.status_code == 200
    update.assert_awaited_once_with(
        {"general_settings": {"master_key": NEXT_MASTER, "sendgrid_api_key": "private-new"}},
        updated_by="admin_api",
    )
    assert test_app.state.settings.master_key == NEXT_MASTER
    kwargs = audit.await_args.kwargs
    assert kwargs["request_payload"]["general_settings"] == {
        "master_key": SECRET_SETTING_MASK,
        "sendgrid_api_key": SECRET_SETTING_MASK,
    }
    assert kwargs["metadata"] == {"updated_secret_fields": ["master_key", "sendgrid_api_key"]}
    assert NEXT_MASTER not in repr(kwargs)
    assert "private-new" not in repr(kwargs)
    assert NEXT_MASTER not in response.text
