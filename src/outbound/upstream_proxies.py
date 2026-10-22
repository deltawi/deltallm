"""Operator-defined outbound proxies that model deployments select by name."""

from __future__ import annotations

from collections.abc import Mapping
import re

import httpx
from pydantic import SecretStr

from src.outbound.network_policy import is_metadata_host

MAX_UPSTREAM_PROXIES = 16
UPSTREAM_PROXY_SCHEMES = frozenset({"http", "https", "socks5", "socks5h"})
_PROXY_NAME = re.compile(r"^[a-z0-9][a-z0-9_\-]{0,62}$")


def normalize_upstream_proxies(value: object) -> dict[str, SecretStr]:
    """Validate names and URLs without ever putting a URL in an error message."""
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError("upstream_proxies must map proxy names to URLs")
    if len(value) > MAX_UPSTREAM_PROXIES:
        raise ValueError(f"upstream_proxies allows at most {MAX_UPSTREAM_PROXIES} proxies")
    proxies: dict[str, SecretStr] = {}
    for raw_name, raw_url in value.items():
        name = str(raw_name).strip()
        if not _PROXY_NAME.fullmatch(name):
            raise ValueError(
                "upstream_proxies names use lowercase letters, digits, '-' and '_'"
            )
        url = raw_url.get_secret_value() if isinstance(raw_url, SecretStr) else raw_url
        if not isinstance(url, str) or not url.strip():
            raise ValueError(f"upstream_proxies.{name} must be a proxy URL")
        try:
            parsed = httpx.URL(url.strip())
        except Exception:
            raise ValueError(f"upstream_proxies.{name} is not a valid URL") from None
        if parsed.scheme not in UPSTREAM_PROXY_SCHEMES:
            raise ValueError(
                f"upstream_proxies.{name} must use http, https, socks5 or socks5h"
            )
        if not parsed.host:
            raise ValueError(f"upstream_proxies.{name} must name a host")
        if is_metadata_host(parsed.host):
            raise ValueError(f"upstream_proxies.{name} cannot point at a metadata address")
        proxies[name] = SecretStr(url.strip())
    return proxies


def upstream_proxy_urls(proxies: Mapping[str, SecretStr] | None) -> dict[str, str]:
    return {name: url.get_secret_value() for name, url in (proxies or {}).items()}
