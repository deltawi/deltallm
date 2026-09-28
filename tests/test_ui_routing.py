from __future__ import annotations

import pytest

from src.config import AppConfig
from src.config_runtime.loader import deep_merge


@pytest.mark.asyncio
async def test_routing_capabilities_do_not_offer_deprecated_tag_alias(client, test_app) -> None:
    setattr(test_app.state.settings, "master_key", "mk-test")
    setattr(test_app.state.app_config.router_settings, "routing_strategy", "tag-based-routing")

    response = await client.get(
        "/ui/api/routing",
        headers={"Authorization": "Bearer mk-test"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["strategy"] == "tag-based-routing"
    assert "tag-based-routing" not in payload["available_strategies"]
    assert "weighted" in payload["available_strategies"]


@pytest.mark.asyncio
async def test_settings_fallback_update_reaches_dynamic_config(
    client, test_app, monkeypatch: pytest.MonkeyPatch
) -> None:
    master_key = "SettingsMasterKey2026SecureValue123"
    setattr(test_app.state.settings, "master_key", master_key)
    test_app.state.app_config = AppConfig.model_validate(
        {"general_settings": {"master_key": master_key}}
    )

    class RecordingDynamicConfigManager:
        def __init__(self) -> None:
            self.updates: list[tuple[dict, str]] = []

        async def update_config(self, update: dict, updated_by: str) -> None:
            self.updates.append((update, updated_by))
            current = test_app.state.app_config.model_dump(mode="python")
            test_app.state.app_config = AppConfig.model_validate(deep_merge(current, update))

    manager = RecordingDynamicConfigManager()
    test_app.state.dynamic_config_manager = manager

    async def ignore_audit(**_kwargs) -> None:  # noqa: ANN003
        return None

    monkeypatch.setattr("src.api.admin.endpoints.config.emit_admin_mutation_audit", ignore_audit)
    fallbacks = [{"gpt-4o-mini": ["gpt-4o-mini-fallback"]}]

    response = await client.put(
        "/ui/api/settings",
        headers={"Authorization": f"Bearer {master_key}"},
        json={"deltallm_settings": {"fallbacks": fallbacks}},
    )

    assert response.status_code == 200
    assert manager.updates == [({"deltallm_settings": {"fallbacks": fallbacks}}, "admin_api")]
    assert response.json()["deltallm_settings"]["fallbacks"] == fallbacks
