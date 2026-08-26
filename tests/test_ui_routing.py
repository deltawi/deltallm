from __future__ import annotations

import pytest


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
