from __future__ import annotations

import base64

from src.mcp.auth import (
    build_effective_mcp_forwarded_headers,
    build_effective_mcp_upstream_headers,
    build_forwarded_headers,
    build_server_headers,
)


def test_build_server_headers_for_bearer_auth() -> None:
    headers = build_server_headers(auth_mode="bearer", auth_config={"token": "abc123"})
    assert headers == {"Authorization": "Bearer abc123"}


def test_build_server_headers_for_basic_auth() -> None:
    headers = build_server_headers(
        auth_mode="basic", auth_config={"username": "user", "password": "pass"}
    )
    expected = base64.b64encode(b"user:pass").decode("ascii")
    assert headers == {"Authorization": f"Basic {expected}"}


def test_build_forwarded_headers_filters_by_server_prefix_and_allowlist() -> None:
    headers = build_forwarded_headers(
        request_headers={
            "x-deltallm-mcp-github-authorization": "Bearer token",
            "x-deltallm-mcp-github-x-api-key": "secret",
            "x-deltallm-mcp-slack-authorization": "Bearer wrong",
        },
        server_key="github",
        allowlist=["authorization"],
    )
    assert headers == {"authorization": "Bearer token"}


def test_build_effective_mcp_forwarded_headers_drops_protected_headers() -> None:
    headers = build_effective_mcp_forwarded_headers(
        request_headers={
            "x-deltallm-mcp-github-accept": "application/json",
            "x-deltallm-mcp-github-authorization": "Bearer token",
            "x-deltallm-mcp-github-content-type": "text/plain",
            "x-deltallm-mcp-github-mcp-protocol-version": "1900-01-01",
            "x-deltallm-mcp-github-mcp-session-id": "bad-session",
            "x-deltallm-mcp-github-x-api-key": "secret",
        },
        server_key="github",
        allowlist=[
            "accept",
            "authorization",
            "content-type",
            "mcp-protocol-version",
            "mcp-session-id",
            "x-api-key",
        ],
    )

    assert headers == {"authorization": "Bearer token", "x-api-key": "secret"}


def test_build_effective_mcp_upstream_headers_keeps_auth_and_drops_protected_headers() -> None:
    headers = build_effective_mcp_upstream_headers(
        auth_mode="header_map",
        auth_config={
            "headers": {
                "Accept": "application/json",
                "Authorization": "Bearer upstream",
                "Content-Type": "text/plain",
                "MCP-Session-Id": "bad-session",
            }
        },
        request_headers={
            "x-deltallm-mcp-github-authorization": "Bearer forwarded",
            "x-deltallm-mcp-github-mcp-protocol-version": "1900-01-01",
        },
        server_key="github",
        allowlist=["authorization", "mcp-protocol-version"],
    )

    assert headers == {"Authorization": "Bearer upstream", "authorization": "Bearer forwarded"}
