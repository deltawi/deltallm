from __future__ import annotations

import pytest
from pydantic import SecretStr

from src.notifications.channels import slack as slack_module
from src.notifications.channels.slack import SlackChannel
from src.notifications.types import NotificationMessage
from src.notifications.webhook import WebhookResult
from src.services.notification_recipients import NotificationRecipients


def _budget_message() -> NotificationMessage:
    return NotificationMessage(
        alert_type="budget_threshold",
        metric_kind="budget_threshold",
        payload={
            "instance_name": "DeltaLLM",
            "entity_type": "team",
            "entity_id": "team-1",
            "current_spend": 12.0,
            "soft_budget": 10.0,
            "hard_budget": 20.0,
        },
    )


def test_supports_respects_allowlist() -> None:
    channel = SlackChannel(webhook_url=SecretStr("https://x"), allowed_alert_types={"budget_threshold"})
    assert channel.supports("budget_threshold") is True
    assert channel.supports("api_key_lifecycle") is False


@pytest.mark.asyncio
async def test_send_posts_rendered_text(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    async def fake_post(*, url, json_body, **kwargs):  # noqa: ANN001, ANN003
        captured["json_body"] = json_body
        return WebhookResult(ok=True, status_code=200)

    monkeypatch.setattr(slack_module, "post_webhook", fake_post)
    channel = SlackChannel(webhook_url=SecretStr("https://x"), allowed_alert_types={"budget_threshold"})

    result = await channel.send(
        message=_budget_message(),
        recipients=NotificationRecipients(emails=(), policy="none"),
    )

    assert result.outcome == "queued"
    text = captured["json_body"]["text"]  # type: ignore[index]
    assert "budget alert" in text
    assert "team-1" in text


@pytest.mark.asyncio
async def test_send_reports_undeliverable_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_post(*, url, json_body, **kwargs):  # noqa: ANN001, ANN003
        return WebhookResult(ok=False, status_code=500, error="http_500")

    monkeypatch.setattr(slack_module, "post_webhook", fake_post)
    channel = SlackChannel(webhook_url=SecretStr("https://x"), allowed_alert_types={"budget_threshold"})

    result = await channel.send(
        message=_budget_message(),
        recipients=NotificationRecipients(emails=(), policy="none"),
    )

    assert result.outcome == "undeliverable"
    assert result.error == "http_500"
