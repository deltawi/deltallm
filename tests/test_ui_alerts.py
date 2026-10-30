from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from src.audit.actions import AuditAction
from src.config import AppConfig, GeneralSettings
from src.notifications.channels.destinations import AlertDestinationChannel
from src.notifications.destination_http import AlertDestinationHTTP
from src.outbound.network_policy import OutboundNetworkPolicy

pytestmark = pytest.mark.app

MASTER = "AlertDestinationFixtureMasterKey2026Valid"
HOOK_URL = "https://hooks.example.com/private-hook-path"
SECRET = "private-fixture-signing-secret-0000000"


async def _public(hostname: str, port: int) -> tuple[str, ...]:
    del hostname, port
    return ("93.184.216.34",)


def configure(test_app, status_code: int = 200):  # noqa: ANN001, ANN201
    general = GeneralSettings(
        master_key=MASTER,
        alert_destinations=[
            {
                "name": "ops-webhook",
                "type": "webhook",
                "url": HOOK_URL,
                "signing_secret": SECRET,
                "alert_types": ["budget_threshold"],
            },
            {
                "name": "team-chat",
                "type": "teams",
                "url": HOOK_URL,
                "alert_types": ["*"],
            },
        ],
    )
    test_app.state.settings.master_key = MASTER
    test_app.state.app_config = AppConfig(general_settings=general)
    sent: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(status_code)

    http = AlertDestinationHTTP(
        transport=httpx.MockTransport(handle), policy=OutboundNetworkPolicy(resolver=_public)
    )
    test_app.state.alert_destination_channels = {
        item.name: AlertDestinationChannel(settings=item, http=http)
        for item in general.alert_destinations
    }
    update = AsyncMock()
    test_app.state.dynamic_config_manager = SimpleNamespace(update_config=update)
    return sent, update


async def test_send_test_alert_posts_to_one_destination_and_audits(client, test_app, monkeypatch):
    sent, _ = configure(test_app)
    audit = AsyncMock()
    monkeypatch.setattr("src.api.admin.endpoints.alerts.emit_admin_mutation_audit", audit)

    response = await client.post(
        "/ui/api/alerts/test", headers={"X-Master-Key": MASTER}, json={"destination": "team-chat"}
    )

    assert response.status_code == 200
    assert response.json() == {
        "delivered": True,
        "destination": "team-chat",
        "type": "teams",
        "status_code": 200,
        "error": None,
    }
    assert len(sent) == 1
    assert b"alert delivery test" in sent[0].content
    assert audit.await_args.kwargs["action"] == AuditAction.ADMIN_ALERT_TEST
    assert audit.await_args.kwargs["status"] == "success"


async def test_send_test_alert_reports_failed_delivery(client, test_app, monkeypatch):
    configure(test_app, status_code=403)
    audit = AsyncMock()
    monkeypatch.setattr("src.api.admin.endpoints.alerts.emit_admin_mutation_audit", audit)

    response = await client.post(
        "/ui/api/alerts/test",
        headers={"X-Master-Key": MASTER},
        json={"destination": "ops-webhook"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["delivered"] is False
    assert body["error"] == "http_403"
    assert audit.await_args.kwargs["status"] == "error"


async def test_send_test_alert_unknown_destination_is_404(client, test_app):
    configure(test_app)
    response = await client.post(
        "/ui/api/alerts/test", headers={"X-Master-Key": MASTER}, json={"destination": "nope"}
    )
    assert response.status_code == 404


async def test_list_destinations_hides_urls_and_secrets(client, test_app):
    configure(test_app)
    response = await client.get("/ui/api/alerts/destinations", headers={"X-Master-Key": MASTER})
    assert response.status_code == 200
    assert response.json()["destinations"][0] == {
        "name": "ops-webhook",
        "type": "webhook",
        "alert_types": ["budget_threshold"],
    }
    assert "private-hook-path" not in response.text
    assert SECRET not in response.text


async def test_settings_mask_destinations_and_accept_their_echo(client, test_app, monkeypatch):
    _, update = configure(test_app)
    monkeypatch.setattr("src.api.admin.endpoints.config.emit_admin_mutation_audit", AsyncMock())
    response = await client.get("/ui/api/settings", headers={"X-Master-Key": MASTER})
    destinations = response.json()["general_settings"]["alert_destinations"]
    assert destinations[0]["url"] == "**********"
    assert destinations[0]["signing_secret"] == "**********"
    assert "private-hook-path" not in response.text
    assert SECRET not in response.text

    echoed = await client.put(
        "/ui/api/settings",
        headers={"X-Master-Key": MASTER},
        json={"general_settings": {"alert_destinations": destinations, "log_level": "INFO"}},
    )
    assert echoed.status_code == 200
    update.assert_awaited_once_with(
        {"general_settings": {"log_level": "INFO"}}, updated_by="admin_api"
    )


@pytest.mark.parametrize(
    "value",
    [
        [],
        [
            {
                "name": "evil",
                "type": "slack",
                "url": "https://attacker.example/x",
                "alert_types": ["*"],
            }
        ],
    ],
)
async def test_settings_reject_live_destination_changes(client, test_app, monkeypatch, value):
    _, update = configure(test_app)
    monkeypatch.setattr("src.api.admin.endpoints.config.emit_admin_mutation_audit", AsyncMock())
    response = await client.put(
        "/ui/api/settings",
        headers={"X-Master-Key": MASTER},
        json={"general_settings": {"alert_destinations": value}},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "restart_required"
    update.assert_not_awaited()
