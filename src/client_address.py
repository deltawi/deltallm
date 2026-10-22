"""Resolve the caller's IP address without trusting client-supplied forwarding data.

`X-Forwarded-For` is read only when the direct peer is a trusted proxy, and the chain is
walked from the right, so the address is the last hop that a trusted proxy saw. Entries a
client adds on the left are never used while an untrusted hop follows them.
"""

from __future__ import annotations

from functools import lru_cache
from ipaddress import IPv4Network, IPv6Network, ip_address, ip_network
from typing import Any

from starlette.requests import HTTPConnection

# Loopback and private ranges, where a reverse proxy or ingress normally runs.
DEFAULT_TRUSTED_PROXY_CIDRS = (
    "127.0.0.0/8",
    "::1/128",
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "fc00::/7",
)
_MAX_FORWARDED_FOR_BYTES = 1024
_MAX_FORWARDED_HOPS = 16


@lru_cache(maxsize=8)
def _trusted_networks(cidrs: tuple[str, ...]) -> tuple[IPv4Network | IPv6Network, ...]:
    return tuple(ip_network(cidr, strict=False) for cidr in cidrs)


def trusted_proxy_cidrs(general_settings: Any) -> tuple[str, ...]:
    configured = getattr(general_settings, "trusted_proxy_cidrs", None)
    if configured is None:
        return DEFAULT_TRUSTED_PROXY_CIDRS
    return tuple(configured)


def _is_trusted(value: str, networks: tuple[IPv4Network | IPv6Network, ...]) -> bool:
    try:
        address = ip_address(value.split("%", 1)[0])
    except ValueError:
        return False
    mapped = getattr(address, "ipv4_mapped", None)
    return any(address in network or (mapped is not None and mapped in network) for network in networks)


def resolve_client_address(
    direct_peer: str | None,
    forwarded_for: str | None,
    *,
    cidrs: tuple[str, ...] = DEFAULT_TRUSTED_PROXY_CIDRS,
) -> str | None:
    networks = _trusted_networks(cidrs)
    if not direct_peer:
        return None
    if not forwarded_for or not _is_trusted(direct_peer, networks):
        return direct_peer
    if len(forwarded_for) > _MAX_FORWARDED_FOR_BYTES:
        return direct_peer
    hops = [hop.strip() for hop in forwarded_for.split(",")]
    if len(hops) > _MAX_FORWARDED_HOPS:
        return direct_peer
    for hop in reversed(hops):
        if not hop:
            continue
        try:
            ip_address(hop.split("%", 1)[0])
        except ValueError:
            # A malformed hop ends the chain we can vouch for.
            return direct_peer
        if not _is_trusted(hop, networks):
            return hop
    # Every hop is a private or proxy address (an internal caller); the leftmost is the origin.
    first = next((hop for hop in hops if hop), None)
    return first or direct_peer


def request_client_ip(request: HTTPConnection) -> str | None:
    app_config = getattr(request.app.state, "app_config", None)
    general_settings = getattr(app_config, "general_settings", None)
    client = request.client
    return resolve_client_address(
        client.host if client is not None else None,
        request.headers.get("x-forwarded-for"),
        cidrs=trusted_proxy_cidrs(general_settings),
    )
