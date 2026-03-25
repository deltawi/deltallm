from __future__ import annotations

from datetime import UTC, datetime
from html import escape
from typing import Any

from src.email.models import RenderedEmailTemplate


def render_email_template(template_key: str, payload: dict[str, Any] | None = None) -> RenderedEmailTemplate:
    normalized = str(template_key or "").strip()
    context = dict(payload or {})

    if normalized == "invite_user":
        accept_url = str(context.get("accept_url") or "")
        instance_name = str(context.get("instance_name") or "DeltaLLM")
        inviter_email = str(context.get("inviter_email") or "an administrator")
        scope_summary = str(context.get("scope_summary") or "your assigned organization and team access")
        subject = f"You're invited to {instance_name}"
        text_body = (
            f"You were invited to {instance_name} by {inviter_email}.\n\n"
            f"Access granted: {scope_summary}\n\n"
            f"Accept invite: {accept_url}\n"
        )
        html_body = (
            f"<p>You were invited to <strong>{escape(instance_name)}</strong> by {escape(inviter_email)}.</p>"
            f"<p>Access granted: {escape(scope_summary)}</p>"
            f"<p><a href=\"{escape(accept_url)}\">Accept invite</a></p>"
        )
        return RenderedEmailTemplate(template_key=normalized, subject=subject, text_body=text_body, html_body=html_body)

    if normalized == "reset_password":
        reset_url = str(context.get("reset_url") or "")
        instance_name = str(context.get("instance_name") or "DeltaLLM")
        subject = f"{instance_name} password reset"
        text_body = f"A password reset was requested for your {instance_name} account.\n\nReset password: {reset_url}\n"
        html_body = (
            f"<p>A password reset was requested for your <strong>{escape(instance_name)}</strong> account.</p>"
            f"<p><a href=\"{escape(reset_url)}\">Reset password</a></p>"
        )
        return RenderedEmailTemplate(template_key=normalized, subject=subject, text_body=text_body, html_body=html_body)

    if normalized == "budget_threshold":
        instance_name = str(context.get("instance_name") or "DeltaLLM")
        entity_type = str(context.get("entity_type") or "scope")
        entity_id = str(context.get("entity_id") or "unknown")
        current_spend = context.get("current_spend")
        soft_budget = context.get("soft_budget")
        hard_budget = context.get("hard_budget")
        subject = f"{instance_name} budget alert: {entity_type} {entity_id}"
        text_body = (
            f"Budget threshold reached in {instance_name}.\n\n"
            f"Entity type: {entity_type}\n"
            f"Entity id: {entity_id}\n"
            f"Current spend: {current_spend}\n"
            f"Soft budget: {soft_budget}\n"
            f"Hard budget: {hard_budget}\n"
        )
        html_body = (
            f"<p>Budget threshold reached in <strong>{escape(instance_name)}</strong>.</p>"
            f"<ul>"
            f"<li>Entity type: {escape(entity_type)}</li>"
            f"<li>Entity id: {escape(entity_id)}</li>"
            f"<li>Current spend: {escape(str(current_spend))}</li>"
            f"<li>Soft budget: {escape(str(soft_budget))}</li>"
            f"<li>Hard budget: {escape(str(hard_budget))}</li>"
            f"</ul>"
        )
        return RenderedEmailTemplate(template_key=normalized, subject=subject, text_body=text_body, html_body=html_body)

    if normalized == "api_key_lifecycle":
        instance_name = str(context.get("instance_name") or "DeltaLLM")
        event_label = str(context.get("event_label") or "updated")
        key_name = str(context.get("key_name") or "unnamed key")
        team_name = str(context.get("team_name") or "unknown team")
        actor_email = str(context.get("actor_email") or "an administrator")
        subject = f"{instance_name} API key {event_label}: {key_name}"
        text_body = (
            f"An API key in {instance_name} was {event_label}.\n\n"
            f"Key name: {key_name}\n"
            f"Team: {team_name}\n"
            f"Actor: {actor_email}\n"
        )
        html_body = (
            f"<p>An API key in <strong>{escape(instance_name)}</strong> was {escape(event_label)}.</p>"
            f"<ul>"
            f"<li>Key name: {escape(key_name)}</li>"
            f"<li>Team: {escape(team_name)}</li>"
            f"<li>Actor: {escape(actor_email)}</li>"
            f"</ul>"
        )
        return RenderedEmailTemplate(template_key=normalized, subject=subject, text_body=text_body, html_body=html_body)

    if normalized == "test_email":
        instance_name = str(context.get("instance_name") or "DeltaLLM")
        provider = str(context.get("provider") or "unknown")
        sent_at = str(context.get("sent_at") or datetime.now(tz=UTC).isoformat())
        subject = f"{instance_name} email delivery test"
        text_body = (
            f"This is a test email from {instance_name}.\n\n"
            f"Provider: {provider}\n"
            f"Sent at: {sent_at}\n"
        )
        html_body = (
            f"<p>This is a test email from <strong>{escape(instance_name)}</strong>.</p>"
            f"<ul><li>Provider: {escape(provider)}</li><li>Sent at: {escape(sent_at)}</li></ul>"
        )
        return RenderedEmailTemplate(template_key=normalized, subject=subject, text_body=text_body, html_body=html_body)

    raise ValueError(f"Unknown email template '{template_key}'")
