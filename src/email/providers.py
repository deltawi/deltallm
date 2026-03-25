from __future__ import annotations

import asyncio
import contextlib
from email.message import EmailMessage
from email.utils import make_msgid, parseaddr
import smtplib
from typing import Any

import httpx

from src.email.models import EmailDeliveryError, EmailDeliveryResult, PreparedEmail


def _all_recipients(message: PreparedEmail) -> list[str]:
    return [*message.to_addresses, *message.cc_addresses, *message.bcc_addresses]


def _build_mime_message(message: PreparedEmail) -> EmailMessage:
    mime = EmailMessage()
    mime["From"] = message.from_address
    mime["To"] = ", ".join(message.to_addresses)
    if message.cc_addresses:
        mime["Cc"] = ", ".join(message.cc_addresses)
    if message.reply_to:
        mime["Reply-To"] = message.reply_to
    mime["Subject"] = message.subject
    mime["Message-ID"] = make_msgid()
    mime.set_content(message.text_body)
    if message.html_body:
        mime.add_alternative(message.html_body, subtype="html")
    return mime


def _split_mailbox(value: str) -> tuple[str | None, str]:
    display_name, address = parseaddr(value)
    normalized_address = address.strip() or value.strip()
    normalized_name = display_name.strip() or None
    return normalized_name, normalized_address


def _classify_http_error(*, provider: str, response: httpx.Response) -> EmailDeliveryError:
    status_code = int(response.status_code)
    retriable = status_code >= 500 or status_code in {408, 409, 425, 429}
    detail = response.text.strip()
    message = f"{provider} delivery failed with status {status_code}"
    if detail:
        message = f"{message}: {detail[:200]}"
    return EmailDeliveryError(
        message,
        retriable=retriable,
        provider=provider,  # type: ignore[arg-type]
        status_code=status_code,
    )


def _classify_smtp_error(exc: Exception) -> EmailDeliveryError:
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return EmailDeliveryError("SMTP authentication failed", retriable=False, provider="smtp")
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        return EmailDeliveryError("SMTP recipients refused", retriable=False, provider="smtp")
    if isinstance(exc, smtplib.SMTPSenderRefused):
        return EmailDeliveryError("SMTP sender refused", retriable=False, provider="smtp")
    if isinstance(exc, smtplib.SMTPNotSupportedError):
        return EmailDeliveryError("SMTP operation not supported", retriable=False, provider="smtp")
    if isinstance(exc, smtplib.SMTPResponseException):
        status_code = int(getattr(exc, "smtp_code", 0) or 0) or None
        retriable = bool(status_code and 400 <= status_code < 500)
        error_message = getattr(exc, "smtp_error", b"")
        detail = error_message.decode("utf-8", errors="ignore").strip() if isinstance(error_message, bytes) else str(error_message).strip()
        message = "SMTP response error"
        if detail:
            message = f"{message}: {detail[:200]}"
        return EmailDeliveryError(message, retriable=retriable, provider="smtp", status_code=status_code)
    if isinstance(exc, (smtplib.SMTPConnectError, smtplib.SMTPServerDisconnected, TimeoutError, OSError)):
        return EmailDeliveryError(str(exc) or "SMTP transport error", retriable=True, provider="smtp")
    return EmailDeliveryError(str(exc) or "SMTP delivery error", retriable=True, provider="smtp")


class SMTPEmailProvider:
    def __init__(self, config: dict[str, Any]) -> None:
        self.host = str(config.get("host") or "")
        self.port = int(config.get("port") or 587)
        self.username = str(config.get("username") or "") or None
        self.password = str(config.get("password") or "") or None
        self.use_tls = bool(config.get("use_tls", False))
        self.use_starttls = bool(config.get("use_starttls", True))
        self.timeout = float(config.get("timeout") or 20.0)

    async def send(self, message: PreparedEmail) -> EmailDeliveryResult:
        mime = _build_mime_message(message)
        recipients = _all_recipients(message)
        await asyncio.to_thread(self._send_sync, mime, recipients)
        return EmailDeliveryResult(provider="smtp", provider_message_id=str(mime.get("Message-ID") or ""))

    def _send_sync(self, mime: EmailMessage, recipients: list[str]) -> None:
        smtp: smtplib.SMTP | None = None
        try:
            if self.use_tls:
                smtp = smtplib.SMTP_SSL(self.host, self.port, timeout=self.timeout)
            else:
                smtp = smtplib.SMTP(self.host, self.port, timeout=self.timeout)
            smtp.ehlo()
            if self.use_starttls and not self.use_tls:
                smtp.starttls()
                smtp.ehlo()
            if self.username:
                smtp.login(self.username, self.password or "")
            smtp.send_message(mime, to_addrs=recipients)
        except Exception as exc:
            raise _classify_smtp_error(exc) from exc
        finally:
            if smtp is not None:
                with contextlib.suppress(Exception):
                    smtp.quit()


class ResendEmailProvider:
    def __init__(self, http_client: httpx.AsyncClient, config: dict[str, Any]) -> None:
        self.http_client = http_client
        self.api_key = str(config.get("api_key") or "")
        self.base_url = str(config.get("base_url") or "https://api.resend.com")

    async def send(self, message: PreparedEmail) -> EmailDeliveryResult:
        try:
            response = await self.http_client.post(
                f"{self.base_url.rstrip('/')}/emails",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "from": message.from_address,
                    "to": list(message.to_addresses),
                    "cc": list(message.cc_addresses),
                    "bcc": list(message.bcc_addresses),
                    "reply_to": message.reply_to,
                    "subject": message.subject,
                    "text": message.text_body,
                    "html": message.html_body,
                },
                timeout=20,
            )
        except httpx.TransportError as exc:
            raise EmailDeliveryError(str(exc) or "Resend transport error", retriable=True, provider="resend") from exc
        if response.status_code >= 400:
            raise _classify_http_error(provider="resend", response=response)
        payload = response.json() if response.content else {}
        return EmailDeliveryResult(provider="resend", provider_message_id=str(payload.get("id") or ""))


class SendGridEmailProvider:
    def __init__(self, http_client: httpx.AsyncClient, config: dict[str, Any]) -> None:
        self.http_client = http_client
        self.api_key = str(config.get("api_key") or "")
        self.base_url = str(config.get("base_url") or "https://api.sendgrid.com")

    async def send(self, message: PreparedEmail) -> EmailDeliveryResult:
        from_name, from_address = _split_mailbox(message.from_address)
        try:
            response = await self.http_client.post(
                f"{self.base_url.rstrip('/')}/v3/mail/send",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "from": {
                        "email": from_address,
                        **({"name": from_name} if from_name else {}),
                    },
                    "personalizations": [
                        {
                            "to": [{"email": item} for item in message.to_addresses],
                            "cc": [{"email": item} for item in message.cc_addresses],
                            "bcc": [{"email": item} for item in message.bcc_addresses],
                        }
                    ],
                    "reply_to": {"email": message.reply_to} if message.reply_to else None,
                    "subject": message.subject,
                    "content": [
                        {"type": "text/plain", "value": message.text_body},
                        *([{"type": "text/html", "value": message.html_body}] if message.html_body else []),
                    ],
                },
                timeout=20,
            )
        except httpx.TransportError as exc:
            raise EmailDeliveryError(str(exc) or "SendGrid transport error", retriable=True, provider="sendgrid") from exc
        if response.status_code >= 400:
            raise _classify_http_error(provider="sendgrid", response=response)
        provider_message_id = response.headers.get("X-Message-Id")
        return EmailDeliveryResult(provider="sendgrid", provider_message_id=provider_message_id)
