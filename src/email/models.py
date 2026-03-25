from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

EmailKind = Literal["transactional", "notification", "test"]
EmailProviderName = Literal["smtp", "resend", "sendgrid"]


@dataclass(frozen=True)
class RenderedEmailTemplate:
    template_key: str
    subject: str
    text_body: str
    html_body: str | None = None


@dataclass(frozen=True)
class PreparedEmail:
    kind: EmailKind
    provider: EmailProviderName
    to_addresses: tuple[str, ...]
    from_address: str
    subject: str
    text_body: str
    cc_addresses: tuple[str, ...] = ()
    bcc_addresses: tuple[str, ...] = ()
    reply_to: str | None = None
    template_key: str | None = None
    payload_json: dict[str, Any] | None = None
    html_body: str | None = None


@dataclass(frozen=True)
class EmailDeliveryResult:
    provider: EmailProviderName
    provider_message_id: str | None = None


class EmailConfigurationError(ValueError):
    pass


class EmailDeliveryError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        retriable: bool,
        provider: EmailProviderName | None = None,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.retriable = retriable
        self.provider = provider
        self.status_code = status_code
