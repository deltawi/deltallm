from __future__ import annotations

import base64

from src.mcp.auth import build_forwarded_headers, build_server_headers


def test_build_server_headers_for_bearer_auth() -> None:
    headers = build_server_headers(auth_mode="bearer", auth_config={"token": "abc123"})
    assert headers == {"Authorization": "Bearer abc123"}


def test_build_server_headers_for_basic_auth() -> None:
    headers = build_server_headers(auth_mode="basic", auth_config={"username": "user", "password": "pass"})
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
