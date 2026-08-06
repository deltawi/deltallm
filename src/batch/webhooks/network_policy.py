from __future__ import annotations

import asyncio
import socket
from collections.abc import Awaitable, Callable, Collection, Sequence
from dataclasses import dataclass, field
from ipaddress import IPv4Address, IPv6Address, ip_address, ip_network
from typing import TypeAlias
from urllib.parse import urlsplit, urlunsplit

from src.batch.webhooks.models import BatchWebhookRequest


IPAddress: TypeAlias = IPv4Address | IPv6Address
Resolver: TypeAlias = Callable[[str, int], Awaitable[Sequence[str]]]

# Metadata services must remain unreachable even when an operator configures a
# broad private CIDR for development traffic.
_HARD_DENIED_METADATA_ADDRESSES = frozenset(
    {
        ip_address("169.254.169.254"),
        ip_address("169.254.170.2"),
        ip_address("169.254.170.23"),
        ip_address("100.100.100.200"),
        ip_address("fd00:ec2::254"),
        ip_address("fd00:ec2::23"),
    }
)


class BatchWebhookNetworkPolicyError(ValueError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class BatchWebhookResolutionError(OSError):
    def __init__(self, reason: str = "dns_resolution_failed") -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class ResolvedBatchWebhookTarget:
    connection_url: str = field(repr=False)
    host_header: str = field(repr=False)
    sni_hostname: str | None = field(repr=False)
    address: IPAddress = field(repr=False)


async def resolve_batch_webhook_hostname(hostname: str, port: int) -> tuple[str, ...]:
    try:
        results = await asyncio.get_running_loop().getaddrinfo(
            hostname,
            port,
            family=socket.AF_UNSPEC,
            type=socket.SOCK_STREAM,
            proto=socket.IPPROTO_TCP,
        )
    except (OSError, UnicodeError) as exc:
        raise BatchWebhookResolutionError() from exc

    addresses = {str(result[4][0]).split("%", 1)[0] for result in results if result[4]}
    if not addresses:
        raise BatchWebhookResolutionError("dns_resolution_empty")
    return tuple(addresses)


class BatchWebhookNetworkPolicy:
    def __init__(
        self,
        *,
        allow_http: bool = False,
        allowed_ports: Collection[int] = (443,),
        allowed_private_cidrs: Collection[str] = (),
        resolver: Resolver | None = None,
        resolution_timeout_seconds: float = 10.0,
    ) -> None:
        self.allow_http = bool(allow_http)
        self.allowed_ports = frozenset(int(port) for port in allowed_ports)
        self.allowed_private_networks = tuple(
            ip_network(str(cidr), strict=False) for cidr in allowed_private_cidrs
        )
        self.resolver = resolver or resolve_batch_webhook_hostname
        self.resolution_timeout_seconds = max(0.001, float(resolution_timeout_seconds))

    def _is_explicitly_allowed(self, address: IPAddress) -> bool:
        comparable = address.ipv4_mapped if isinstance(address, IPv6Address) else None
        candidates = (address,) if comparable is None else (address, comparable)
        return any(
            candidate.version == network.version and candidate in network
            for candidate in candidates
            for network in self.allowed_private_networks
        )

    def _address_is_allowed(self, address: IPAddress) -> bool:
        comparable = address.ipv4_mapped if isinstance(address, IPv6Address) else None
        effective = comparable or address
        if effective in _HARD_DENIED_METADATA_ADDRESSES:
            return False
        if (
            effective.is_unspecified
            or effective.is_multicast
            or effective.is_reserved
            or (isinstance(effective, IPv6Address) and effective.is_site_local)
        ):
            return False
        if effective.is_global:
            return True
        return self._is_explicitly_allowed(address)

    async def resolve(
        self,
        config: BatchWebhookRequest,
        *,
        attempt_count: int,
    ) -> ResolvedBatchWebhookTarget:
        parsed = urlsplit(config.url)
        scheme = parsed.scheme.lower()
        if scheme not in {"http", "https"}:
            raise BatchWebhookNetworkPolicyError("scheme_not_allowed")
        if scheme == "http" and not self.allow_http:
            raise BatchWebhookNetworkPolicyError("http_not_allowed")
        hostname = parsed.hostname
        if not hostname:
            raise BatchWebhookNetworkPolicyError("hostname_invalid")
        effective_port = parsed.port or (443 if scheme == "https" else 80)
        if effective_port not in self.allowed_ports:
            raise BatchWebhookNetworkPolicyError("port_not_allowed")

        try:
            literal = ip_address(hostname.split("%", 1)[0])
        except ValueError:
            try:
                async with asyncio.timeout(self.resolution_timeout_seconds):
                    raw_addresses = await self.resolver(hostname, effective_port)
            except TimeoutError:
                raise BatchWebhookResolutionError("dns_resolution_timeout") from None
        else:
            raw_addresses = (str(literal),)

        addresses: set[IPAddress] = set()
        try:
            for raw_address in raw_addresses:
                addresses.add(ip_address(str(raw_address).split("%", 1)[0]))
        except ValueError as exc:
            raise BatchWebhookResolutionError("dns_resolution_invalid") from exc
        if not addresses:
            raise BatchWebhookResolutionError("dns_resolution_empty")
        if not all(self._address_is_allowed(address) for address in addresses):
            raise BatchWebhookNetworkPolicyError("resolved_address_not_allowed")

        ordered = sorted(addresses, key=lambda value: (value.version, value.packed))
        selected = ordered[(max(1, int(attempt_count)) - 1) % len(ordered)]
        rendered_address = f"[{selected}]" if selected.version == 6 else str(selected)
        default_port = 443 if scheme == "https" else 80
        connection_host = (
            rendered_address
            if effective_port == default_port
            else f"{rendered_address}:{effective_port}"
        )
        connection_url = urlunsplit((scheme, connection_host, parsed.path or "/", parsed.query, ""))
        return ResolvedBatchWebhookTarget(
            connection_url=connection_url,
            host_header=parsed.netloc,
            sni_hostname=hostname if scheme == "https" else None,
            address=selected,
        )
