from __future__ import annotations

from typing import Any

from .exceptions import MCPToolNotFoundError
from .models import MCPToolSchema, NamespacedTool


def namespace_tool_name(server_key: str, tool_name: str) -> str:
    return f"{server_key.strip()}.{tool_name.strip()}"


def parse_namespaced_tool_name(value: str) -> tuple[str, str]:
    server_key, separator, tool_name = str(value or "").partition(".")
    if not separator or not server_key.strip() or not tool_name.strip():
        raise MCPToolNotFoundError(f"Invalid MCP tool name '{value}'")
    return server_key.strip(), tool_name.strip()


def extract_tool_schemas(capabilities_json: dict[str, Any] | None) -> list[MCPToolSchema]:
    if not isinstance(capabilities_json, dict):
        return []
    tools = capabilities_json.get("tools")
    if not isinstance(tools, list):
        return []
    out: list[MCPToolSchema] = []
    for item in tools:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        input_schema = item.get("inputSchema")
        out.append(
            MCPToolSchema(
                name=name,
                description=str(item.get("description")) if item.get("description") is not None else None,
                input_schema=input_schema if isinstance(input_schema, dict) else {},
                annotations=item.get("annotations") if isinstance(item.get("annotations"), dict) else {},
            )
        )
    return out


def namespace_tools(server_key: str, tools: list[MCPToolSchema]) -> list[NamespacedTool]:
    return [
        NamespacedTool(
            server_key=server_key,
            original_name=tool.name,
            namespaced_name=namespace_tool_name(server_key, tool.name),
            description=tool.description,
            input_schema=dict(tool.input_schema),
        )
        for tool in tools
    ]
