from __future__ import annotations

from email.message import EmailMessage
from types import SimpleNamespace

import httpx
import pytest

from src.email.models import EmailConfigurationError, EmailDeliveryError, PreparedEmail
from src.email.providers import ResendEmailProvider, SMTPEmailProvider, SendGridEmailProvider
from src.services.email_delivery_service import EmailDeliveryService


def _config(**overrides):
    general_settings = SimpleNamespace(
        email_enabled=True,
        email_provider="smtp",
        email_from_address="noreply@example.com",
        email_from_name="DeltaLLM",
        email_reply_to=None,
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_username="mailer",
        smtp_password="secret",
        smtp_use_tls=False,
        smtp_use_starttls=True,
        resend_api_key="re_test",
        sendgrid_api_key="sg_test",
    )
    for key, value in overrides.items():
        setattr(general_settings, key, value)
    return SimpleNamespace(general_settings=general_settings)


def _delivery_service(**overrides) -> EmailDeliveryService:
    return EmailDeliveryService(config_getter=lambda: _config(**overrides), http_client=httpx.AsyncClient())


@pytest.mark.asyncio
async def test_prepare_template_email_allows_empty_cc_and_bcc() -> None:
    service = _delivery_service()

    message = service.prepare_template_email(
        template_key="test_email",
        to_addresses=("user@example.com",),
        payload_json={"instance_name": "DeltaLLM"},
    )

    assert message.to_addresses == ("user@example.com",)
    assert message.cc_addresses == ()
    assert message.bcc_addresses == ()
    await service._http_client.aclose()


def test_validate_current_config_requires_sender_address() -> None:
    service = _delivery_service(email_from_address=None)

    with pytest.raises(EmailConfigurationError, match="email_from_address"):
        service.validate_current_config()


@pytest.mark.asyncio
async def test_smtp_provider_uses_starttls_and_login(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[tuple[str, object | None]] = []

    class FakeSMTP:
        def __init__(self, host: str, port: int, timeout: float) -> None:
            events.append(("init", (host, port, timeout)))

        def ehlo(self) -> None:
            events.append(("ehlo", None))

        def starttls(self) -> None:
            events.append(("starttls", None))

        def login(self, username: str, password: str) -> None:
            events.append(("login", (username, password)))

        def send_message(self, mime: EmailMessage, to_addrs: list[str]) -> None:
            events.append(("send", (mime["From"], tuple(to_addrs), mime["Subject"])))

        def quit(self) -> None:
            events.append(("quit", None))

    monkeypatch.setattr("src.email.providers.smtplib.SMTP", FakeSMTP)

    provider = SMTPEmailProvider(
        {
            "host": "smtp.example.com",
            "port": 587,
            "username": "mailer",
            "password": "secret",
            "use_tls": False,
            "use_starttls": True,
        }
    )
    result = await provider.send(
        PreparedEmail(
            kind="test",
            provider="smtp",
            to_addresses=("user@example.com",),
            from_address="DeltaLLM <noreply@example.com>",
            subject="subject",
            text_body="body",
        )
    )

    assert result.provider == "smtp"
    assert any(name == "starttls" for name, _ in events)
    assert ("login", ("mailer", "secret")) in events
    assert ("send", ("DeltaLLM <noreply@example.com>", ("user@example.com",), "subject")) in events


@pytest.mark.asyncio
async def test_resend_provider_returns_provider_message_id() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = dict(request.headers)
        captured["body"] = request.content.decode("utf-8")
        return httpx.Response(200, json={"id": "re_msg_123"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        provider = ResendEmailProvider(client, {"api_key": "re_test", "base_url": "https://api.resend.com"})
        result = await provider.send(
            PreparedEmail(
                kind="test",
                provider="resend",
                to_addresses=("user@example.com",),
                from_address="DeltaLLM <noreply@example.com>",
                subject="subject",
                text_body="body",
            )
        )

    assert result.provider_message_id == "re_msg_123"
    assert captured["headers"]["authorization"] == "Bearer re_test"
    assert '"from":"DeltaLLM <noreply@example.com>"' in str(captured["body"])


@pytest.mark.asyncio
async def test_sendgrid_provider_splits_display_name_from_address() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = dict(request.headers)
        captured["body"] = request.content.decode("utf-8")
        return httpx.Response(202, headers={"X-Message-Id": "sg_msg_123"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        provider = SendGridEmailProvider(client, {"api_key": "sg_test", "base_url": "https://api.sendgrid.com"})
        result = await provider.send(
            PreparedEmail(
                kind="test",
                provider="sendgrid",
                to_addresses=("user@example.com",),
                from_address="DeltaLLM <noreply@example.com>",
                subject="subject",
                text_body="body",
                reply_to="support@example.com",
            )
        )

    assert result.provider_message_id == "sg_msg_123"
    assert captured["headers"]["authorization"] == "Bearer sg_test"
    assert '"email":"noreply@example.com"' in str(captured["body"])
    assert '"name":"DeltaLLM"' in str(captured["body"])


@pytest.mark.asyncio
async def test_resend_provider_marks_rate_limit_errors_retriable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(429, text="slow down")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        provider = ResendEmailProvider(client, {"api_key": "re_test", "base_url": "https://api.resend.com"})
        with pytest.raises(EmailDeliveryError) as exc_info:
            await provider.send(
                PreparedEmail(
                    kind="test",
                    provider="resend",
                    to_addresses=("user@example.com",),
                    from_address="DeltaLLM <noreply@example.com>",
                    subject="subject",
                    text_body="body",
                )
            )

    assert exc_info.value.retriable is True
    assert exc_info.value.status_code == 429


@pytest.mark.asyncio
async def test_sendgrid_provider_marks_bad_request_non_retriable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(400, text="bad request")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        provider = SendGridEmailProvider(client, {"api_key": "sg_test", "base_url": "https://api.sendgrid.com"})
        with pytest.raises(EmailDeliveryError) as exc_info:
            await provider.send(
                PreparedEmail(
                    kind="test",
                    provider="sendgrid",
                    to_addresses=("user@example.com",),
                    from_address="DeltaLLM <noreply@example.com>",
                    subject="subject",
                    text_body="body",
                )
            )

    assert exc_info.value.retriable is False
    assert exc_info.value.status_code == 400
