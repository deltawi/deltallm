from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class MCPServerConfig:
    server_id: str
    server_key: str
    name: str
    transport: str
    base_url: str
    auth_mode: str = "none"
    auth_config: dict[str, Any] = field(default_factory=dict)
    forwarded_headers_allowlist: list[str] = field(default_factory=list)
    request_timeout_ms: int = 30000


@dataclass(frozen=True)
class MCPToolSchema:
    name: str
    description: str | None = None
    input_schema: dict[str, Any] = field(default_factory=dict)
    annotations: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class NamespacedTool:
    server_key: str
    original_name: str
    namespaced_name: str
    description: str | None = None
    input_schema: dict[str, Any] = field(default_factory=dict)
    scope_type: str | None = None
    scope_id: str | None = None


@dataclass(frozen=True)
class MCPToolCallResult:
    content: list[dict[str, Any]] = field(default_factory=list)
    structured_content: dict[str, Any] | None = None
    is_error: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MCPBindingResolution:
    server_id: str
    server_key: str
    scope_type: str
    scope_id: str
    allowed_tool_names: tuple[str, ...] | None = None


@dataclass(frozen=True)
class MCPRequestEnvelope:
    method: str
    params: dict[str, Any] = field(default_factory=dict)
    request_id: str | int | None = None
