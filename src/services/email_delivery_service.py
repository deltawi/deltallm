from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Callable, Iterable

import httpx

from src.email.config import normalize_email_base_url
from src.email.models import EmailConfigurationError, EmailDeliveryResult, EmailProviderName, PreparedEmail
from src.email.providers import ResendEmailProvider, SMTPEmailProvider, SendGridEmailProvider
from src.email.rendering import render_email_template


class EmailDeliveryService:
    def __init__(self, *, config_getter: Callable[[], Any], http_client: httpx.AsyncClient) -> None:
        self._config_getter = config_getter
        self._http_client = http_client

    def validate_current_config(self, *, provider_override: str | None = None) -> None:
        provider = self._resolve_provider(provider_override=provider_override)
        self._from_address()
        self._base_url()
        self._provider_settings(provider)

    def prepare_template_email(
        self,
        *,
        template_key: str,
        to_addresses: Iterable[str],
        payload_json: dict[str, Any] | None = None,
        kind: str = "transactional",
        provider_override: str | None = None,
        cc_addresses: Iterable[str] | None = None,
        bcc_addresses: Iterable[str] | None = None,
    ) -> PreparedEmail:
        provider = self._resolve_provider(provider_override=provider_override)
        general = self._general_settings()
        rendered = render_email_template(template_key, payload_json)
        return PreparedEmail(
            kind=kind,  # type: ignore[arg-type]
            provider=provider,
            to_addresses=self._normalize_addresses(to_addresses, required=True),
            cc_addresses=self._normalize_addresses(cc_addresses or (), required=False),
            bcc_addresses=self._normalize_addresses(bcc_addresses or (), required=False),
            from_address=self._from_address(),
            reply_to=str(getattr(general, "email_reply_to", "") or "") or None,
            template_key=rendered.template_key,
            payload_json=dict(payload_json or {}),
            subject=rendered.subject,
            text_body=rendered.text_body,
            html_body=rendered.html_body,
        )

    async def send_prepared_email(self, message: PreparedEmail) -> EmailDeliveryResult:
        provider = self._build_provider(message.provider)
        return await provider.send(message)

    async def send_test_email(self, *, to_address: str, provider_override: str | None = None) -> EmailDeliveryResult:
        provider = self._resolve_provider(provider_override=provider_override)
        general = self._general_settings()
        prepared = self.prepare_template_email(
            template_key="test_email",
            to_addresses=(to_address,),
            payload_json={
                "instance_name": str(getattr(general, "instance_name", "DeltaLLM") or "DeltaLLM"),
                "provider": provider,
                "sent_at": datetime.now(tz=UTC).isoformat(),
            },
            kind="test",
            provider_override=provider,
        )
        return await self.send_prepared_email(prepared)

    def _general_settings(self) -> Any:
        app_config = self._config_getter()
        general = getattr(app_config, "general_settings", None)
        if general is None:
            raise EmailConfigurationError("email configuration unavailable")
        return general

    def _resolve_provider(self, *, provider_override: str | None = None) -> EmailProviderName:
        general = self._general_settings()
        if not bool(getattr(general, "email_enabled", False)):
            raise EmailConfigurationError("email is disabled")
        provider = str(provider_override or getattr(general, "email_provider", "") or "").strip().lower()
        if provider not in {"smtp", "resend", "sendgrid"}:
            raise EmailConfigurationError("email_provider must be one of: smtp, resend, sendgrid")
        return provider  # type: ignore[return-value]

    def _from_address(self) -> str:
        general = self._general_settings()
        from_address = str(getattr(general, "email_from_address", "") or "").strip()
        if not from_address or "@" not in from_address:
            raise EmailConfigurationError("email_from_address is required when email is enabled")
        from_name = str(getattr(general, "email_from_name", "") or "").strip()
        return f"{from_name} <{from_address}>" if from_name else from_address

    def _normalize_addresses(self, items: Iterable[str], *, required: bool) -> tuple[str, ...]:
        normalized: list[str] = []
        for item in items:
            value = str(item or "").strip()
            if value:
                normalized.append(value)
        if required and not normalized:
            raise EmailConfigurationError("at least one recipient is required")
        return tuple(normalized)

    def _base_url(self) -> str:
        general = self._general_settings()
        return normalize_email_base_url(getattr(general, "email_base_url", None))

    def _provider_settings(self, provider: EmailProviderName) -> dict[str, Any]:
        general = self._general_settings()
        if provider == "smtp":
            host = str(getattr(general, "smtp_host", "") or "").strip()
            if not host:
                raise EmailConfigurationError("smtp_host is required when email_provider=smtp")
            return {
                "host": host,
                "port": int(getattr(general, "smtp_port", 587) or 587),
                "username": getattr(general, "smtp_username", None),
                "password": getattr(general, "smtp_password", None),
                "use_tls": bool(getattr(general, "smtp_use_tls", False)),
                "use_starttls": bool(getattr(general, "smtp_use_starttls", True)),
            }
        if provider == "resend":
            api_key = str(getattr(general, "resend_api_key", "") or "").strip()
            if not api_key:
                raise EmailConfigurationError("resend_api_key is required when email_provider=resend")
            return {"api_key": api_key}
        api_key = str(getattr(general, "sendgrid_api_key", "") or "").strip()
        if not api_key:
            raise EmailConfigurationError("sendgrid_api_key is required when email_provider=sendgrid")
        return {"api_key": api_key}

    def _build_provider(self, provider: EmailProviderName):
        config = self._provider_settings(provider)
        if provider == "smtp":
            return SMTPEmailProvider(config)
        if provider == "resend":
            return ResendEmailProvider(self._http_client, config)
        return SendGridEmailProvider(self._http_client, config)
