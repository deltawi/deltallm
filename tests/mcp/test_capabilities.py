from __future__ import annotations

import pytest

from src.mcp.capabilities import extract_tool_schemas, namespace_tool_name, namespace_tools, parse_namespaced_tool_name


def test_extract_tool_schemas_ignores_invalid_entries() -> None:
    tools = extract_tool_schemas(
        {
            "tools": [
                {"name": "search", "description": "Search", "inputSchema": {"type": "object"}},
                {"description": "missing name"},
                "bad-entry",
            ]
        }
    )
    assert len(tools) == 1
    assert tools[0].name == "search"
    assert tools[0].input_schema == {"type": "object"}


def test_namespace_tools_preserves_original_identity() -> None:
    tools = extract_tool_schemas({"tools": [{"name": "search", "inputSchema": {"type": "object"}}]})
    namespaced = namespace_tools("github", tools)
    assert namespaced[0].namespaced_name == "github.search"
    assert namespaced[0].original_name == "search"
    assert namespaced[0].server_key == "github"


def test_parse_namespaced_tool_name_requires_prefix() -> None:
    assert namespace_tool_name("github", "search") == "github.search"
    assert parse_namespaced_tool_name("github.search") == ("github", "search")
    with pytest.raises(Exception):
        parse_namespaced_tool_name("search")
