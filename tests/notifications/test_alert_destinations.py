from __future__ import annotations

import hashlib
import hmac
import json

import httpx
import pytest
from pydantic import ValidationError

from src.config import AlertDestinationSettings, GeneralSettings
from src.notifications.channels.destinations import AlertDestinationChannel
from src.notifications.destination_http import AlertDestinationHTTP
from src.notifications.types import NotificationMessage
from src.outbound.network_policy import OutboundNetworkPolicy
from src.services.email.notification_recipients import NotificationRecipients

PUBLIC_ADDRESS = "93.184.216.34"
SECRET = "s" * 32
RECIPIENTS = NotificationRecipients(emails=(), policy="none")


async def _public(hostname: str, port: int) -> tuple[str, ...]:
    del hostname, port
    return (PUBLIC_ADDRESS,)


async def _private(hostname: str, port: int) -> tuple[str, ...]:
    del hostname, port
    return ("10.0.0.7",)


class _Upstream:
    def __init__(self, *responses: httpx.Response) -> None:
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if not self.responses:
            return httpx.Response(204)
        template = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        # A response body can be read once; hand out a fresh copy per request.
        return httpx.Response(template.status_code, headers=template.headers)


def _http(upstream: _Upstream, *, resolver=_public, **policy) -> AlertDestinationHTTP:  # noqa: ANN001
    return AlertDestinationHTTP(
        transport=upstream.transport(),
        policy=OutboundNetworkPolicy(resolver=resolver, resolution_timeout_seconds=1.0, **policy),
    )


def _destination(**overrides) -> AlertDestinationSettings:  # noqa: ANN003
    values = {
        "name": "ops",
        "type": "webhook",
        "url": "https://hooks.example.com/deltallm",
        "signing_secret": SECRET,
        "alert_types": ["*"],
    }
    values.update(overrides)
    return AlertDestinationSettings(**values)


def _budget_message() -> NotificationMessage:
    return NotificationMessage(
        alert_type="budget_threshold",
        metric_kind="budget_threshold",
        payload={
            "instance_name": "DeltaLLM",
            "entity_type": "team",
            "entity_id": "team-1",
            "current_spend": 81.5,
            "soft_budget": 80,
            "hard_budget": 100,
        },
    )


# --- Configuration ---


def test_webhook_destination_requires_long_signing_secret() -> None:
    with pytest.raises(ValidationError, match="signing_secret"):
        _destination(signing_secret=None)
    with pytest.raises(ValidationError, match="signing_secret"):
        _destination(signing_secret="short")


def test_chat_destination_rejects_signing_secret_and_bad_values() -> None:
    with pytest.raises(ValidationError, match="signing_secret"):
        _destination(type="slack")
    with pytest.raises(ValidationError):
        _destination(type="pagerduty", signing_secret=None)
    with pytest.raises(ValidationError):
        _destination(type="teams", signing_secret=None, url="ftp://example.com/hook")
    with pytest.raises(ValidationError):
        _destination(alert_types=["unknown_alert"])
    with pytest.raises(ValidationError):
        _destination(alert_types=[])
    for name in ("email", "slack", "none", "Ops", "-ops", "a" * 33):
        with pytest.raises(ValidationError):
            _destination(name=name)


def test_destination_names_must_be_unique() -> None:
    first = _destination().model_dump()
    first["url"] = "https://hooks.example.com/a"
    first["signing_secret"] = SECRET
    with pytest.raises(ValidationError, match="unique"):
        GeneralSettings(alert_destinations=[first, dict(first)])


def test_destination_secrets_are_masked_in_dumps() -> None:
    settings = GeneralSettings(
        alert_destinations=[
            {
                "name": "ops",
                "type": "webhook",
                "url": "https://hooks.example.com/secret-path",
                "signing_secret": SECRET,
                "alert_types": ["budget_threshold"],
            }
        ]
    )
    dumped = json.dumps(settings.model_dump(mode="json"))
    assert "secret-path" not in dumped
    assert SECRET not in dumped


# --- Routing ---


def test_supports_matches_listed_types_or_wildcard() -> None:
    http = _http(_Upstream())
    budget_only = AlertDestinationChannel(
        settings=_destination(alert_types=["budget_threshold"]), http=http
    )
    everything = AlertDestinationChannel(settings=_destination(), http=http)
    assert budget_only.supports("budget_threshold")
    assert not budget_only.supports("api_key_lifecycle")
    assert everything.supports("api_key_lifecycle")


# --- Rendering ---


@pytest.mark.asyncio
async def test_signed_webhook_event_verifies_with_shared_secret() -> None:
    upstream = _Upstream(httpx.Response(200))
    channel = AlertDestinationChannel(settings=_destination(), http=_http(upstream))

    result = await channel.send(message=_budget_message(), recipients=RECIPIENTS)

    assert result.outcome == "queued"
    assert result.detail == {"destination_type": "webhook", "status_code": 200}
    request = upstream.requests[0]
    assert request.url.host == PUBLIC_ADDRESS
    assert request.headers["host"] == "hooks.example.com"
    body = request.content
    event = json.loads(body)
    assert event["type"] == "budget_threshold"
    assert event["data"]["entity_id"] == "team-1"
    assert event["id"] == request.headers["x-deltallm-event-id"]
    assert request.headers["idempotency-key"] == event["id"]
    timestamp = request.headers["x-deltallm-timestamp"]
    expected = hmac.new(SECRET.encode(), f"{timestamp}.".encode() + body, hashlib.sha256)
    assert request.headers["x-deltallm-signature"] == f"v1={expected.hexdigest()}"


@pytest.mark.parametrize("kind", ["slack", "discord", "teams"])
def test_chat_destinations_render_alert_text(kind: str) -> None:
    channel = AlertDestinationChannel(
        settings=_destination(type=kind, signing_secret=None), http=_http(_Upstream())
    )
    body, headers = channel.render(_budget_message())
    payload = json.loads(body)
    assert headers["Content-Type"] == "application/json"
    assert "x-deltallm-signature" not in {name.lower() for name in headers}
    if kind == "slack":
        assert payload["text"].startswith("*DeltaLLM budget alert: team team-1*")
    elif kind == "discord":
        assert "team-1" in payload["content"]
        assert payload["allowed_mentions"] == {"parse": []}
    else:
        card = payload["attachments"][0]
        assert card["contentType"] == "application/vnd.microsoft.card.adaptive"
        assert "team-1" in card["content"]["body"][0]["text"]


def test_discord_content_is_truncated_to_limit() -> None:
    channel = AlertDestinationChannel(
        settings=_destination(type="discord", signing_secret=None), http=_http(_Upstream())
    )
    message = _budget_message()
    message.payload["entity_id"] = "x" * 5000
    payload = json.loads(channel.render(message)[0])
    assert len(payload["content"]) == 2000


# --- Outbound policy and failures ---


@pytest.mark.asyncio
async def test_private_address_is_denied_without_sending() -> None:
    upstream = _Upstream()
    channel = AlertDestinationChannel(
        settings=_destination(), http=_http(upstream, resolver=_private)
    )
    result = await channel.send(message=_budget_message(), recipients=RECIPIENTS)
    assert result.outcome == "undeliverable"
    assert result.error and result.error.startswith("policy_")
    assert upstream.requests == []


@pytest.mark.asyncio
async def test_allowed_private_cidr_permits_internal_receiver() -> None:
    upstream = _Upstream(httpx.Response(200))
    channel = AlertDestinationChannel(
        settings=_destination(),
        http=_http(upstream, resolver=_private, allowed_private_cidrs=["10.0.0.0/24"]),
    )
    result = await channel.send(message=_budget_message(), recipients=RECIPIENTS)
    assert result.outcome == "queued"


@pytest.mark.asyncio
async def test_http_url_is_denied_unless_allowed() -> None:
    upstream = _Upstream()
    channel = AlertDestinationChannel(
        settings=_destination(url="http://hooks.example.com/deltallm"), http=_http(upstream)
    )
    result = await channel.send(message=_budget_message(), recipients=RECIPIENTS)
    assert result.outcome == "undeliverable"
    assert upstream.requests == []


@pytest.mark.asyncio
async def test_redirect_is_a_failure_and_not_followed() -> None:
    upstream = _Upstream(httpx.Response(302, headers={"Location": "https://elsewhere.example"}))
    channel = AlertDestinationChannel(settings=_destination(), http=_http(upstream))
    result = await channel.send(message=_budget_message(), recipients=RECIPIENTS)
    assert result.outcome == "undeliverable"
    assert result.error == "http_302"
    assert len(upstream.requests) == 1


@pytest.mark.asyncio
async def test_retryable_status_is_retried_once() -> None:
    upstream = _Upstream(httpx.Response(503), httpx.Response(200))
    channel = AlertDestinationChannel(settings=_destination(), http=_http(upstream))
    result = await channel.send(message=_budget_message(), recipients=RECIPIENTS)
    assert result.outcome == "queued"
    assert len(upstream.requests) == 2


@pytest.mark.asyncio
async def test_client_error_is_not_retried() -> None:
    upstream = _Upstream(httpx.Response(400))
    channel = AlertDestinationChannel(settings=_destination(), http=_http(upstream))
    result = await channel.send(message=_budget_message(), recipients=RECIPIENTS)
    assert result.error == "http_400"
    assert len(upstream.requests) == 1


@pytest.mark.asyncio
async def test_transport_error_never_raises() -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    http = AlertDestinationHTTP(
        transport=httpx.MockTransport(fail),
        policy=OutboundNetworkPolicy(resolver=_public),
    )
    channel = AlertDestinationChannel(settings=_destination(), http=http)
    result = await channel.send(message=_budget_message(), recipients=RECIPIENTS)
    assert result.outcome == "undeliverable"
    assert result.error == "transport_error"
