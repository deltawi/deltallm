from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import hmac
import json
from typing import Any, Mapping

from src.db.email_feedback import EmailFeedbackRepository, EmailWebhookEventRecord


class EmailFeedbackError(ValueError):
    pass


@dataclass(frozen=True)
class EmailFeedbackOutcome:
    provider: str
    event_type: str
    duplicate: bool
    suppressed_count: int
    recipient_addresses: tuple[str, ...]
    email_id: str | None = None


def _normalize_email_address(value: Any) -> str | None:
    normalized = str(value or "").strip().lower()
    if not normalized or "@" not in normalized:
        return None
    return normalized


def _as_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    if value is None:
        return None
    if isinstance(value, str):
        if not value.strip():
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
        except ValueError as exc:
            raise EmailFeedbackError("invalid resend webhook payload") from exc
    raise EmailFeedbackError("invalid resend webhook payload")
    return None


class EmailFeedbackService:
    def __init__(self, *, repository: EmailFeedbackRepository, config_getter) -> None:  # noqa: ANN001
        self.repository = repository
        self._config_getter = config_getter

    async def handle_resend_webhook(self, *, headers: Mapping[str, str], raw_body: bytes) -> EmailFeedbackOutcome:
        secret = self._resend_webhook_secret()
        if not secret:
            raise EmailFeedbackError("resend_webhook_signing_secret is not configured")
        signature_headers = self._extract_signature_headers(headers)
        body_text = self._decode_body(raw_body)
        self._verify_standard_webhook(secret=secret, body=body_text, headers=signature_headers)

        payload = self._parse_payload(body_text)
        event_type = self._parse_event_type(payload)
        if event_type not in {"email.bounced", "email.complained"}:
            return EmailFeedbackOutcome(
                provider="resend",
                event_type=event_type or "unknown",
                duplicate=False,
                suppressed_count=0,
                recipient_addresses=(),
            )

        webhook_event_id = str(signature_headers["id"])
        data = self._parse_actionable_data(payload)
        provider_message_id = str(data.get("email_id") or "").strip() or None
        raw_recipients = data.get("to", [])
        if not isinstance(raw_recipients, list):
            raise EmailFeedbackError("invalid resend webhook payload")
        recipient_addresses = tuple(
            sorted(
                address
                for address in {
                    _normalize_email_address(item)
                    for item in raw_recipients
                }
                if address is not None
            )
        )
        occurred_at = _as_datetime(payload.get("created_at"))
        email_id = await self.repository.resolve_email_id_by_provider_message_id(
            provider="resend",
            provider_message_id=provider_message_id,
        )
        event_created = await self.repository.create_webhook_event(
            EmailWebhookEventRecord(
                webhook_event_id=webhook_event_id,
                provider="resend",
                event_type=event_type,
                recipient_address=recipient_addresses[0] if recipient_addresses else None,
                provider_message_id=provider_message_id,
                email_id=email_id,
                payload_json=payload,
                occurred_at=occurred_at,
            )
        )
        if not event_created:
            return EmailFeedbackOutcome(
                provider="resend",
                event_type=event_type,
                duplicate=True,
                suppressed_count=0,
                recipient_addresses=recipient_addresses,
                email_id=email_id,
            )

        suppressed_count = 0
        reason = "bounce" if event_type == "email.bounced" else "complaint"
        for recipient in recipient_addresses:
            await self.repository.upsert_suppression(
                email_address=recipient,
                provider="resend",
                reason=reason,
                source="webhook",
                provider_message_id=provider_message_id,
                webhook_event_id=webhook_event_id,
                metadata=payload,
            )
            suppressed_count += 1

        return EmailFeedbackOutcome(
            provider="resend",
            event_type=event_type,
            duplicate=False,
            suppressed_count=suppressed_count,
            recipient_addresses=recipient_addresses,
            email_id=email_id,
        )

    def _decode_body(self, raw_body: bytes) -> str:
        try:
            return raw_body.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise EmailFeedbackError("invalid resend webhook payload") from exc

    def _parse_payload(self, body_text: str) -> dict[str, Any]:
        try:
            payload = json.loads(body_text)
        except json.JSONDecodeError as exc:
            raise EmailFeedbackError("invalid resend webhook payload") from exc
        if not isinstance(payload, dict):
            raise EmailFeedbackError("invalid resend webhook payload")
        return payload

    def _parse_event_type(self, payload: Mapping[str, Any]) -> str:
        value = payload.get("type")
        if value is None:
            return ""
        if not isinstance(value, str):
            raise EmailFeedbackError("invalid resend webhook payload")
        return value.strip()

    def _parse_actionable_data(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        data = payload.get("data")
        if not isinstance(data, dict):
            raise EmailFeedbackError("invalid resend webhook payload")
        return data

    def _resend_webhook_secret(self) -> str | None:
        cfg = self._config_getter()
        general = getattr(cfg, "general_settings", None)
        secret = str(getattr(general, "resend_webhook_signing_secret", "") or "").strip()
        return secret or None

    def _webhook_tolerance_seconds(self) -> int:
        cfg = self._config_getter()
        general = getattr(cfg, "general_settings", None)
        return int(getattr(general, "resend_webhook_tolerance_seconds", 300) or 300)

    def _extract_signature_headers(self, headers: Mapping[str, str]) -> dict[str, str]:
        lowered = {str(key).lower(): str(value) for key, value in headers.items()}
        webhook_id = lowered.get("svix-id") or lowered.get("webhook-id")
        webhook_timestamp = lowered.get("svix-timestamp") or lowered.get("webhook-timestamp")
        webhook_signature = lowered.get("svix-signature") or lowered.get("webhook-signature")
        if not webhook_id or not webhook_timestamp or not webhook_signature:
            raise EmailFeedbackError("missing webhook signature headers")
        return {
            "id": webhook_id,
            "timestamp": webhook_timestamp,
            "signature": webhook_signature,
        }

    def _verify_standard_webhook(self, *, secret: str, body: str, headers: Mapping[str, str]) -> None:
        secret_value = secret
        if secret_value.startswith("whsec_"):
            secret_value = secret_value[len("whsec_"):]
        signed_content = f"{headers['id']}.{headers['timestamp']}.{body}".encode("utf-8")
        expected = base64.b64encode(
            hmac.new(secret_value.encode("utf-8"), signed_content, hashlib.sha256).digest()
        ).decode("utf-8")

        tolerance_seconds = self._webhook_tolerance_seconds()
        try:
            timestamp = int(headers["timestamp"])
        except (TypeError, ValueError) as exc:
            raise EmailFeedbackError("invalid webhook timestamp") from exc
        now = int(datetime.now(tz=UTC).timestamp())
        if abs(now - timestamp) > tolerance_seconds:
            raise EmailFeedbackError("webhook timestamp is outside the allowed tolerance")

        signatures = {
            part.split(",", 1)[1]
            for part in headers["signature"].split()
            if part.startswith("v1,") and "," in part
        }
        if expected not in signatures:
            raise EmailFeedbackError("webhook signature verification failed")
