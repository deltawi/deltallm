from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Protocol


@dataclass(frozen=True, slots=True)
class PromptRenderEvent:
    prompt_render_log_id: str
    status: str
    audit_event_id: str | None = None
    request_id: str | None = None
    api_key: str | None = None
    user_id: str | None = None
    team_id: str | None = None
    organization_id: str | None = None
    route_group_key: str | None = None
    model: str | None = None
    prompt_template_id: str | None = None
    prompt_version_id: str | None = None
    prompt_key: str | None = None
    label: str | None = None
    latency_ms: int | None = None
    error_code: str | None = None
    error_message: str | None = None
    ip: str | None = None
    user_agent: str | None = None
    variables: dict[str, object] | None = None
    metadata: dict[str, object] | None = None
    variables_redacted: bool = False

    def redacted(self) -> PromptRenderEvent:
        return replace(
            self,
            variables=None,
            error_message=None,
            variables_redacted=bool(self.variables),
        )

    def persistence_payload(self) -> dict[str, object]:
        return {
            "prompt_render_log_id": self.prompt_render_log_id,
            "audit_event_id": self.audit_event_id,
            "request_id": self.request_id,
            "api_key": self.api_key,
            "user_id": self.user_id,
            "team_id": self.team_id,
            "organization_id": self.organization_id,
            "route_group_key": self.route_group_key,
            "model": self.model,
            "prompt_template_id": self.prompt_template_id,
            "prompt_version_id": self.prompt_version_id,
            "prompt_key": self.prompt_key,
            "label": self.label,
            "status": self.status,
            "latency_ms": self.latency_ms,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "ip": self.ip,
            "user_agent": self.user_agent,
            "variables": self.variables,
            "metadata": self.metadata,
            "variables_redacted": self.variables_redacted,
        }

    def render_log_payload(self) -> dict[str, object]:
        payload = self.persistence_payload()
        for field_name in ("audit_event_id", "ip", "user_agent"):
            payload.pop(field_name, None)
        return payload

    @classmethod
    def from_persistence_payload(
        cls,
        payload: Mapping[str, object],
    ) -> PromptRenderEvent:
        event_id = _required_string(payload, "prompt_render_log_id")
        status = _required_string(payload, "status")
        latency = payload.get("latency_ms")
        return cls(
            prompt_render_log_id=event_id,
            status=status,
            audit_event_id=_optional_string(payload.get("audit_event_id")),
            request_id=_optional_string(payload.get("request_id")),
            api_key=_optional_string(payload.get("api_key")),
            user_id=_optional_string(payload.get("user_id")),
            team_id=_optional_string(payload.get("team_id")),
            organization_id=_optional_string(payload.get("organization_id")),
            route_group_key=_optional_string(payload.get("route_group_key")),
            model=_optional_string(payload.get("model")),
            prompt_template_id=_optional_string(payload.get("prompt_template_id")),
            prompt_version_id=_optional_string(payload.get("prompt_version_id")),
            prompt_key=_optional_string(payload.get("prompt_key")),
            label=_optional_string(payload.get("label")),
            latency_ms=int(latency) if isinstance(latency, int | float) else None,
            error_code=_optional_string(payload.get("error_code")),
            error_message=_optional_string(payload.get("error_message")),
            ip=_optional_string(payload.get("ip")),
            user_agent=_optional_string(payload.get("user_agent")),
            variables=_optional_mapping(payload.get("variables")),
            metadata=_optional_mapping(payload.get("metadata")),
            variables_redacted=bool(payload.get("variables_redacted", False)),
        )


class PromptRenderSink(Protocol):
    async def enqueue_prompt_render(self, event: PromptRenderEvent) -> str | None: ...


def _required_string(payload: Mapping[str, object], field_name: str) -> str:
    value = _optional_string(payload.get(field_name))
    if value is None:
        raise ValueError(f"prompt render event is missing {field_name}")
    return value


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _optional_mapping(value: object) -> dict[str, object] | None:
    if not isinstance(value, Mapping):
        return None
    return {str(key): item for key, item in value.items()}
