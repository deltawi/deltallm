from __future__ import annotations

from dataclasses import dataclass
from ipaddress import ip_address, ip_network

from src.auth.external_config import ExternalAuthSettings


@dataclass(frozen=True, slots=True)
class ExternalClientAddress:
    address: str
    forwarded: bool


class ExternalClientResolver:
    """Use forwarding data only from explicitly trusted direct peers."""

    def __init__(self, settings: ExternalAuthSettings) -> None:
        self.networks = tuple(ip_network(value) for value in settings.trusted_proxy_cidrs)
        self.origins = frozenset(settings.allowed_origins)

    def resolve(self, direct_peer: str, forwarded_for: str | None) -> ExternalClientAddress:
        peer = ip_address(direct_peer)
        if not any(peer in network for network in self.networks) or not forwarded_for:
            return ExternalClientAddress(str(peer), False)
        if len(forwarded_for) > 1024:
            raise ValueError("Forwarding chain exceeds its bound")
        chain = forwarded_for.split(",")
        if len(chain) > 16:
            raise ValueError("Forwarding chain exceeds its bound")
        addresses = [ip_address(value.strip()) for value in chain]
        for address in reversed(addresses):
            if not any(address in network for network in self.networks):
                return ExternalClientAddress(str(address), True)
        return ExternalClientAddress(str(addresses[0]), True)

    def accepts_origin(self, origin: str | None) -> bool:
        return origin is not None and origin in self.origins
